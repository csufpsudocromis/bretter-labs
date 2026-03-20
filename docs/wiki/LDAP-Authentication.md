# LDAP Authentication

Last reviewed: March 19, 2026.

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

- Backend migrations at head:
  - `kubectl -n labs exec deploy/bretter-postgres -- psql -U bretter -d bretterlabs -c "select version_num from alembic_version;"`
- Backend pods healthy:
  - `kubectl -n labs rollout status deploy/bretter-backend --timeout=300s`
- Frontend pods healthy:
  - `kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s`
- Admin API readable:
  - `curl -sk https://<UI_HOST>:30073/api/user/settings/sso` (or equivalent local path through frontend proxy)

## Common issues

### LDAP tile missing

- Cause: stale frontend bundle or incomplete rollout.
- Fix: hard refresh browser; verify frontend image/pod rollout.

### LDAP login fails but local login works

- Cause: bind/search settings incorrect or TLS handshake/cert mismatch.
- Check backend logs:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'ldap|bind|search|tls|auth|certificate'
```

### Login appears blocked after enabling LDAP

- Cause: rate-limit lockout after repeated failed attempts.
- Check:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -i 'lockout|rate limit|ldap'
```

- Wait lockout window or adjust:
  - `BLABS_AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS`
  - `BLABS_AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS`
  - `BLABS_AUTH_LOGIN_LOCKOUT_SECONDS`

## Related pages

- [Security and Auth](Security-and-Auth)
- [Setup and Configuration](Setup-and-Configuration)
- [Error Catalog](Error-Catalog)
