#!/bin/bash
set -e

# Start the Go WhatsApp bridge in the background
echo "Starting WhatsApp bridge..."
mkdir -p /app/store
cd /app/store
whatsapp-bridge &
BRIDGE_PID=$!

# Wait for the bridge to be ready
echo "Waiting for WhatsApp bridge on port 8080..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/api/send > /dev/null 2>&1 || [ $i -eq 30 ]; then
        break
    fi
    sleep 1
done

# Export the DB path to point at the bridge's store
export GIGA_WHATSAPP_DB_PATH="${GIGA_WHATSAPP_DB_PATH:-/app/store/messages.db}"

echo "Starting giga-mcp-server..."
exec giga-mcp-server "$@"
