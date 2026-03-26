#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_NAMESPACE="${SOURCE_NAMESPACE:-labs}"
POSTGRES_SELECTOR="${POSTGRES_SELECTOR:-app=bretter-postgres}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-postgres}"
RESTORE_IMAGE="${RESTORE_IMAGE:-postgres:16}"
TEMP_NAMESPACE_PREFIX="${TEMP_NAMESPACE_PREFIX:-labs-restore-drill-}"
KEEP_RESTORE_NAMESPACE="${KEEP_RESTORE_NAMESPACE:-0}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-300}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/restore-drill-clean-namespace}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
suffix="$(date -u +%s)"
temp_namespace="${TEMP_NAMESPACE_PREFIX}${suffix}"
temp_namespace="${temp_namespace:0:63}"
temp_namespace="${temp_namespace%-}"
report_path="${REPORT_DIR}/clean-namespace-restore-drill-${timestamp}.txt"
tmp_dump_file="$(mktemp /tmp/blabs-clean-restore.XXXXXX.dump)"

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

cleanup() {
  rm -f "$tmp_dump_file"
  if [ "$KEEP_RESTORE_NAMESPACE" != "1" ]; then
    kubectl delete namespace "$temp_namespace" --ignore-not-found=true >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

case "$KEEP_RESTORE_NAMESPACE" in
  0 | 1) ;;
  *)
    echo "ERROR: KEEP_RESTORE_NAMESPACE must be 0 or 1." >&2
    exit 1
    ;;
