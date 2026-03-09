#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

cd "$ROOT_DIR/backend"

PYTHONPATH="$PWD" "$PYTHON_BIN" -m pytest -q \
  tests/test_ci_guardrails.py \
  tests/test_e2e_regressions.py
