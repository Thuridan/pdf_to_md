#!/usr/bin/env bash
# Starts the pdf_to_md web app (backend.src.app:app) via uvicorn in the background.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ] && [ "$HOST" != "::1" ]; then
    echo "WARNING: HOST=$HOST - listening beyond loopback. There is no auth or" >&2
    echo "         HTTPS; anyone who can reach this host/port on the network can" >&2
    echo "         upload, list, download and delete files. Restrict access via" >&2
    echo "         your firewall (ufw), or set HOST=127.0.0.1 to bind loopback-only." >&2
fi

RUN_DIR="$DIR/.run"
PID_FILE="$RUN_DIR/uvicorn.pid"
LOG_FILE="$RUN_DIR/uvicorn.log"
PYTHON="$DIR/.venv/bin/python3"

mkdir -p "$RUN_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Already running (PID $(cat "$PID_FILE")) at http://$HOST:$PORT"
    exit 0
fi

if [ ! -x "$PYTHON" ]; then
    echo "Virtualenv not found at .venv - create it and install the 'web' extra first." >&2
    exit 1
fi

nohup "$PYTHON" -m uvicorn backend.src.app:app --host "$HOST" --port "$PORT" \
    > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
disown

echo "Starting (PID $(cat "$PID_FILE"))... logs: $LOG_FILE"
for _ in $(seq 1 30); do
    if curl -sf "http://$HOST:$PORT/api/health" > /dev/null 2>&1; then
        echo "Up at http://$HOST:$PORT"
        exit 0
    fi
    sleep 1
done

echo "Did not become healthy in time - check $LOG_FILE" >&2
exit 1
