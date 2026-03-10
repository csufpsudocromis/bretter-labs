# Security and Auth

Last reviewed: March 9, 2026.

## Authentication model

- Login uses secure session cookies (`HttpOnly`) instead of localStorage tokens.
- `/auth/login` sets auth cookie and returns user profile metadata.
- `/auth/logout` revokes server token and clears auth cookie.
- Optional LDAP authentication can be configured in `/admin/settings/ldap` (local auth attempted first, then LDAP).

Default cookie names:

- Auth session: `blabs_session`
- Connect grant: `blabs_connect_grant`
- Connect session: `blabs_connect_session`

## Connect token flow (VM + container)

Connect flow is short-lived and split in two stages:

1. UI requests `/user/.../connect-token`.
2. Backend issues one-time connect grant cookie.
3. Connect endpoint consumes grant and mints connect session cookie.
4. Proxy/websocket calls require valid connect session cookie.

Security properties:

- Grant token is one-time use.
- Grant/session cookies are path-scoped to connect routes.
- Session TTL is server-enforced.

## RBAC model

Roles:

- `user`
- `viewer`
- `image_manager`
- `template_manager`
- `lab_operator`
- `platform_admin`

Permissions are enforced on admin/API routes (read/write split for users, templates, images, operations, settings).

## OIDC SSO

OIDC is optional and uses authorization code + PKCE.

Required SSO config fields:

- `sso_client_id`
- `sso_authorize_url`
- `sso_token_url`
- `sso_userinfo_url`
- `sso_redirect_url`

Behavior:

- Login state is stored server-side with short TTL.
- Callback exchanges code and creates/updates local user.
- Session cookie is then issued using the normal auth flow.

## LDAP auth

LDAP is optional and runs only if local auth fails.

Settings page:

- `/admin/settings/ldap`

Notes:

- `ldap_user_filter` must include `{username}`.
- Use `ldaps://` in production where possible.
- Keep skip-verify disabled unless troubleshooting non-production cert issues.
- LDAP settings changes are dynamic in DB config; backend restart is not required after save.

## CORS and login origin policy

If login/API calls must work from LAN IPs and campus domains, set explicit allowed UI origins.

Examples:

```bash
BLABS_CORS_ALLOWED_ORIGINS="https://<UI_HOST>:30073,https://labs.example.edu"
BLABS_CORS_ALLOWED_ORIGIN_REGEX="^https://([a-z0-9-]+\\.)?example\\.edu(:[0-9]+)?$"
```

Notes:

- Include the **frontend origin** (for example `:30073`), not only API origin (`:30080`).
- Keep `BLABS_AUTH_COOKIE_SECURE=1` and `BLABS_CONNECT_COOKIE_SECURE=1` when using HTTPS.

## API docs exposure

OpenAPI/docs endpoints are disabled in non-dev by default.

- `BLABS_API_DOCS_ENABLED=0` (recommended for production)

## Related pages

- [Production Architecture](Production-Architecture.md)
- [Hardened Deployment Guide](Hardened-Deployment-Guide.md)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive.md)
- [Operations Runbook](Operations-Runbook.md)
- [Scaling and Quotas](Scaling-and-Quotas.md)
- [Pentest Plan and Checklist](Pentest-Plan-and-Checklist.md)
- [Setup and Configuration](Setup-and-Configuration.md)
- [LDAP Authentication](LDAP-Authentication.md)
