#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m pip install \
  --no-cache-dir \
  -r "$ROOT_DIR/requirements.txt" \
  'pyinstaller>=6.10,<7'
