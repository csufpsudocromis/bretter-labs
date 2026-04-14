#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

NAMESPACE="${NAMESPACE:-labs}"
HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-bretter-labs}"
BASE_VALUES_FILE="${BASE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-production.yaml}"
SITE_VALUES_FILE="${SITE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-prod-site.yaml}"
REQUIRE_SITE_VALUES_FILE="${REQUIRE_SITE_VALUES_FILE:-1}"
ROLLBACK_ON_PROOF_FAILURE="${ROLLBACK_ON_PROOF_FAILURE:-1}"
HELM_TIMEOUT_SECONDS="${HELM_TIMEOUT_SECONDS:-900}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/deploy-safe}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="${REPORT_DIR}/deploy-safe-${timestamp}.txt"
mkdir -p "$REPORT_DIR"
touch "$report_path"

log() {
  printf '%s\n' "$*" | tee -a "$report_path"
}

fail() {
  log "FAIL: $*"
  log "Report written to: $report_path"
  exit 1
}

case "$REQUIRE_SITE_VALUES_FILE" in
  0 | 1) ;;
  *) fail "REQUIRE_SITE_VALUES_FILE must be 0 or 1." ;;
esac
case "$ROLLBACK_ON_PROOF_FAILURE" in
  0 | 1) ;;
  *) fail "ROLLBACK_ON_PROOF_FAILURE must be 0 or 1." ;;
esac
if [[ ! "$HELM_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$HELM_TIMEOUT_SECONDS" -lt 120 ]; then
  fail "HELM_TIMEOUT_SECONDS must be an integer >= 120."
fi

if ! command -v helm >/dev/null 2>&1; then
  fail "helm is required."
fi
if ! command -v kubectl >/dev/null 2>&1; then
  fail "kubectl is required."
fi
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is required."
fi

values_args=(
  -f "$ROOT_DIR/deploy/helm/values.yaml"
  -f "$BASE_VALUES_FILE"
)
validator_args=(
  --strict
  -f "$BASE_VALUES_FILE"
)
site_values_present=0
if [ -f "$SITE_VALUES_FILE" ]; then
  values_args+=(-f "$SITE_VALUES_FILE")
  validator_args+=(-f "$SITE_VALUES_FILE")
  site_values_present=1
elif [ "$REQUIRE_SITE_VALUES_FILE" -eq 1 ]; then
  fail "Site values file is required but missing: $SITE_VALUES_FILE"
fi

log "Safe deploy started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: $NAMESPACE"
log "Release: $HELM_RELEASE_NAME"
log "Base values: $BASE_VALUES_FILE"
if [ "$site_values_present" -eq 1 ]; then
  log "Site values: $SITE_VALUES_FILE"
else
  log "Site values: not provided (allowed)"
fi
log "Report: $report_path"

pre_revision=""
if helm -n "$NAMESPACE" status "$HELM_RELEASE_NAME" >/dev/null 2>&1; then
  history_json="$(helm -n "$NAMESPACE" history "$HELM_RELEASE_NAME" -o json)"
  pre_revision="$(
    python3 - "$history_json" <<'PY'
import json
import sys

rows = json.loads(str(sys.argv[1] or "[]"))
if not isinstance(rows, list) or not rows:
    print("", end="")
    raise SystemExit(0)
last = rows[-1]
revision = str(last.get("revision") or "").strip()
print(revision, end="")
PY
  )"
fi
if [ -n "$pre_revision" ]; then
  log "Pre-deploy helm revision: $pre_revision"
else
  log "Pre-deploy helm revision: none (new release install path)"
fi

if python3 "$ROOT_DIR/scripts/validate_production_profile.py" "${validator_args[@]}" >>"$report_path" 2>&1; then
  log "PASS: strict production profile validation."
else
  fail "strict production profile validation failed."
fi

if helm -n "$NAMESPACE" upgrade --install "$HELM_RELEASE_NAME" "$ROOT_DIR/deploy/helm" \
  --create-namespace \
  "${values_args[@]}" \
  --atomic \
  --wait \
  --timeout "${HELM_TIMEOUT_SECONDS}s" >>"$report_path" 2>&1; then
  log "PASS: helm upgrade --install completed (--atomic --wait)."
else
  fail "helm upgrade --install failed (atomic rollback, if needed, handled by Helm)."
fi

