#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

NAMESPACE="${NAMESPACE:-labs}"
HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-bretter-labs}"
TARGET_REVISION="${TARGET_REVISION:-}"
ROLLBACK_WAIT_TIMEOUT_SECONDS="${ROLLBACK_WAIT_TIMEOUT_SECONDS:-600}"
RUN_GO_LIVE_PROOF="${RUN_GO_LIVE_PROOF:-1}"
REQUIRE_SITE_VALUES_FILE="${REQUIRE_SITE_VALUES_FILE:-1}"

log() {
  printf '==> %s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

if ! command -v helm >/dev/null 2>&1; then
  fail "helm is required."
fi
if ! command -v kubectl >/dev/null 2>&1; then
  fail "kubectl is required."
fi

if [[ ! "$ROLLBACK_WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$ROLLBACK_WAIT_TIMEOUT_SECONDS" -lt 60 ]; then
  fail "ROLLBACK_WAIT_TIMEOUT_SECONDS must be an integer >= 60."
fi
case "$RUN_GO_LIVE_PROOF" in
  0 | 1) ;;
  *) fail "RUN_GO_LIVE_PROOF must be either 0 or 1." ;;
esac

resolve_target_revision() {
  local requested="$1"
  local history_json
  history_json="$(helm -n "$NAMESPACE" history "$HELM_RELEASE_NAME" -o json)"
  python3 - "$requested" "$history_json" <<'PY'
import json
import sys

requested = str(sys.argv[1] or "").strip()
history = json.loads(str(sys.argv[2] or "[]"))
if not isinstance(history, list) or not history:
    raise SystemExit("no Helm history entries found.")

rows = []
for item in history:
    revision_raw = str(item.get("revision") or "").strip()
    status = str(item.get("status") or "").strip().lower()
    if not revision_raw.isdigit():
        continue
    rows.append((int(revision_raw), status))

if not rows:
    raise SystemExit("no usable Helm history revisions found.")

rows.sort(key=lambda pair: pair[0])
if requested:
    if not requested.isdigit():
        raise SystemExit(f"TARGET_REVISION must be an integer (found {requested!r}).")
    target = int(requested)
    if target not in {rev for rev, _ in rows}:
        raise SystemExit(f"TARGET_REVISION={target} does not exist in Helm history.")
    print(target)
    raise SystemExit(0)

current = rows[-1][0]
candidates = [rev for rev, status in rows if rev < current and status in {"deployed", "superseded"}]
if not candidates:
    raise SystemExit("no previous deployed/superseded revision found for rollback.")
print(candidates[-1])
PY
}

log "Resolving rollback target revision for release=${HELM_RELEASE_NAME} namespace=${NAMESPACE}..."
target_revision="$(resolve_target_revision "$TARGET_REVISION")" || fail "failed to resolve rollback target revision."

log "Rolling back Helm release ${HELM_RELEASE_NAME} to revision ${target_revision}..."
helm -n "$NAMESPACE" rollback "$HELM_RELEASE_NAME" "$target_revision" --wait --timeout "${ROLLBACK_WAIT_TIMEOUT_SECONDS}s"

log "Waiting for backend/frontend rollout after rollback..."
kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout="${ROLLBACK_WAIT_TIMEOUT_SECONDS}s"
kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout="${ROLLBACK_WAIT_TIMEOUT_SECONDS}s"

if [ "$RUN_GO_LIVE_PROOF" -eq 1 ]; then
  log "Running production go-live proof after rollback..."
  NAMESPACE="$NAMESPACE" REQUIRE_SITE_VALUES_FILE="$REQUIRE_SITE_VALUES_FILE" "$ROOT_DIR/scripts/production_go_live_proof.sh"
fi

log "Rollback completed successfully."
