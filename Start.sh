#!/usr/bin/env bash
# Starts the pdf_to_md web app (webapp.main:app) via uvicorn in the background.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
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

nohup "$PYTHON" -m uvicorn webapp.main:app --host "$HOST" --port "$PORT" \
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
