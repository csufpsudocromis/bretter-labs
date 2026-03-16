# Setup and Configuration

Last reviewed: March 16, 2026.

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
- `SETUP_PHASES` (default `prereqs,deploy,postdeploy`, or `all`)
- `SETUP_DRY_RUN` (default `0`)
- `CONTROL_NODE`
- `NODE_EXTERNAL_HOST`
- `PUBLIC_SCHEME` (`https` recommended)
- `TLS_ENABLED`, `TLS_SECRET_NAME`

Storage:

- `VM_STORAGE_CLASS`
- `GOLDEN_IMAGES_HOSTPATH`
- `BACKEND_DATA_HOSTPATH`
- `POSTGRES_DATA_HOSTPATH`

Images:

- `BACKEND_IMAGE`
- `FRONTEND_IMAGE`
- `RUNNER_IMAGE`
- `ALLOW_MUTABLE_IMAGE_TAGS` (default `0`; production should stay `0`)

Auth/session/cors:

- `ADMIN_BOOTSTRAP_PASSWORD`
- `BLABS_AUTH_COOKIE_TTL_SECONDS`
- `BLABS_CONNECT_GRANT_TTL_SECONDS`
- `BLABS_CONNECT_SESSION_TTL_SECONDS`
- `BLABS_AUTH_COOKIE_SECURE`, `BLABS_CONNECT_COOKIE_SECURE`
- `BLABS_CORS_ENTERPRISE_PROFILE`
- `BLABS_CORS_ALLOWED_ORIGINS`
- `BLABS_CORS_ALLOWED_METHODS`
- `BLABS_CORS_ALLOWED_HEADERS`
- `BLABS_CORS_ALLOWED_ORIGIN_REGEX` (non-enterprise mode only)

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
- `METRICS_SERVER_INSECURE_TLS` (default `0`; dev-only override)
- `ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL`
- `KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE`
- `RUN_POST_DEPLOY_SYNTHETIC_CHECK`

External secrets (optional):

- `USE_EXTERNAL_SECRETS`
- `EXTERNAL_SECRETS_NAMESPACE`, `EXTERNAL_SECRETS_RELEASE_NAME`
- `EXTERNAL_SECRETS_STORE_NAME`
- Vault settings (`VAULT_ADDR`, `VAULT_K8S_ROLE`, `VAULT_KV_MOUNT`, secret keys)

## Example

```bash
NAMESPACE=labs \
SETUP_PHASES=all \
NODE_EXTERNAL_HOST=<NODE_EXTERNAL_HOST_OR_FQDN> \
PUBLIC_SCHEME=https \
TLS_ENABLED=1 \
VM_STORAGE_CLASS=longhorn-r1 \
BLABS_CORS_ENTERPRISE_PROFILE=1 \
BLABS_CORS_ALLOWED_ORIGINS="https://<UI_HOST>:30073,https://labs.example.edu" \
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
- `ADMIN_BOOTSTRAP_PASSWORD` is only used when no admin user exists; generated random bootstrap secret is one-time and force-reset on first login.
- Enterprise CORS (`BLABS_CORS_ENTERPRISE_PROFILE=1`) requires explicit `BLABS_CORS_ALLOWED_ORIGINS`, blocks `BLABS_CORS_ALLOWED_ORIGIN_REGEX`, and disallows wildcard methods/headers.
- Default image policy rejects mutable refs (for example `:latest`); use immutable tags/digests, or set `ALLOW_MUTABLE_IMAGE_TAGS=1` for explicit dev-only override.
- Setup phases can be run independently via `SETUP_PHASES` (`prereqs`, `deploy`, `postdeploy`, or `all`).
- `SETUP_DRY_RUN=1` performs validation and phase planning without cluster/package changes.
- Production metrics-server should run with `METRICS_SERVER_INSECURE_TLS=0`; use kubelet serving certs with valid SANs (the setup-installed CSR approver helps with future kubelet-serving cert rotation).
- Storage settings page supports clearing overrides back to env defaults.
- Login background should be hosted locally (`/user/site-assets/...`) for reliability.
- LDAP requires backend schema migration `0018` and current frontend bundle to render settings tile.

## Related pages

- [Production Architecture](Production-Architecture)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Operations Runbook](Operations-Runbook)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Security and Auth](Security-and-Auth)
- [LDAP Authentication](LDAP-Authentication)
