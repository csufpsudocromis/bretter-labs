# Upgrade Path

Last reviewed: March 16, 2026.

This document covers safe upgrades between Bretter Labs releases, including the token-storage migration that removes plaintext lookup fallback.

## Scope

- Backend/API and database schema upgrades
- Helm values and setup script changes
- Production safety checks before and after rollout

## Pre-upgrade checklist

1. Back up database and configuration.
2. Capture running image refs and Helm values used by the current release.
3. Confirm no unresolved critical alerts.
4. Prepare the target values files and run:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

## Upgrade procedure

1. Pull latest main/tagged release.
2. Deploy with safe atomic workflow (recommended) or setup.

```bash
NAMESPACE=labs \
HELM_RELEASE_NAME=bretter-labs \
BASE_VALUES_FILE=deploy/helm/values-production.yaml \
SITE_VALUES_FILE=deploy/helm/values-prod-site.yaml \
./scripts/deploy_production_safe.sh
```

Alternative:

```bash
PRODUCTION_PROFILE=1 SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh
```

3. Confirm rollout health and API health endpoint.
4. Generate a go-live proof report if you did not run `deploy_production_safe.sh`:

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```
5. Run post-deploy synthetic validation using explicit credentials for existing deployments:

```bash
SETUP_PHASES=postdeploy \
RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 \
SYNTHETIC_CHECK_USERNAME=admin \
SYNTHETIC_CHECK_PASSWORD='<EXISTING_ADMIN_PASSWORD>' \
./scripts/setup.sh
```
6. Validate backup retention policy:

```bash
NAMESPACE=labs ./scripts/validate_backup_retention.sh
```

## Token storage migration notes

- Legacy plaintext rows in `token` and `connecttoken` are migrated to hashed storage keys by Alembic migration `0019`.
- Plaintext fallback lookups are removed after migration.
- Existing raw session/connect cookie values continue to work because lookup computes the same deterministic storage key.
- If migrations are skipped, legacy plaintext rows will not authenticate.

## Rollback notes

- Roll back application images/chart only after confirming DB compatibility.
- Migration `0019` is intentionally non-reversible (hashed keys cannot be converted back to plaintext rows).
- If rollback crosses incompatible schema boundaries, restore from pre-upgrade backup.

## Post-upgrade validation

1. `/api/health` returns `{"status":"ok"}`.
2. Login, VM launch/connect/delete, container launch/connect/delete.
3. No new repeating auth or storage errors in backend logs.
4. Archive `artifacts/go-live/production-go-live-*.txt` output with release notes.
