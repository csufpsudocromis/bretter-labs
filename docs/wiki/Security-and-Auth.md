# Security and Auth

Last reviewed: March 16, 2026.

## Authentication model

- Login uses secure session cookies (`HttpOnly`) instead of localStorage tokens.
- `/auth/login` sets auth cookie and returns user profile metadata.
- `/auth/logout` revokes server token and clears auth cookie.
- Optional LDAP authentication can be configured in `/admin/settings/ldap` (local auth attempted first, then LDAP).

Bootstrap admin behavior:

- Username defaults to `admin`.
- Setup uses a one-time bootstrap secret (generated random unless `ADMIN_BOOTSTRAP_PASSWORD` is set).
- Bootstrap secret is only used when no admin user exists.
- Generated bootstrap secret is written to `~/.config/bretter-labs/bootstrap-admin-<timestamp>.txt` (`600`).
- First login requires password reset (`force_password_change=true`).

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
- LDAP bind password and SSO client secret are write-only in admin APIs and are not returned by read endpoints.
- If `BLABS_SECRETS_ENCRYPTION_KEY` is configured, these stored secrets are encrypted at rest.

## Login rate limiting and audit events

- Login failures are rate-limited by backend settings:
  - `BLABS_AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS` (default `5`)
  - `BLABS_AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` (default `300`)
  - `BLABS_AUTH_LOGIN_LOCKOUT_SECONDS` (default `300`)
- Successful and failed login/logout/SSO callback events are logged with source, username, and request IP.

## CORS and login origin policy

If login/API calls must work from LAN IPs and campus domains, set explicit allowed UI origins.
For hardened deployments, use enterprise CORS mode.

Examples:

```bash
BLABS_CORS_ENTERPRISE_PROFILE=1
BLABS_CORS_ALLOWED_ORIGINS="https://<UI_HOST>:30073,https://labs.example.edu"
BLABS_CORS_ALLOWED_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS"
BLABS_CORS_ALLOWED_HEADERS="Accept,Content-Type,Authorization"
```

Notes:

- Include the **frontend origin** (for example `:30073`, where `/api` is proxied).
- Keep `BLABS_AUTH_COOKIE_SECURE=1` and `BLABS_CONNECT_COOKIE_SECURE=1` when using HTTPS.
- In enterprise mode, `BLABS_CORS_ALLOWED_ORIGIN_REGEX` is not allowed.
- In non-enterprise mode, regex-based origins remain available for dev/legacy compatibility.

## API docs exposure

OpenAPI/docs endpoints are disabled in non-dev by default.

- `BLABS_API_DOCS_ENABLED=0` (recommended for production)

## Related pages

- [Production Architecture](Production-Architecture)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Operations Runbook](Operations-Runbook)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Pentest Plan and Checklist](Pentest-Plan-and-Checklist)
- [Setup and Configuration](Setup-and-Configuration)
- [LDAP Authentication](LDAP-Authentication)
