#!/usr/bin/env bash
set -euo pipefail

# Build the runtime MediaMTX config from the committed base using PyYAML
# (rtspAddress + optional auth block from TARGET_* env). No shell string-munging.
MTX_CONFIG=/tmp/mediamtx.runtime.yml
uv run --frozen python -m relay.mediamtx_config \
  --base /app/deploy/mediamtx.yml --out "$MTX_CONFIG" \
  --streams-config /app/config/streams.yml

# Start MediaMTX (RTSP server) in the background.
/usr/local/bin/mediamtx "$MTX_CONFIG" &
MEDIAMTX_PID=$!

# Give the RTSP server a moment to bind.
sleep 1

PROXY_PID=""
# Forward signals to children for clean docker stop.
trap 'kill -TERM "$MEDIAMTX_PID" "${PROXY_PID:-}" 2>/dev/null || true' TERM INT

# Run the proxy with whatever CMD/args were provided (default: parse).
uv run --frozen python -m relay "$@" &
PROXY_PID=$!
wait "$PROXY_PID"
