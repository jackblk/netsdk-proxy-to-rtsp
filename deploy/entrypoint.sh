#!/usr/bin/env bash
set -euo pipefail

# RTSP port: MediaMTX listens here, the proxy publishes here, compose exposes it.
RTSP_PORT="${TARGET_PORT:-8554}"
export MTX_RTSPADDRESS=":${RTSP_PORT}"

# Build the runtime MediaMTX config from the committed base. When RTSP credentials
# are provided, append an auth block so MediaMTX requires them to publish AND view;
# without them, behaviour is unchanged (open access).
MTX_CONFIG=/tmp/mediamtx.runtime.yml
cp /app/deploy/mediamtx.yml "$MTX_CONFIG"

if [ -n "${TARGET_USERNAME:-}" ] && [ -n "${TARGET_PASSWORD:-}" ]; then
  # Single-quote for YAML so numeric/special-char values are treated as strings;
  # YAML escapes an embedded single quote by doubling it.
  yaml_user="'${TARGET_USERNAME//\'/\'\'}'"
  yaml_pass="'${TARGET_PASSWORD//\'/\'\'}'"
  cat >> "$MTX_CONFIG" <<EOF
authInternalUsers:
  - user: ${yaml_user}
    pass: ${yaml_pass}
    ips: []
    permissions:
      - action: publish
      - action: read
  - user: any
    ips: ['127.0.0.1', '::1']
    permissions:
      - action: api
      - action: metrics
      - action: pprof
EOF
  echo "entrypoint: RTSP authentication enabled for user '${TARGET_USERNAME}'"
else
  echo "entrypoint: RTSP authentication disabled (TARGET_USERNAME/TARGET_PASSWORD not set)"
fi

# Start MediaMTX (RTSP server) in the background.
/usr/local/bin/mediamtx "$MTX_CONFIG" &
MEDIAMTX_PID=$!

# Give the RTSP server a moment to bind.
sleep 1

PROXY_PID=""
# Forward signals to children for clean docker stop.
trap 'kill -TERM "$MEDIAMTX_PID" "${PROXY_PID:-}" 2>/dev/null || true' TERM INT

# Run the proxy with whatever CMD/args were provided (default: parse).
/app/.venv/bin/python -m relay "$@" &
PROXY_PID=$!
wait "$PROXY_PID"
