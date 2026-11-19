#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
export SMARTNANO_HOST="127.0.0.1"
exec .venv/bin/python server.py "${1:-8000}"
