#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python run.py
fi

exec python3 run.py
