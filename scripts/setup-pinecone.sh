#!/usr/bin/env bash
# Creates the Pinecone integrated-inference indexes for a board if they
# don't already exist. Idempotent — safe to re-run.
#
# Reads index name + API key from .env.<boardId>. Run after setup-ssm.sh
# and before `cdk deploy` so the App Runner service can connect to the
# index on boot.
#
# Usage: ./scripts/setup-pinecone.sh --board <boardId>
#
# Env var overrides (with defaults):
#   PINECONE_EMBED_MODEL=llama-text-embed-v2
#   PINECONE_CLOUD=aws
#   PINECONE_REGION=us-east-1
#   PINECONE_API_VERSION=2025-04
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMBED_MODEL="${PINECONE_EMBED_MODEL:-llama-text-embed-v2}"
CLOUD="${PINECONE_CLOUD:-aws}"
REGION="${PINECONE_REGION:-us-east-1}"
API_VERSION="${PINECONE_API_VERSION:-2025-04}"
BOARD_FILTER=""

# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
Usage: $0 --board <boardId>

Creates the Pinecone integrated-inference indexes (ticket + optional
code-history) for the board if they don't already exist.

Env var overrides:
  PINECONE_EMBED_MODEL=$EMBED_MODEL
  PINECONE_CLOUD=$CLOUD
  PINECONE_REGION=$REGION
  PINECONE_API_VERSION=$API_VERSION
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD_FILTER="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$BOARD_FILTER" ]]; then
  echo "ERROR: --board is required" >&2
  usage
  exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Read a single variable value from an env file, stripping inline comments.
read_env_var() {
  local env_file="$1"
  local var_name="$2"
  grep -E "^${var_name}=" "$env_file" 2>/dev/null \
    | tail -1 \
    | cut -d= -f2- \
    | sed 's/[[:space:]]*#.*$//' \
    | xargs
}

# Check if an index exists. Echoes "exists", "missing", or exits on unexpected status.
index_exists() {
  local api_key="$1"
  local name="$2"
  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "Api-Key: $api_key" \
    -H "X-Pinecone-API-Version: $API_VERSION" \
    "https://api.pinecone.io/indexes/$name")
  case "$code" in
    200) echo "exists" ;;
    404) echo "missing" ;;
    *) echo "ERROR: unexpected status $code while checking index $name" >&2; exit 1 ;;
  esac
}

# Create an integrated-inference index. Body is generated inline.
create_index() {
  local api_key="$1"
  local name="$2"

  local payload
  payload=$(cat <<JSON
{
  "name": "$name",
  "cloud": "$CLOUD",
  "region": "$REGION",
  "embed": {
    "model": "$EMBED_MODEL",
    "field_map": {"text": "text"}
  }
}
JSON
)

  local response
  response=$(curl -sS -X POST \
    -H "Api-Key: $api_key" \
    -H "Content-Type: application/json" \
    -H "X-Pinecone-API-Version: $API_VERSION" \
    -d "$payload" \
    "https://api.pinecone.io/indexes/create-for-model")

  # Success responses include "name" at top level; errors include "error" or "message".
  if echo "$response" | grep -q '"name"[[:space:]]*:'; then
    return 0
  fi

  echo "ERROR: create-for-model failed for $name:" >&2
  echo "$response" >&2
  return 1
}

ensure_index() {
  local api_key="$1"
  local var_name="$2"
  local env_file="$3"

  local index_name
  index_name="$(read_env_var "$env_file" "$var_name")"

  if [[ -z "$index_name" ]]; then
    echo "  SKIP  $var_name not set in $(basename "$env_file")"
    return
  fi

  local state
  state="$(index_exists "$api_key" "$index_name")"
  if [[ "$state" == "exists" ]]; then
    echo "  OK    $index_name (already exists)"
    return
  fi

  if create_index "$api_key" "$index_name"; then
    echo "  OK    $index_name (created — model=$EMBED_MODEL, $CLOUD/$REGION)"
  else
    exit 1
  fi
}

setup_board() {
  local board_id="$1"
  local env_file="$REPO_ROOT/.env.$board_id"

  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: $env_file not found" >&2
    exit 1
  fi

  local api_key
  api_key="$(read_env_var "$env_file" GIGA_PINECONE_API_KEY)"
  if [[ -z "$api_key" ]]; then
    echo "ERROR: GIGA_PINECONE_API_KEY not set in $env_file" >&2
    exit 1
  fi

  echo ""
  echo "── $board_id ──────────────────────────────────────────"
  ensure_index "$api_key" GIGA_PINECONE_INDEX_NAME "$env_file"
  ensure_index "$api_key" GIGA_PINECONE_CODEHISTORY_INDEX_NAME "$env_file"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "Setting up Pinecone integrated-inference indexes..."
setup_board "$BOARD_FILTER"
echo ""
echo "Done."
