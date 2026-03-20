# Security and Auth

Last reviewed: March 19, 2026.

## Authentication model

- Session auth uses secure HttpOnly cookies (no localStorage auth tokens).
- `/auth/login` issues auth cookie and returns user profile.
- `/auth/logout` revokes server token and clears auth cookie.
- Optional LDAP fallback is available via `/admin/settings/ldap` (local auth first, LDAP second).

Default cookie names:

- Auth session: `blabs_session`
- Connect grant: `blabs_connect_grant`
- Connect session: `blabs_connect_session`

## Bootstrap admin lifecycle

- Default bootstrap username: `admin`.
- Initial deployment requires one-time bootstrap secret when no admin exists.
- Setup generates random bootstrap secret unless `ADMIN_BOOTSTRAP_PASSWORD` is provided.
- Generated secret is written to `~/.config/bretter-labs/bootstrap-admin-<timestamp>.txt` with mode `600`.
- First login requires password reset (`force_password_change=true`).
- Backend startup fails fast if no admin exists and bootstrap secret is empty.
- Setup prunes bootstrap env from backend deployment after rollout (`PRUNE_BOOTSTRAP_ADMIN_ENV=1` by default).

## Token/connect security

Connect flow:

1. UI requests `/user/.../connect-token`.
2. Backend issues one-time connect grant cookie.
3. Connect endpoint consumes grant and mints connect session cookie.
4. Proxy/WS calls require valid connect session cookie.

Security properties:

- Grant token is one-time and short-lived.
- Connect cookies are path-scoped.
- Connect session TTL is server-enforced.
- Session/connect tokens are hashed before DB storage.
- Legacy plaintext token rows are migrated; plaintext lookup fallback is removed.

## RBAC model

Roles:

- `user`
- `viewer`
- `image_manager`
- `template_manager`
- `lab_operator`
- `platform_admin`

Permissions are enforced on admin/API routes with read/write separation.

## OIDC SSO (implemented)

OIDC uses authorization code + PKCE and is configured in `/admin/settings/sso`.

Core fields:

- `sso_client_id`
- `sso_authorize_url`
- `sso_token_url`
- `sso_userinfo_url`
- `sso_redirect_url`

Role mapping controls:

- `sso_role_claim` (default `groups`)
- `sso_default_role`
- `sso_role_mappings_json`
- `sso_auto_create_users`
- `sso_sync_roles_on_login`

Behavior:

- Login state is stored server-side with short TTL.
- Callback exchanges code for tokens, resolves identity claims, applies role mapping, then issues normal session cookie.
- Sensitive fields (for example client secret) are treated as write-only in admin APIs.

SAML status:

- SAML is not yet implemented in runtime; keep SAML as a follow-on adapter track.

## LDAP auth

- Optional; attempted only after local auth failure.
- Configured in `/admin/settings/ldap`.
- `ldap_user_filter` must include `{username}`.
- Prefer `ldaps://` in production; keep skip-verify disabled outside controlled troubleshooting.
- LDAP settings are stored in config and do not require backend restart after save.

## Secrets and encryption controls

- In production profile, runtime must provide `BLABS_SECRETS_ENCRYPTION_KEY`.
- Committed production values should keep plaintext key empty and use runtime secret injection.
- Signature verification key must be mounted and referenced when signature verification is enabled.
- Stored sensitive admin values (for example LDAP bind password, SSO client secret, template RDP defaults) are encrypted at rest through backend secret-encryption path.

## CORS and cookie safety

For hardened deployments:

```bash
BLABS_CORS_ENTERPRISE_PROFILE=1
BLABS_CORS_ALLOWED_ORIGINS="https://<UI_HOST>:30073,https://labs.example.edu"
BLABS_CORS_ALLOWED_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS"
BLABS_CORS_ALLOWED_HEADERS="Accept,Content-Type,Authorization"
```

Rules:

- Include actual frontend origin(s), not localhost placeholders in production.
- Keep `BLABS_AUTH_COOKIE_SECURE=1` and `BLABS_CONNECT_COOKIE_SECURE=1` on HTTPS.
- Enterprise mode disallows `BLABS_CORS_ALLOWED_ORIGIN_REGEX`.

## Login throttling and audit

Rate-limit controls:

- `BLABS_AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS` (default `5`)
- `BLABS_AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` (default `300`)
- `BLABS_AUTH_LOGIN_LOCKOUT_SECONDS` (default `300`)

Audit coverage:

- Login success/failure/logout/SSO callback events
- Admin mutations (templates/images/quotas/settings/error-log clear)
- Query via `/admin/audit-events`

## API docs exposure

- Keep API docs disabled in production:
  - `BLABS_API_DOCS_ENABLED=0`

## Related pages

- [Production Architecture](Production-Architecture)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Operations Runbook](Operations-Runbook)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Setup and Configuration](Setup-and-Configuration)
- [LDAP Authentication](LDAP-Authentication)
