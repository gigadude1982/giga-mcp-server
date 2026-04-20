#!/usr/bin/env bash
# Launch MCP Inspector connected to the remote App Runner server.
#
# Usage:
#   ./inspect-remote.sh                          # Uses default URL (mcp.gigacorp.co)
#   ./inspect-remote.sh https://custom-url.com   # Override URL
set -euo pipefail

REMOTE_URL="${1:-https://mcp.gigacorp.co/mcp}"

echo "Connecting MCP Inspector to: $REMOTE_URL"
exec npx @modelcontextprotocol/inspector --url "$REMOTE_URL"
