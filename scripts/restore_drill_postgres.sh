#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
POSTGRES_SELECTOR="${POSTGRES_SELECTOR:-app=bretter-postgres}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-postgres}"
KEEP_RESTORE_DB="${KEEP_RESTORE_DB:-0}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/restore-drill}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="${REPORT_DIR}/postgres-restore-drill-${timestamp}.txt"
mkdir -p "$REPORT_DIR"
touch "$report_path"

log() {
  printf '%s\n' "$*" | tee -a "$report_path"
}

case "$KEEP_RESTORE_DB" in
  0 | 1) ;;
  *)
    echo "ERROR: KEEP_RESTORE_DB must be 0 or 1." >&2
    exit 1
    ;;
esac

postgres_pod="$(kubectl -n "$NAMESPACE" get pod -l "$POSTGRES_SELECTOR" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [ -z "$postgres_pod" ]; then
  echo "ERROR: no postgres pod found in namespace $NAMESPACE for selector $POSTGRES_SELECTOR." >&2
  exit 1
fi

restore_db="bretter_restore_drill_${timestamp,,}"

log "Postgres restore drill started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: $NAMESPACE"
log "Pod: $postgres_pod"
log "Restore DB: $restore_db"

drill_output="$(
  kubectl -n "$NAMESPACE" exec "$postgres_pod" -c "$POSTGRES_CONTAINER" -- sh -s "$restore_db" "$KEEP_RESTORE_DB" <<'SH'
set -eu
restore_db="$1"
keep_db="$2"
dump_path="/tmp/${restore_db}.dump"
restore_log="/tmp/${restore_db}.restore.log"

if [ -z "${POSTGRES_USER:-}" ] || [ -z "${POSTGRES_PASSWORD:-}" ] || [ -z "${POSTGRES_DB:-}" ]; then
  echo "ERROR=missing_postgres_env"
  exit 1
fi

export PGPASSWORD="$POSTGRES_PASSWORD"
pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc >"$dump_path"
psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$restore_db\";" >/dev/null
psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$restore_db\";" >/dev/null
pg_restore -U "$POSTGRES_USER" -d "$restore_db" "$dump_path" >"$restore_log" 2>&1

table_count="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
alembic_revision="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT version_num FROM alembic_version LIMIT 1;")"
user_count="$(psql -U "$POSTGRES_USER" -d "$restore_db" -Atc "SELECT count(*) FROM \"user\";")"

echo "table_count=${table_count}"
echo "alembic_revision=${alembic_revision}"
echo "user_count=${user_count}"

if [ "$keep_db" != "1" ]; then
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$restore_db\";" >/dev/null
fi
rm -f "$dump_path" "$restore_log"
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
done <<<"$drill_output"

if [ -z "$table_count" ] || [ "$table_count" -lt 1 ] 2>/dev/null; then
  log "FAIL: restore drill produced invalid table_count (${table_count:-empty})."
  exit 1
fi
if [ -z "$alembic_revision" ]; then
  log "FAIL: restore drill did not recover alembic revision."
  exit 1
fi
if [ -z "$user_count" ] || [ "$user_count" -lt 0 ] 2>/dev/null; then
  log "FAIL: restore drill produced invalid user_count (${user_count:-empty})."
  exit 1
fi

log "PASS: postgres logical restore drill completed."
log "table_count=${table_count}"
log "alembic_revision=${alembic_revision}"
log "user_count=${user_count}"
if [ "$KEEP_RESTORE_DB" = "1" ]; then
  log "restore_db_retained=${restore_db}"
fi
log "Report written to: $report_path"
