#!/usr/bin/env bash
# Creates or updates SSM SecureString parameters for all boards.
# Reads secrets from .env.<boardId> files in the repo root.
# Usage: ./scripts/setup-ssm.sh [--board gigacorp|pitchvault]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOARD_FILTER="${2:-}"  # optional: --board <id>

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

load_env() {
  local env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: $env_file not found" >&2
    exit 1
  fi
  # Export non-comment, non-blank lines, stripping inline comments
  set -a
  # shellcheck disable=SC1090
  source <(grep -E '^[A-Z_]+=.' "$env_file" | sed 's/[[:space:]]*#.*$//')
  set +a
}

put_param() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "  SKIP  $name (empty value)"
    return
  fi
  aws ssm put-parameter \
    --name "$name" \
    --type SecureString \
    --value "$value" \
    --overwrite \
    --no-cli-pager \
    --output text \
    --query "Version" | xargs -I{} echo "  OK    $name (version {})"
}

setup_board() {
  local board_id="$1"
  local env_file="$REPO_ROOT/.env.$board_id"

  echo ""
  echo "── $board_id ──────────────────────────────────────────"
  load_env "$env_file"

  put_param "/giga-mcp-server/$board_id/jira-api-token"   "${GIGA_JIRA_API_TOKEN:-}"
  put_param "/giga-mcp-server/$board_id/anthropic-api-key" "${GIGA_ANTHROPIC_API_KEY:-}"
  put_param "/giga-mcp-server/$board_id/github-token"     "${GIGA_GITHUB_TOKEN:-}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "Setting up SSM SecureString parameters..."

if [[ -n "$BOARD_FILTER" ]]; then
  setup_board "$BOARD_FILTER"
else
  setup_board "gigacorp-react"
  setup_board "pitchvault-react"
fi

echo ""
echo "Done."
