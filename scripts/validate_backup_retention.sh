#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

NAMESPACE="${NAMESPACE:-labs}"
BACKUP_CRONJOB_NAME="${BACKUP_CRONJOB_NAME:-bretter-postgres-backup}"
REQUIRE_BACKUP_CRONJOB="${REQUIRE_BACKUP_CRONJOB:-0}"
VALIDATION_TIMEOUT_SECONDS="${VALIDATION_TIMEOUT_SECONDS:-300}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/backup-retention}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="${REPORT_DIR}/backup-retention-${timestamp}.txt"
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

case "$REQUIRE_BACKUP_CRONJOB" in
  0 | 1) ;;
  *) fail "REQUIRE_BACKUP_CRONJOB must be 0 or 1." ;;
esac
if [[ ! "$VALIDATION_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$VALIDATION_TIMEOUT_SECONDS" -lt 30 ]; then
  fail "VALIDATION_TIMEOUT_SECONDS must be an integer >= 30."
fi

if ! command -v kubectl >/dev/null 2>&1; then
  fail "kubectl is required."
fi
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is required."
fi

log "Backup retention validation started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: $NAMESPACE"
log "CronJob: $BACKUP_CRONJOB_NAME"

if ! kubectl -n "$NAMESPACE" get cronjob "$BACKUP_CRONJOB_NAME" >/dev/null 2>&1; then
  if [ "$REQUIRE_BACKUP_CRONJOB" -eq 1 ]; then
    fail "Required backup cronjob not found: ${NAMESPACE}/${BACKUP_CRONJOB_NAME}"
  fi
  log "SKIP: backup cronjob not found; retention validation skipped."
  log "Report written to: $report_path"
  exit 0
fi

cronjob_json="$(kubectl -n "$NAMESPACE" get cronjob "$BACKUP_CRONJOB_NAME" -o json)"
metadata="$(
  python3 - "$cronjob_json" <<'PY'
import json
import sys

payload = json.loads(str(sys.argv[1] or "{}"))
spec = (((payload or {}).get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}
template_spec = ((spec.get("template") or {}).get("spec")) or {}
containers = template_spec.get("containers") or []
if not containers:
    raise SystemExit("ERROR: cronjob has no containers.")
container = containers[0]
image = str(container.get("image") or "").strip()
if not image:
    raise SystemExit("ERROR: cronjob container image is empty.")
env_values = {}
for row in container.get("env") or []:
    name = str(row.get("name") or "").strip()
    if not name:
        continue
    if "value" in row:
        env_values[name] = str(row.get("value") or "").strip()
mount_path = str(env_values.get("BACKUP_MOUNT_PATH") or "").strip()
retention_days = str(env_values.get("BACKUP_RETENTION_DAYS") or "").strip()
if not mount_path:
    raise SystemExit("ERROR: BACKUP_MOUNT_PATH is missing in cronjob env.")
if not retention_days.isdigit():
    raise SystemExit("ERROR: BACKUP_RETENTION_DAYS is missing/invalid in cronjob env.")
mount_name = ""
for mount in container.get("volumeMounts") or []:
    mp = str(mount.get("mountPath") or "").strip()
    if mp == mount_path:
        mount_name = str(mount.get("name") or "").strip()
        break
if not mount_name:
    raise SystemExit("ERROR: no volumeMount found for BACKUP_MOUNT_PATH.")
claim_name = ""
for volume in template_spec.get("volumes") or []:
    if str(volume.get("name") or "").strip() != mount_name:
        continue
    pvc = volume.get("persistentVolumeClaim") or {}
    claim_name = str(pvc.get("claimName") or "").strip()
    break
if not claim_name:
    raise SystemExit("ERROR: no persistentVolumeClaim found for backup mount volume.")

print(f"image={image}")
print(f"mount_path={mount_path}")
print(f"retention_days={retention_days}")
print(f"claim_name={claim_name}")
PY
)" || fail "failed to parse backup cronjob metadata."

image=""
mount_path=""
retention_days=""
claim_name=""
while IFS='=' read -r key value; do
  case "$key" in
    image) image="$value" ;;
    mount_path) mount_path="$value" ;;
    retention_days) retention_days="$value" ;;
    claim_name) claim_name="$value" ;;
  esac
done <<<"$metadata"

[ -n "$image" ] || fail "parsed image is empty."
[ -n "$mount_path" ] || fail "parsed mount_path is empty."
[ -n "$retention_days" ] || fail "parsed retention_days is empty."
[ -n "$claim_name" ] || fail "parsed claim_name is empty."

log "Resolved backup image: $image"
log "Resolved backup path: $mount_path"
log "Resolved retention days: $retention_days"
log "Resolved backup PVC claim: $claim_name"

validator_pod="backup-retention-audit-${timestamp,,}"
cleanup() {
  kubectl -n "$NAMESPACE" delete pod "$validator_pod" --ignore-not-found=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl -n "$NAMESPACE" apply -f - >>"$report_path" 2>&1 <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${validator_pod}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/part-of: bretter-labs
    app.kubernetes.io/component: backup-retention-audit
spec:
  restartPolicy: Never
  containers:
    - name: validator
      image: ${image}
      imagePullPolicy: IfNotPresent
      env:
        - name: BACKUP_MOUNT_PATH
          value: ${mount_path}
        - name: BACKUP_RETENTION_DAYS
          value: "${retention_days}"
      command:
        - /bin/sh
        - -lc
        - |
          set -eu
          [ -d "\$BACKUP_MOUNT_PATH" ] || { echo "ERROR: backup path missing: \$BACKUP_MOUNT_PATH" >&2; exit 2; }
          total_count="\$(find "\$BACKUP_MOUNT_PATH" -type f -name '*.dump' | wc -l | tr -d ' ')"
          old_count="\$(find "\$BACKUP_MOUNT_PATH" -type f -name '*.dump' -mtime +"\$BACKUP_RETENTION_DAYS" | wc -l | tr -d ' ')"
          echo "backup_total_count=\$total_count"
          echo "backup_old_count=\$old_count"
          if [ "\$total_count" -lt 1 ]; then
            echo "ERROR: no dump files found in backup mount." >&2
            exit 3
          fi
          if [ "\$old_count" -gt 0 ]; then
            echo "ERROR: found \${old_count} backup file(s) older than retention (\$BACKUP_RETENTION_DAYS days)." >&2
            exit 4
          fi
          echo "retention_check=pass"
      volumeMounts:
        - name: backups
          mountPath: ${mount_path}
  volumes:
    - name: backups
      persistentVolumeClaim:
        claimName: ${claim_name}
EOF

deadline=$(($(date +%s) + VALIDATION_TIMEOUT_SECONDS))
phase=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  phase="$(kubectl -n "$NAMESPACE" get pod "$validator_pod" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  case "$phase" in
    Succeeded | Failed) break ;;
    *) sleep 2 ;;
  esac
done

pod_logs="$(kubectl -n "$NAMESPACE" logs "$validator_pod" 2>&1 || true)"
if [ -n "$pod_logs" ]; then
  log "-- retention validator pod logs --"
  printf '%s\n' "$pod_logs" | tee -a "$report_path" >/dev/null
fi

if [ "$phase" != "Succeeded" ]; then
  fail "backup retention validator pod did not succeed (phase=${phase:-unknown})."
fi
if ! printf '%s\n' "$pod_logs" | grep -Eq '^retention_check=pass$'; then
  fail "retention validator did not report retention_check=pass."
fi

log "PASS: backup retention validation passed."
log "Report written to: $report_path"
