#!/usr/bin/env bash
# Restarts the pdf_to_md web app: stop.sh followed by start.sh.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/stop.sh"
"$DIR/start.sh"
