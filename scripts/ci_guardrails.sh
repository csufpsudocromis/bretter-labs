#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

cd "$ROOT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/scripts/check_release_discipline.py"
TOKEN_SCAN_HISTORY="${TOKEN_SCAN_HISTORY:-1}"
if [ "$TOKEN_SCAN_HISTORY" = "1" ]; then
  "$PYTHON_BIN" "$ROOT_DIR/scripts/check_no_plaintext_tokens.py" --history
else
  "$PYTHON_BIN" "$ROOT_DIR/scripts/check_no_plaintext_tokens.py"
fi
"$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml"
"$PYTHON_BIN" "$ROOT_DIR/scripts/lint_crd_schema.py"
"$PYTHON_BIN" "$ROOT_DIR/scripts/check_openapi_drift.py"
if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl is required for CRD rendering checks." >&2
  exit 1
fi
kubectl kustomize "$ROOT_DIR/deploy/crds" >/dev/null
SKIP_CLUSTER_CHECKS=1 "$ROOT_DIR/scripts/deploy_preflight.sh"
if ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
  echo "ERROR: pytest is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c "import fastapi" >/dev/null 2>&1; then
  echo "ERROR: fastapi is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c "import black" >/dev/null 2>&1; then
  echo "ERROR: black is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi

"$PYTHON_BIN" -m black --check \
  backend/src \
  backend/tests \
  scripts/check_release_discipline.py \
  scripts/check_no_plaintext_tokens.py \
  scripts/lint_crd_schema.py \
  scripts/check_openapi_drift.py \
  scripts/export_openapi_schema.py \
  scripts/backfill_labinstances_from_db.py \
  scripts/bump_version.py \
  scripts/validate_production_profile.py \
  scripts/update_production_image_digests.py

PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" -m pytest -q backend/tests

"$ROOT_DIR/scripts/smoke_setup.sh"
"$ROOT_DIR/scripts/smoke_tls_login.sh"
