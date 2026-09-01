#!/usr/bin/env bash
# Restarts the pdf_to_md web app: Stop.sh followed by Start.sh.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/Stop.sh"
"$DIR/Start.sh"
