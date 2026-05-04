#!/usr/bin/env bash
# Creates or updates SSM SecureString parameters for all boards.
# Reads secrets from .env.<boardId> files in the repo root.
# Usage: ./scripts/setup-ssm.sh [--board <boardId>]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOARD_FILTER="${2:-}"  # optional: --board <id>

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Read a single variable value from an env file, stripping inline comments.
read_env_var() {
  local env_file="$1"
  local var_name="$2"
  grep -E "^${var_name}=" "$env_file" \
    | tail -1 \
    | cut -d= -f2- \
    | sed 's/[[:space:]]*#.*$//' \
    | xargs  # trim surrounding whitespace/quotes
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

  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: $env_file not found" >&2
    exit 1
  fi

  echo ""
  echo "── $board_id ──────────────────────────────────────────"

  put_param "/giga-mcp-server/$board_id/jira-api-token" \
    "$(read_env_var "$env_file" GIGA_JIRA_API_TOKEN)"
  put_param "/giga-mcp-server/$board_id/anthropic-api-key" \
    "$(read_env_var "$env_file" GIGA_ANTHROPIC_API_KEY)"
  put_param "/giga-mcp-server/$board_id/github-token" \
    "$(read_env_var "$env_file" GIGA_GITHUB_TOKEN)"
  put_param "/giga-mcp-server/$board_id/pinecone-api-key" \
    "$(read_env_var "$env_file" GIGA_PINECONE_API_KEY)"
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
