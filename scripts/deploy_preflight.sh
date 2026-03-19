#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

NAMESPACE="${NAMESPACE:-labs}"
VALUES_FILE="${VALUES_FILE:-$ROOT_DIR/deploy/helm/values-production.yaml}"
RUNTIME_SECRET_NAME="${RUNTIME_SECRET_NAME:-bretter-runtime-secrets}"
RUNTIME_SECRET_KEY="${RUNTIME_SECRET_KEY:-encryption_key}"
SIGNATURE_SECRET_NAME="${SIGNATURE_SECRET_NAME:-bretter-signing}"
SIGNATURE_SECRET_KEY="${SIGNATURE_SECRET_KEY:-cosign.pub}"
SKIP_CLUSTER_CHECKS="${SKIP_CLUSTER_CHECKS:-0}"

fail_count=0

log() {
  printf '%s\n' "$*"
}

pass_check() {
  log "PASS: $1"
}

fail_check() {
  log "FAIL: $1"
  fail_count=$((fail_count + 1))
}

run_check() {
  local name="$1"
  shift
  if "$@"; then
    pass_check "$name"
  else
    fail_check "$name"
  fi
}

log "Deploy preflight started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: ${NAMESPACE}"
log "Values file: ${VALUES_FILE}"

run_check "strict production values validation" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$VALUES_FILE"

if [ "$SKIP_CLUSTER_CHECKS" = "1" ]; then
  log "Cluster checks skipped (SKIP_CLUSTER_CHECKS=1)."
  if [ "$fail_count" -ne 0 ]; then
    exit 1
  fi
  exit 0
fi

run_check "kubectl access" kubectl version --request-timeout=10s >/dev/null
run_check "namespace exists" kubectl get namespace "$NAMESPACE" >/dev/null

if kubectl -n "$NAMESPACE" get secret "$RUNTIME_SECRET_NAME" -o "jsonpath={.data.${RUNTIME_SECRET_KEY}}" >/dev/null 2>&1; then
  pass_check "runtime secret/key present (${RUNTIME_SECRET_NAME}/${RUNTIME_SECRET_KEY})"
else
  fail_check "runtime secret/key present (${RUNTIME_SECRET_NAME}/${RUNTIME_SECRET_KEY})"
fi

if kubectl -n "$NAMESPACE" get secret "$SIGNATURE_SECRET_NAME" -o "jsonpath={.data.${SIGNATURE_SECRET_KEY}}" >/dev/null 2>&1; then
  pass_check "signature key secret present (${SIGNATURE_SECRET_NAME}/${SIGNATURE_SECRET_KEY})"
else
  fail_check "signature key secret present (${SIGNATURE_SECRET_NAME}/${SIGNATURE_SECRET_KEY})"
fi

if [ "$fail_count" -ne 0 ]; then
  log "Deploy preflight failed with ${fail_count} check(s)."
  exit 1
fi

log "Deploy preflight passed."
