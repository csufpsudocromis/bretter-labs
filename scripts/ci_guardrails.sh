#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

cd "$ROOT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/scripts/check_release_discipline.py"
if ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
  echo "ERROR: pytest is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c "import fastapi" >/dev/null 2>&1; then
  echo "ERROR: fastapi is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi

PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" -m pytest -q backend/tests

"$ROOT_DIR/scripts/smoke_setup.sh"
