# Upgrade and Rollback

Last reviewed: March 19, 2026.

## Quick path

1. Back up database and current values.
2. Prepare target values overlay and validate production profile:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

3. Deploy:

```bash
PRODUCTION_PROFILE=1 SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh
```

4. Validate rollout, synthetic checks, and go-live proof report.

## Release evidence checklist

Capture and archive:

1. Previous release version + target version.
2. DB backup identifier/snapshot time.
3. Exact values files used for deploy.
4. `production_go_live_proof.sh` report output.
5. Synthetic validation job logs.
6. Any migration warnings and remediation notes.

## Token migration behavior

- Alembic `0019` migrates legacy plaintext rows in `token` and `connecttoken` to hashed storage keys.
- Plaintext lookup fallback is removed.
- Existing session/connect cookies remain valid after migration because backend computes deterministic storage keys from raw cookie values.

## Rollback constraints

- `0019` is non-reversible; hashed token values cannot be reconstructed to plaintext.
- For rollback across schema boundaries, restore from pre-upgrade DB backup.
- Prefer forward-fix deployments where possible.

## Rollback quick procedure

Preferred path:

```bash
NAMESPACE=labs ./scripts/rollback_release.sh
```

Optional explicit target revision:

```bash
TARGET_REVISION=12 NAMESPACE=labs ./scripts/rollback_release.sh
```

Manual fallback:

1. Roll back backend/frontend image digests in values overlay.
2. Re-run deploy phase with pinned prior digests.
3. If schema incompatibility is detected, restore DB backup taken before upgrade.
4. Re-run postdeploy validation and go-live proof.

## Related pages

- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Operations Runbook](Operations-Runbook)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Error Catalog](Error-Catalog)
