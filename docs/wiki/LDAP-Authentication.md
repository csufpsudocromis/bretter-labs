# LDAP Authentication

Last reviewed: March 9, 2026.

LDAP is optional and is used as a fallback after local auth.

Login order:

1. Local user/password check.
2. If local auth fails and LDAP is enabled, backend binds/searches LDAP.
3. If LDAP succeeds, backend can auto-create a local user record (configurable).

## Admin UI location

- `/admin/settings/ldap`

If you do not see the LDAP tile under `/admin/settings`, confirm backend/frontend rollout and clear browser cache (`Ctrl+Shift+R`).

## Required fields

- `LDAP Server URI` (example: `ldaps://ldap.example.edu:636`)
- `Bind DN`
- `Bind Password`
- `User Base DN`
- `User Search Filter` (must include `{username}`)

Optional:

- `StartTLS`
- `Skip TLS certificate verification` (only for non-production troubleshooting)
- `LDAP timeout (seconds)`
- `Auto-create local users on first LDAP login`

## Kubernetes/runtime checklist

- Backend migrations at head (`0018` or newer):
  - `kubectl -n labs exec deploy/bretter-postgres -- psql -U bretter -d bretterlabs -c "select version_num from alembic_version;"`
- Backend pods healthy:
  - `kubectl -n labs rollout status deploy/bretter-backend --timeout=300s`
- Frontend pods healthy:
  - `kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s`

## Common issues

### LDAP tile missing

- Cause: stale frontend bundle or incomplete rollout.
- Fix: hard refresh browser; verify frontend image/pod rollout.

### LDAP login fails but local login works

- Cause: bind/search settings incorrect or TLS handshake/cert mismatch.
- Check backend logs:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'ldap|bind|search|tls|auth'
```

### LDAP migration crash on Postgres

- Cause: bad boolean default SQL in migration.
- Fix: run backend image containing migration fix commit `f9234fd` or later.

## Related pages

- [Security and Auth](Security-and-Auth)
- [Setup and Configuration](Setup-and-Configuration)
- [Error Catalog](Error-Catalog)
