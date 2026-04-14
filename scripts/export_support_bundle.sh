#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/artifacts/support-bundles}"
TAIL_LINES="${TAIL_LINES:-500}"
EVENT_LIMIT="${EVENT_LIMIT:-250}"
ALERTMANAGER_API_URL="${ALERTMANAGER_API_URL:-http://kube-prometheus-stack-alertmanager.monitoring.svc.cluster.local:9093/api/v2/alerts}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
bundle_dir="${OUT_DIR}/support-bundle-${NAMESPACE}-${timestamp}"
bundle_tgz="${bundle_dir}.tar.gz"
mkdir -p "$bundle_dir"

log() {
  printf '%s\n' "$*"
}

capture_cmd() {
  local outfile="$1"
  shift
  {
    printf '$'
    for token in "$@"; do
      printf ' %q' "$token"
    done
    printf '\n\n'
    "$@"
  } >"$outfile" 2>&1 || true
}

capture_tail_events() {
  local outfile="$1"
  {
    echo '$ kubectl -n '"$NAMESPACE"' get events --sort-by=.metadata.creationTimestamp | tail -n '"$EVENT_LIMIT"
    echo
    kubectl -n "$NAMESPACE" get events --sort-by=.metadata.creationTimestamp | tail -n "$EVENT_LIMIT"
  } >"$outfile" 2>&1 || true
}

collect_failed_jobs() {
  local outfile="$1"
  python3 - "$NAMESPACE" <<'PY' >"$outfile" 2>/dev/null || true
import json
import subprocess
import sys

namespace = str(sys.argv[1] if len(sys.argv) > 1 else "labs").strip() or "labs"
raw = subprocess.check_output(["kubectl", "-n", namespace, "get", "jobs", "-o", "json"], text=True)
rows = (json.loads(raw) or {}).get("items") or []
for row in rows:
    metadata = row.get("metadata") or {}
    status = row.get("status") or {}
    failed = int(status.get("failed") or 0)
    conditions = status.get("conditions") or []
    is_failed = failed > 0 or any(str((cond or {}).get("type") or "").lower() == "failed" for cond in conditions)
    if is_failed:
        name = str(metadata.get("name") or "").strip()
        if name:
            print(name)
PY
}

log "Collecting support bundle for namespace=$NAMESPACE"
log "Output directory: $bundle_dir"

capture_cmd "$bundle_dir/kubectl-version.txt" kubectl version
capture_cmd "$bundle_dir/namespace-overview.txt" kubectl -n "$NAMESPACE" get deploy,pod,svc,pvc,job,cronjob -o wide
capture_cmd "$bundle_dir/namespace-describe.txt" kubectl describe namespace "$NAMESPACE"
capture_tail_events "$bundle_dir/namespace-events.txt"
capture_cmd "$bundle_dir/monitoring-alerts-overview.txt" kubectl -n "$MONITORING_NAMESPACE" get prometheusrule,alertmanager -o wide

if command -v curl >/dev/null 2>&1; then
  capture_cmd "$bundle_dir/alertmanager-alerts.json" curl -fsS --max-time 15 "$ALERTMANAGER_API_URL"
else
  printf 'curl not available; skipped Alertmanager API pull.\n' >"$bundle_dir/alertmanager-alerts.json"
fi

for deploy in bretter-backend bretter-frontend bretter-postgres bretter-labimageimport-controller; do
  if kubectl -n "$NAMESPACE" get deploy "$deploy" >/dev/null 2>&1; then
    capture_cmd "$bundle_dir/logs-${deploy}.txt" kubectl -n "$NAMESPACE" logs "deploy/${deploy}" --tail="$TAIL_LINES"
  fi
done

capture_cmd "$bundle_dir/pods-json.txt" kubectl -n "$NAMESPACE" get pods -o json
capture_cmd "$bundle_dir/jobs-json.txt" kubectl -n "$NAMESPACE" get jobs -o json
capture_cmd "$bundle_dir/pvcs-json.txt" kubectl -n "$NAMESPACE" get pvc -o json

failed_jobs_file="$bundle_dir/failed-jobs.txt"
collect_failed_jobs "$failed_jobs_file"
if [ -s "$failed_jobs_file" ]; then
  while IFS= read -r job_name; do
    [ -n "$job_name" ] || continue
    capture_cmd "$bundle_dir/job-${job_name}.describe.txt" kubectl -n "$NAMESPACE" describe job "$job_name"
    capture_cmd "$bundle_dir/job-${job_name}.logs.txt" kubectl -n "$NAMESPACE" logs "job/${job_name}" --all-containers=true
  done <"$failed_jobs_file"
fi

capture_cmd "$bundle_dir/non-running-pods.txt" kubectl -n "$NAMESPACE" get pods --field-selector=status.phase!=Running -o wide
capture_cmd "$bundle_dir/pending-pvcs.txt" kubectl -n "$NAMESPACE" get pvc --field-selector=status.phase=Pending -o wide

tar -czf "$bundle_tgz" -C "$OUT_DIR" "$(basename "$bundle_dir")"
log "Support bundle ready: $bundle_tgz"
