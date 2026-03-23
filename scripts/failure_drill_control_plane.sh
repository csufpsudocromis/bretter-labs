#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/failure-drill}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
SIMULATE_IMAGE_PULL_FAILURE="${SIMULATE_IMAGE_PULL_FAILURE:-1}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="${REPORT_DIR}/control-plane-failure-drill-${timestamp}.txt"
mkdir -p "$REPORT_DIR"
touch "$report_path"

log() {
  printf '%s\n' "$*" | tee -a "$report_path"
}

run_check() {
  local name="$1"
  shift
  if "$@" >>"$report_path" 2>&1; then
    log "PASS: $name"
  else
    log "FAIL: $name"
    return 1
  fi
}

wait_for_image_pull_failure() {
  local pod_name="$1"
  local deadline reason
  deadline=$(($(date +%s) + WAIT_TIMEOUT_SECONDS))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    reason="$(kubectl -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
    if [ "$reason" = "ErrImagePull" ] || [ "$reason" = "ImagePullBackOff" ]; then
      return 0
    fi
    sleep 2
  done
  return 1
}

log "Failure drill started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: $NAMESPACE"
log "Report: $report_path"

run_check "backend deployment exists" kubectl -n "$NAMESPACE" get deployment bretter-backend
run_check "frontend deployment exists" kubectl -n "$NAMESPACE" get deployment bretter-frontend
run_check "postgres deployment exists" kubectl -n "$NAMESPACE" get deployment bretter-postgres

backend_pod="$(kubectl -n "$NAMESPACE" get pods -l app=bretter-backend -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$backend_pod" ]; then
  log "FAIL: backend pod discovery"
  exit 1
fi
log "Deleting backend pod for restart drill: ${backend_pod}"
run_check "delete backend pod" kubectl -n "$NAMESPACE" delete pod "$backend_pod" --wait=false
run_check "backend rollout after pod delete" kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout="${WAIT_TIMEOUT_SECONDS}s"

log "Restarting postgres deployment for dependency recovery drill."
run_check "postgres rollout restart" kubectl -n "$NAMESPACE" rollout restart deployment/bretter-postgres
run_check "postgres rollout status" kubectl -n "$NAMESPACE" rollout status deployment/bretter-postgres --timeout="${WAIT_TIMEOUT_SECONDS}s"
run_check "backend rollout status after postgres restart" kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout="${WAIT_TIMEOUT_SECONDS}s"

if [ "$SIMULATE_IMAGE_PULL_FAILURE" = "1" ]; then
  bad_pod="bretter-drill-imagepull-${timestamp,,}"
  log "Creating invalid-image pod to simulate pull failure: ${bad_pod}"
  kubectl -n "$NAMESPACE" delete pod "$bad_pod" --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" apply -f - >>"$report_path" 2>&1 <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${bad_pod}
  labels:
    app.kubernetes.io/part-of: bretter-labs
spec:
  restartPolicy: Never
  containers:
    - name: pullfail
      image: docker.io/library/this-image-should-not-exist:failure-drill-${timestamp,,}
      imagePullPolicy: Always
      command: ["sh", "-c", "sleep 10"]
EOF
  if wait_for_image_pull_failure "$bad_pod"; then
    log "PASS: image pull failure simulation reached ErrImagePull/ImagePullBackOff."
  else
    log "FAIL: image pull failure simulation did not reach ErrImagePull/ImagePullBackOff within timeout."
    kubectl -n "$NAMESPACE" get pod "$bad_pod" -o yaml >>"$report_path" 2>&1 || true
    kubectl -n "$NAMESPACE" delete pod "$bad_pod" --ignore-not-found >/dev/null 2>&1 || true
    exit 1
  fi
  kubectl -n "$NAMESPACE" delete pod "$bad_pod" --ignore-not-found >/dev/null 2>&1 || true
else
  log "Skipping image pull failure simulation (SIMULATE_IMAGE_PULL_FAILURE=0)."
fi

log "PASS: control-plane failure drill completed."
log "Report written to: $report_path"