esac
if [[ ! "$WAIT_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || [ "$WAIT_TIMEOUT_SECONDS" -lt 30 ]; then
  echo "ERROR: WAIT_TIMEOUT_SECONDS must be an integer >= 30." >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  fail "kubectl is required."
fi

source_postgres_pod="$(kubectl -n "$SOURCE_NAMESPACE" get pod -l "$POSTGRES_SELECTOR" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$source_postgres_pod" ]; then
  fail "No postgres pod found in source namespace ${SOURCE_NAMESPACE} (selector=${POSTGRES_SELECTOR})."
fi

postgres_user="$(kubectl -n "$SOURCE_NAMESPACE" get secret bretter-postgres -o jsonpath='{.data.POSTGRES_USER}' 2>/dev/null | base64 -d || true)"
postgres_password="$(kubectl -n "$SOURCE_NAMESPACE" get secret bretter-postgres -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d || true)"
postgres_db="$(kubectl -n "$SOURCE_NAMESPACE" get secret bretter-postgres -o jsonpath='{.data.POSTGRES_DB}' 2>/dev/null | base64 -d || true)"
if [ -z "$postgres_user" ] || [ -z "$postgres_password" ] || [ -z "$postgres_db" ]; then
  fail "Missing postgres credentials in ${SOURCE_NAMESPACE}/bretter-postgres."
fi

restore_db="bretter_restore_drill_${timestamp,,}"
restore_pod_name="restore-drill-postgres"

log "Clean-namespace restore drill started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Source namespace: $SOURCE_NAMESPACE"
log "Source pod: $source_postgres_pod"
log "Temporary namespace: $temp_namespace"
log "Restore image: $RESTORE_IMAGE"
log "Restore DB: $restore_db"

kubectl create namespace "$temp_namespace" >/dev/null
kubectl -n "$temp_namespace" create secret generic bretter-postgres \
  --from-literal=POSTGRES_USER="$postgres_user" \
  --from-literal=POSTGRES_PASSWORD="$postgres_password" \
  --from-literal=POSTGRES_DB="$postgres_db" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl -n "$temp_namespace" apply -f - <<EOF >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${restore_pod_name}
  namespace: ${temp_namespace}
  labels:
    app.kubernetes.io/part-of: bretter-labs
    app.kubernetes.io/component: restore-drill
spec:
  restartPolicy: Never
  containers:
    - name: postgres
      image: ${RESTORE_IMAGE}
      imagePullPolicy: IfNotPresent
      env:
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: bretter-postgres
              key: POSTGRES_USER
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: bretter-postgres
              key: POSTGRES_PASSWORD
        - name: POSTGRES_DB
          valueFrom:
            secretKeyRef:
              name: bretter-postgres
              key: POSTGRES_DB
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
      ports:
        - containerPort: 5432
      readinessProbe:
        exec:
          command:
            - /bin/sh
            - -c
            - pg_isready -U "\$POSTGRES_USER" -d postgres
        periodSeconds: 5
        timeoutSeconds: 3
        failureThreshold: 20
      livenessProbe:
        exec:
          command:
            - /bin/sh
            - -c
            - pg_isready -U "\$POSTGRES_USER" -d postgres
        periodSeconds: 10
        timeoutSeconds: 3
        failureThreshold: 6
      volumeMounts:
        - name: pgdata
          mountPath: /var/lib/postgresql/data
  volumes:
    - name: pgdata
      emptyDir: {}
EOF

if ! kubectl -n "$temp_namespace" wait --for=condition=Ready "pod/${restore_pod_name}" --timeout="${WAIT_TIMEOUT_SECONDS}s" >/dev/null; then
  kubectl -n "$temp_namespace" describe pod "$restore_pod_name" >>"$report_path" 2>&1 || true
  fail "Temporary restore postgres pod did not become Ready in ${temp_namespace}."
fi

log "Creating logical dump from source postgres..."
if ! kubectl -n "$SOURCE_NAMESPACE" exec "$source_postgres_pod" -c "$POSTGRES_CONTAINER" -- sh -c \
  'set -eu; export PGPASSWORD="$POSTGRES_PASSWORD"; pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$tmp_dump_file"; then
  fail "Failed to create logical dump from source postgres pod."
fi
if [ ! -s "$tmp_dump_file" ]; then
  fail "Logical dump file is empty."
fi

log "Uploading logical dump into temporary namespace..."
cat "$tmp_dump_file" | kubectl -n "$temp_namespace" exec -i "$restore_pod_name" -- sh -c 'cat > /tmp/restore.dump'

log "Restoring dump into temporary postgres..."
kubectl -n "$temp_namespace" exec "$restore_pod_name" -- sh -s "$restore_db" <<'SH'
set -eu
restore_db="$1"
export PGPASSWORD="$POSTGRES_PASSWORD"
psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$restore_db\";" >/dev/null
psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$restore_db\";" >/dev/null
pg_restore -U "$POSTGRES_USER" -d "$restore_db" /tmp/restore.dump >/tmp/restore.log 2>&1
SH

verification="$(
  kubectl -n "$temp_namespace" exec "$restore_pod_name" -- sh -s "$restore_db" <<'SH'
set -eu
restore_db="$1"
export PGPASSWORD="$POSTGRES_PASSWORD"
table_count="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
alembic_revision="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT version_num FROM alembic_version LIMIT 1;")"
user_count="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT count(*) FROM \"user\";")"
echo "table_count=${table_count}"
echo "alembic_revision=${alembic_revision}"
echo "user_count=${user_count}"
SH
)"

table_count=""
alembic_revision=""
user_count=""
while IFS='=' read -r key value; do
  case "$key" in
    table_count) table_count="$value" ;;
    alembic_revision) alembic_revision="$value" ;;
    user_count) user_count="$value" ;;
  esac
done <<<"$verification"

if [ -z "$table_count" ] || [ "$table_count" -lt 1 ] 2>/dev/null; then
  fail "Restore verification failed: invalid table_count (${table_count:-empty})."
fi
if [ -z "$alembic_revision" ]; then
  fail "Restore verification failed: missing alembic revision."
fi
if [ -z "$user_count" ] || [ "$user_count" -lt 0 ] 2>/dev/null; then
  fail "Restore verification failed: invalid user_count (${user_count:-empty})."
fi

log "PASS: clean-namespace restore drill completed."
log "table_count=${table_count}"
log "alembic_revision=${alembic_revision}"
log "user_count=${user_count}"
if [ "$KEEP_RESTORE_NAMESPACE" = "1" ]; then
  log "restore_namespace_retained=${temp_namespace}"
fi
log "Report written to: $report_path"