if kubectl -n "$NAMESPACE" rollout status deployment/bretter-postgres --timeout=300s >>"$report_path" 2>&1 &&
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s >>"$report_path" 2>&1 &&
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=300s >>"$report_path" 2>&1 &&
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-labimageimport-controller --timeout=300s >>"$report_path" 2>&1; then
  log "PASS: core deployment rollout checks passed."
else
  fail "core deployment rollout checks failed."
fi

proof_exit=0
if [ "$site_values_present" -eq 1 ]; then
  NAMESPACE="$NAMESPACE" SITE_VALUES_FILE="$SITE_VALUES_FILE" "$ROOT_DIR/scripts/production_go_live_proof.sh" >>"$report_path" 2>&1 || proof_exit=$?
else
  NAMESPACE="$NAMESPACE" REQUIRE_SITE_VALUES_FILE=0 "$ROOT_DIR/scripts/production_go_live_proof.sh" >>"$report_path" 2>&1 || proof_exit=$?
fi

if [ "$proof_exit" -eq 0 ]; then
  log "PASS: production go-live proof passed."
else
  log "FAIL: production go-live proof failed."
fi

synthetic_gate_exit=0
if [ "$proof_exit" -eq 0 ]; then
  if python3 "$ROOT_DIR/scripts/verify_synthetic_gate_report.py" \
    --report "$report_path" \
    --require-image-upload-check >>"$report_path" 2>&1; then
    log "PASS: synthetic post-deploy gate coverage verified."
  else
    synthetic_gate_exit=$?
    log "FAIL: synthetic post-deploy gate coverage verification failed."
  fi
fi

drift_exit=0
if [ "$proof_exit" -eq 0 ] && [ "$synthetic_gate_exit" -eq 0 ]; then
  if python3 "$ROOT_DIR/scripts/check_live_config_drift.py" \
    --namespace "$NAMESPACE" \
    --release-name "$HELM_RELEASE_NAME" \
    "${values_args[@]}" >>"$report_path" 2>&1; then
    log "PASS: live config drift gate passed."
  else
    drift_exit=$?
    log "FAIL: live config drift gate failed."
  fi
fi

if [ "$proof_exit" -eq 0 ] && [ "$synthetic_gate_exit" -eq 0 ] && [ "$drift_exit" -eq 0 ]; then
  log "Report written to: $report_path"
  exit 0
fi

if [ "$ROLLBACK_ON_PROOF_FAILURE" -ne 1 ]; then
  if [ "$proof_exit" -ne 0 ]; then
    fail "rollback disabled after proof failure."
  fi
  if [ "$synthetic_gate_exit" -ne 0 ]; then
    fail "rollback disabled after synthetic gate verification failure."
  fi
  fail "rollback disabled after live config drift failure."
fi
if [ -z "$pre_revision" ]; then
  if [ "$proof_exit" -ne 0 ]; then
    fail "cannot rollback automatically after proof failure (no previous Helm revision)."
  fi
  if [ "$synthetic_gate_exit" -ne 0 ]; then
    fail "cannot rollback automatically after synthetic gate verification failure (no previous Helm revision)."
  fi
  fail "cannot rollback automatically after live config drift failure (no previous Helm revision)."
fi

log "Attempting automatic rollback to revision ${pre_revision}..."
rollback_exit=0
NAMESPACE="$NAMESPACE" \
  HELM_RELEASE_NAME="$HELM_RELEASE_NAME" \
  TARGET_REVISION="$pre_revision" \
  RUN_GO_LIVE_PROOF=1 \
  REQUIRE_SITE_VALUES_FILE="$REQUIRE_SITE_VALUES_FILE" \
  "$ROOT_DIR/scripts/rollback_release.sh" >>"$report_path" 2>&1 || rollback_exit=$?

if [ "$rollback_exit" -ne 0 ]; then
  if [ "$proof_exit" -ne 0 ]; then
    fail "go-live proof failed and rollback to revision ${pre_revision} also failed."
  fi
  if [ "$synthetic_gate_exit" -ne 0 ]; then
    fail "synthetic gate verification failed and rollback to revision ${pre_revision} also failed."
  fi
  fail "live config drift gate failed and rollback to revision ${pre_revision} also failed."
fi

if [ "$proof_exit" -ne 0 ]; then
  fail "go-live proof failed; release was rolled back to revision ${pre_revision}."
fi
if [ "$synthetic_gate_exit" -ne 0 ]; then
  fail "synthetic gate verification failed; release was rolled back to revision ${pre_revision}."
fi
fail "live config drift gate failed; release was rolled back to revision ${pre_revision}."
