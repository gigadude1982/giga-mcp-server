#!/usr/bin/env bash
# Launch MCP Inspector against a remote giga-mcp-server deployment.
#
# Usage:
#   ./inspect-remote.sh                        # gigacorp (default)
#   ./inspect-remote.sh --board pitchvault     # pitchvault
#   ./inspect-remote.sh --url https://...      # custom URL
set -euo pipefail

BOARD="gigacorp"
CUSTOM_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARD="$2"; shift 2 ;;
    --url)   CUSTOM_URL="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -n "$CUSTOM_URL" ]]; then
  REMOTE_URL="$CUSTOM_URL"
elif [[ "$BOARD" == "pitchvault" ]]; then
  REMOTE_URL="https://mcp.pitchvault.co/mcp"
else
  REMOTE_URL="https://mcp.gigacorp.co/mcp"
fi

echo "Starting MCP Inspector → $REMOTE_URL"
echo ""
echo "Once the Inspector opens in your browser:"
echo "  1. Transport: Streamable HTTP"
echo "  2. URL: $REMOTE_URL"
echo "  3. Click Connect"
echo ""
echo "(NODE_TLS_REJECT_UNAUTHORIZED=0 set for Zscaler compatibility)"
echo ""

export NODE_TLS_REJECT_UNAUTHORIZED=0
exec npx @modelcontextprotocol/inspector
