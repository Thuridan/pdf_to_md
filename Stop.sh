#!/usr/bin/env bash
# Stops the pdf_to_md web app started by Start.sh.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$DIR/.run"
PID_FILE="$RUN_DIR/uvicorn.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "Not running (no PID file)."
    exit 0
fi

PID="$(cat "$PID_FILE")"

if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 15); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "PID $PID did not stop gracefully, forcing..." >&2
        kill -9 "$PID" 2>/dev/null || true
    fi
    echo "Stopped (PID $PID)."
else
    echo "PID $PID not running (stale PID file)."
fi

rm -f "$PID_FILE"
