# Security and Auth

Last reviewed: March 6, 2026.

## Authentication model

- Login uses secure session cookies (`HttpOnly`) rather than storing auth tokens in browser localStorage.
- `/auth/login` sets auth cookie and returns user profile metadata.
- `/auth/logout` revokes server token and clears auth cookie.

Default cookie names:

- Auth session: `blabs_session`
- Container connect grant: `blabs_connect_grant`
- Container connect session: `blabs_connect_session`

## Connect token flow (container connect)

Connect flow is short-lived and split in two stages:

1. UI calls `/user/containers/{id}/connect-token`.
2. Backend issues one-time grant token cookie.
3. Connect endpoint consumes grant token and mints session token cookie.
4. Web and websocket proxy calls require valid connect session cookie.

Security properties:

- Grant token is one-time use.
- Grant/session cookies are scoped to connect path.
- Session TTL is short-lived and configurable.

## Relevant backend settings

Environment prefix: `BLABS_`

- `AUTH_COOKIE_NAME`
- `AUTH_COOKIE_TTL_SECONDS`
- `AUTH_COOKIE_SECURE`
- `AUTH_COOKIE_SAMESITE`
- `CONNECT_GRANT_TTL_SECONDS`
- `CONNECT_SESSION_TTL_SECONDS`
- `CONNECT_COOKIE_SECURE`
- `CONNECT_COOKIE_SAMESITE`

## Login page background asset behavior

- Background image can be uploaded in admin appearance settings.
- Asset is stored locally and served via `/user/site-assets/<filename>`.
- This avoids external dependency failures on login page render.

## Operational recommendations

- Keep TLS enabled for all public access.
- Keep cookie `secure=true` in production.
- Rotate/revoke leaked GitHub PATs immediately.
- Review admin actions and access regularly.

## Related pages

- [Operations Runbook](Operations-Runbook.md)
- [Setup and Configuration](Setup-and-Configuration.md)
