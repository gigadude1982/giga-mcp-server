#!/usr/bin/env bash
# Wire Claude Desktop to a board's remote MCP server.
#
# Mints a fresh 24h Cognito access token for the board's demo user and writes a
# matching `mcpServers.<board>` entry into the Claude Desktop config. The entry
# uses mcp-remote to bridge the remote streamable-http endpoint to stdio with an
# `Authorization: Bearer <token>` header, pins an absolute Node path (GUI apps
# don't inherit your nvm PATH), and trusts the corporate CA bundle so TLS
# verifies even behind a Zscaler-style inspection proxy.
#
# All board-specific values (URL, Cognito client, demo creds) are read from the
# gitignored `.env.<board>` — this script contains NO secrets.
#
# Usage: ./scripts/connect-claude-desktop.sh --board <boardId>
# After it runs, fully restart Claude Desktop to pick up the new token.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CA_BUNDLE="$HOME/node-ca-bundle.pem"
BOARD=""

usage() { echo "Usage: $0 --board <boardId>"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done
[[ -z "$BOARD" ]] && { echo "ERROR: --board is required" >&2; usage; exit 1; }

ENV_FILE="$REPO_ROOT/.env.$BOARD"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: $ENV_FILE not found" >&2; exit 1; }

# Read a single KEY=value from the env file (strips inline comments/whitespace).
read_env() {
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | xargs
}

URL="$(read_env MCP_REMOTE_URL)"
CLIENT="$(read_env MCP_COGNITO_CLIENT_ID)"
EMAIL="$(read_env MCP_DEMO_EMAIL)"
PASSWORD="$(read_env MCP_DEMO_PASSWORD)"
for v in URL CLIENT EMAIL PASSWORD; do
  [[ -z "${!v}" ]] && { echo "ERROR: MCP_* connection keys missing in $ENV_FILE (need MCP_REMOTE_URL/COGNITO_CLIENT_ID/DEMO_EMAIL/DEMO_PASSWORD)" >&2; exit 1; }
done

# Resolve an absolute Node bin dir (>=18) to bake into the GUI config.
NODE_BIN="$(dirname "$(command -v node)")"
NODE_MAJOR="$("$NODE_BIN/node" -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 18 ]] || { echo "ERROR: node $($NODE_BIN/node -v) too old (need >=18). Run 'nvm use 20' first." >&2; exit 1; }

# Ensure the CA bundle exists (system + corporate roots) for Node TLS.
if [[ ! -f "$CA_BUNDLE" ]]; then
  echo "  building CA bundle ($CA_BUNDLE)..."
  security find-certificate -a -p /Library/Keychains/System.keychain > "$CA_BUNDLE" 2>/dev/null || true
  security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> "$CA_BUNDLE" 2>/dev/null || true
fi

echo "  minting token for $EMAIL ..."
TOKEN="$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
  --client-id "$CLIENT" --auth-parameters USERNAME="$EMAIL",PASSWORD="$PASSWORD" \
  --query 'AuthenticationResult.AccessToken' --output text)"
[[ -n "$TOKEN" && "$TOKEN" != "None" ]] || { echo "ERROR: failed to mint token (check the board's Cognito pool/user exist)" >&2; exit 1; }

[[ -f "$CFG" ]] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d-%H%M%S)"

BOARD="$BOARD" URL="$URL" TOKEN="$TOKEN" NODE_BIN="$NODE_BIN" CA_BUNDLE="$CA_BUNDLE" CFG="$CFG" \
python3 <<'PY'
import json, os
cfg_path = os.environ["CFG"]
try:
    with open(cfg_path) as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = {}
node_bin = os.environ["NODE_BIN"]
cfg.setdefault("mcpServers", {})[os.environ["BOARD"]] = {
    "command": f"{node_bin}/npx",
    "args": ["-y", "mcp-remote", os.environ["URL"],
             "--header", "Authorization:Bearer ${MCP_TOKEN}"],
    "env": {
        "PATH": f"{node_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "MCP_TOKEN": os.environ["TOKEN"],
        "NODE_EXTRA_CA_CERTS": os.environ["CA_BUNDLE"],
    },
}
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"  wrote mcpServers.{os.environ['BOARD']}")
PY

echo "✓ '$BOARD' connected (token valid 24h). Fully restart Claude Desktop to apply."
