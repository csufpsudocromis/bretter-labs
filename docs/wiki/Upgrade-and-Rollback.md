# Upgrade and Rollback

Last reviewed: March 16, 2026.

## Quick path

1. Back up database and current values.
2. Validate target production profile:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

3. Deploy:

```bash
SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh
```

4. Validate rollout and synthetic checks.

## Token migration behavior

- Alembic `0019` migrates legacy plaintext rows in `token` and `connecttoken` to hashed storage keys.
- Plaintext lookup fallback is removed.
- Existing session/connect cookies remain valid after migration because backend computes deterministic storage keys from raw cookie values.

## Rollback constraints

- `0019` is non-reversible; hashed token values cannot be reconstructed to plaintext.
- For rollback across schema boundaries, restore from pre-upgrade DB backup.
- Prefer forward-fix deployments where possible.

## Related pages

- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Operations Runbook](Operations-Runbook)
- [Error Catalog](Error-Catalog)
