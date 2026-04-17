# Stage 1: Build the Go WhatsApp bridge
FROM golang:1.22-bookworm AS go-builder
WORKDIR /build
# Clone whatsapp-mcp and build the bridge binary
RUN git clone --depth 1 https://github.com/lharries/whatsapp-mcp.git . && \
    cd whatsapp-bridge && \
    CGO_ENABLED=1 go build -o /whatsapp-bridge .

# Stage 2: Python runtime
FROM python:3.12-slim-bookworm
WORKDIR /app

# System deps for SQLite C extensions (used by Go bridge)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy Go bridge binary
COPY --from=go-builder /whatsapp-bridge /usr/local/bin/whatsapp-bridge

# Install Python package
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Copy entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Persistent storage for WhatsApp session + message DB
VOLUME ["/app/store"]

EXPOSE 8000 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
