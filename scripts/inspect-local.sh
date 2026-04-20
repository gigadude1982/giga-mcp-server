#!/usr/bin/env bash
# Launch MCP Inspector connected to the local server in inspect mode (mock clients).
# No credentials required.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

exec npx @modelcontextprotocol/inspector -- \
    "$REPO_DIR/.venv/bin/python" -m giga_mcp_server.server --inspect
