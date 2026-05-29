#!/usr/bin/env bash
# Wire Claude Desktop to one (or all) board's remote MCP server.
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
# Usage:
#   ./scripts/connect-claude-desktop.sh --board <boardId>   # one board
#   ./scripts/connect-claude-desktop.sh --all               # every board with a connection block
# After it runs, fully restart Claude Desktop to pick up the new token(s).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CA_BUNDLE="$HOME/node-ca-bundle.pem"
BOARD=""
ALL=false

usage() { echo "Usage: $0 --board <boardId> | --all"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD="${2:-}"; shift 2 ;;
    --all) ALL=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done
if ! $ALL && [[ -z "$BOARD" ]]; then
  echo "ERROR: pass --board <id> or --all" >&2; usage; exit 1
fi

# Read a single KEY=value from a board's env file (strips inline comments/ws).
read_env() {
  grep -E "^$2=" "$REPO_ROOT/.env.$1" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//' | xargs
}

# Resolve an absolute Node bin dir (>=18) once — baked into the GUI config.
NODE_BIN="$(dirname "$(command -v node)")"
NODE_MAJOR="$("$NODE_BIN/node" -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 18 ]] || { echo "ERROR: node $($NODE_BIN/node -v) too old (need >=18). Run 'nvm use 20' first." >&2; exit 1; }

# Ensure the CA bundle exists (system + corporate roots) for Node TLS.
if [[ ! -f "$CA_BUNDLE" ]]; then
  echo "  building CA bundle ($CA_BUNDLE)..."
  security find-certificate -a -p /Library/Keychains/System.keychain > "$CA_BUNDLE" 2>/dev/null || true
  security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> "$CA_BUNDLE" 2>/dev/null || true
fi

# Mint a token for one board and write its mcpServers entry. Returns non-zero on
# a board-level problem (missing block, mint failure) without aborting --all.
connect_board() {
  local board="$1"
  [[ -f "$REPO_ROOT/.env.$board" ]] || { echo "  SKIP $board: .env.$board not found"; return 1; }
  local url client email password
  url="$(read_env "$board" MCP_REMOTE_URL)"
  client="$(read_env "$board" MCP_COGNITO_CLIENT_ID)"
  email="$(read_env "$board" MCP_DEMO_EMAIL)"
  password="$(read_env "$board" MCP_DEMO_PASSWORD)"
  if [[ -z "$url" || -z "$client" || -z "$email" || -z "$password" ]]; then
    echo "  SKIP $board: no MCP_* connection block in .env.$board"; return 1
  fi
  echo "  $board: minting token for $email ..."
  local token
  token="$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
    --client-id "$client" --auth-parameters USERNAME="$email",PASSWORD="$password" \
    --query 'AuthenticationResult.AccessToken' --output text 2>/dev/null)" || true
  if [[ -z "$token" || "$token" == "None" ]]; then
    echo "  ERROR $board: token mint failed (check the board's Cognito pool/user)"; return 1
  fi
  BOARD="$board" URL="$url" TOKEN="$token" NODE_BIN="$NODE_BIN" CA_BUNDLE="$CA_BUNDLE" CFG="$CFG" \
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
print(f"  ✓ wrote mcpServers.{os.environ['BOARD']}")
PY
}

# Back up the config once before touching it.
[[ -f "$CFG" ]] && cp "$CFG" "$CFG.bak.$(date +%Y%m%d-%H%M%S)"

if $ALL; then
  shopt -s nullglob
  count=0
  for f in "$REPO_ROOT"/.env.*; do
    b="${f##*/.env.}"
    [[ "$b" == "example" ]] && continue
    grep -qE "^MCP_REMOTE_URL=" "$f" || continue   # only boards with a connection block
    connect_board "$b" || true
    count=$((count + 1))
  done
  [[ "$count" -eq 0 ]] && { echo "No boards with an MCP_* connection block found." >&2; exit 1; }
else
  connect_board "$BOARD"
fi

echo "✓ Done. Fully restart Claude Desktop to apply."
