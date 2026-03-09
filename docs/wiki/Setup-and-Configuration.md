# Setup and Configuration

Last reviewed: March 9, 2026.

## Quick install

```bash
git clone https://github.com/csufpsudocromis/bretter-labs.git
cd bretter-labs
./scripts/setup.sh
```

`setup.sh` deploys base application resources through Helm (`helm upgrade --install`) and keeps cleanup/monitoring/operator wiring in scripted steps.

## High-value setup variables

All values are read by `scripts/setup.sh` and/or backend env.

Core:

- `NAMESPACE` (default `labs`)
- `HELM_RELEASE_NAME` (default `bretter-labs`)
- `HELM_CHART_DIR` (default `deploy/helm`)
- `CONTROL_NODE`
- `NODE_EXTERNAL_HOST`
- `PUBLIC_SCHEME` (`https` recommended)
- `TLS_ENABLED`, `TLS_SECRET_NAME`

Storage:

- `VM_STORAGE_CLASS`
- `GOLDEN_IMAGES_HOSTPATH`
- `BACKEND_DATA_HOSTPATH`
- `POSTGRES_DATA_HOSTPATH`

Auth/session/cors:

- `BLABS_AUTH_COOKIE_TTL_SECONDS`
- `BLABS_CONNECT_GRANT_TTL_SECONDS`
- `BLABS_CONNECT_SESSION_TTL_SECONDS`
- `BLABS_AUTH_COOKIE_SECURE`, `BLABS_CONNECT_COOKIE_SECURE`
- `BLABS_CORS_ALLOWED_ORIGINS`
- `BLABS_CORS_ALLOWED_ORIGIN_REGEX`

OIDC/SSO:

- `BLABS_API_DOCS_ENABLED` (keep `0` in production)
- SSO settings are configured in `/admin/settings/sso` and stored in app config.

Container runtime:

- `CONTAINER_INGRESS_ENABLED`
- `CONTAINER_INGRESS_BASE_DOMAIN`
- `CONTAINER_ALLOWED_REGISTRIES`
- `CONTAINER_SCAN_ENABLED`
- `CONTAINER_START_QUEUE_ENABLED`

VM runtime:

- `WINDOWS_EFI_ENABLED`, `WINDOWS_MACHINE_TYPE`
- `LINUX_EFI_ENABLED`, `LINUX_MACHINE_TYPE`
- `VM_NET_BACKEND`
- `CPU_MANAGER_STATIC`

Monitoring/ops:

- `ENABLE_MONITORING`
- `ENABLE_AUTOCLEANUP`
- `AUTOCLEANUP_NODEFS_WARN_PCT`, `AUTOCLEANUP_NODEFS_CRITICAL_PCT`, `AUTOCLEANUP_NODEFS_EMERGENCY_PCT`
- `AUTOCLEANUP_PVC_WARN_PCT`, `AUTOCLEANUP_PVC_CRITICAL_PCT`, `AUTOCLEANUP_PVC_EMERGENCY_PCT`
- `RUN_POST_DEPLOY_SYNTHETIC_CHECK`

External secrets (optional):

- `USE_EXTERNAL_SECRETS`
- `EXTERNAL_SECRETS_NAMESPACE`, `EXTERNAL_SECRETS_RELEASE_NAME`
- `EXTERNAL_SECRETS_STORE_NAME`
- Vault settings (`VAULT_ADDR`, `VAULT_K8S_ROLE`, `VAULT_KV_MOUNT`, secret keys)

## Example

```bash
NAMESPACE=labs \
NODE_EXTERNAL_HOST=10.68.49.250 \
PUBLIC_SCHEME=https \
TLS_ENABLED=1 \
VM_STORAGE_CLASS=longhorn-r1 \
BLABS_CORS_ALLOWED_ORIGINS="https://10.68.49.250:30073,https://labs.fullerton.edu" \
ENABLE_MONITORING=1 \
./scripts/setup.sh
```

## Runtime/admin settings pages

- `/admin/settings/storage`: writable storage overrides + validation
- `/admin/settings/runtime`: read-only runtime/env/drift visibility
- `/admin/settings/appearance`: theme, contrast targets, background upload, font sizing
- `/admin/settings/sso`: SSO provider config
- `/admin/settings/ldap`: LDAP bind/search settings
- `/admin/scaling-quotas`: namespace quota controls for lab count/cpu/ram/storage/idle cap

## Notes

- Runtime settings page is read-only by design.
- Storage settings page supports clearing overrides back to env defaults.
- Login background should be hosted locally (`/user/site-assets/...`) for reliability.
- LDAP requires backend schema migration `0018` and current frontend bundle to render settings tile.

## Related pages

- [Production Architecture](Production-Architecture.md)
- [Hardened Deployment Guide](Hardened-Deployment-Guide.md)
- [Operations Runbook](Operations-Runbook.md)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP.md)
- [Scaling and Quotas](Scaling-and-Quotas.md)
- [Security and Auth](Security-and-Auth.md)
- [LDAP Authentication](LDAP-Authentication.md)
