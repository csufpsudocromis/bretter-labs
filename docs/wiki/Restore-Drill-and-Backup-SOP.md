# Restore Drill and Backup SOP

Last reviewed: March 20, 2026.

## Goal

Prove PostgreSQL logical restore viability on a running cluster before and after major releases.

## Run the drill

```bash
NAMESPACE=labs ./scripts/restore_drill_postgres.sh
```

Outputs:

- report file under `artifacts/restore-drill/postgres-restore-drill-<timestamp>.txt`
- PASS/FAIL summary with:
  - restored table count
  - restored Alembic revision
  - restored user table row count

## Keep restored DB for manual inspection (optional)

```bash
NAMESPACE=labs KEEP_RESTORE_DB=1 ./scripts/restore_drill_postgres.sh
```

When enabled, report includes `restore_db_retained=<name>`.

## Integrate with go-live proof

`production_go_live_proof.sh` now supports optional restore check:

```bash
NAMESPACE=labs RUN_RESTORE_DRILL=1 ./scripts/production_go_live_proof.sh
```

Optional:

```bash
NAMESPACE=labs RUN_RESTORE_DRILL=1 RESTORE_DRILL_KEEP_DB=1 ./scripts/production_go_live_proof.sh
```

## Off-cluster encrypted backup replication (optional)

Setup can deploy `bretter-postgres-backup-replication` to copy the latest dump to S3-compatible object storage with SSE.

Key settings:

- `ENABLE_POSTGRES_BACKUP_REPLICATION=1`
- `POSTGRES_BACKUP_REPLICATION_BUCKET`
- `POSTGRES_BACKUP_REPLICATION_SECRET_NAME`
- `POSTGRES_BACKUP_REPLICATION_SSE_MODE` (`AES256` or `aws:kms`)

Quick verification:

```bash
kubectl -n labs get cronjob bretter-postgres-backup-replication
kubectl -n labs get jobs --sort-by=.metadata.creationTimestamp | rg bretter-postgres-backup-replication
kubectl -n labs logs job/<latest-replication-job> --all-containers=true
```

Expected log marker:

- `backup_replicated path=s3://...`

## Failure handling

If drill fails:

1. Inspect report + pod logs.
2. Verify `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` are present in postgres pod env.
3. Validate persistent storage health (`kubectl -n labs get pvc,pv` and storage backend status).
4. Re-run drill after remediation and archive successful report with release artifacts.
