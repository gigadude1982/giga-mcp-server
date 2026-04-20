#!/usr/bin/env bash
# Launch MCP Inspector, then connect to a remote server via the web UI.
#
# Usage:
#   ./inspect-remote.sh                          # Uses default URL
#   ./inspect-remote.sh https://custom-url.com   # Override URL
#   ./inspect-remote.sh --no-tls-bypass           # Skip Zscaler workaround
set -euo pipefail

REMOTE_URL="${1:-https://mcp.gigacorp.co/mcp}"
TLS_BYPASS=true

if [[ "${1:-}" == "--no-tls-bypass" ]]; then
    TLS_BYPASS=false
    REMOTE_URL="${2:-https://mcp.gigacorp.co/mcp}"
fi

echo "Starting MCP Inspector..."
echo ""
echo "Once the Inspector opens in your browser:"
echo "  1. Change transport to 'Streamable HTTP'"
echo "  2. Enter URL: $REMOTE_URL"
echo "  3. Click Connect"
echo ""

if $TLS_BYPASS; then
    echo "(Zscaler TLS bypass enabled — use --no-tls-bypass to disable)"
    echo ""
    export NODE_TLS_REJECT_UNAUTHORIZED=0
fi

exec npx @modelcontextprotocol/inspector
