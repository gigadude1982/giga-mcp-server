#!/usr/bin/env bash
# Launch MCP Inspector connected to the local server.
#
# Usage:
#   ./inspect-local.sh          # mock clients, no credentials needed
#   ./inspect-local.sh --live   # real JIRA + GitHub from .env
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "--live" ]]; then
    # Load .env so real credentials are available to the server
    if [[ -f "$REPO_DIR/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "$REPO_DIR/.env"
        set +a
    else
        echo "ERROR: --live requires a .env file at $REPO_DIR/.env" >&2
        exit 1
    fi
    echo "Starting inspector in LIVE mode (real JIRA + GitHub)..."
    exec npx @modelcontextprotocol/inspector \
        -- "$REPO_DIR/.venv/bin/python" -m giga_mcp_server.server
else
    echo "Starting inspector in MOCK mode (no credentials required)..."
    exec npx @modelcontextprotocol/inspector \
        -- "$REPO_DIR/.venv/bin/python" -m giga_mcp_server.server --inspect
fi
