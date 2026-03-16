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
- `PRUNE_BOOTSTRAP_ADMIN_ENV` (default `1`; removes bootstrap admin secret env from running backend deployment after initial rollout)

Auth/session/cors:

- `ADMIN_BOOTSTRAP_PASSWORD`
- `BACKEND_NODEPORT_ENABLED` (default `0`, keep disabled for hardened deployments)
- `CORS_ENTERPRISE_PROFILE`
- `CORS_ALLOWED_ORIGINS`
- `CORS_ALLOWED_METHODS`
- `CORS_ALLOWED_HEADERS`
- `CORS_ALLOWED_ORIGIN_REGEX` (non-enterprise mode only)
- `AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS`
- `AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS`
- `AUTH_LOGIN_LOCKOUT_SECONDS`
- `SECRETS_ENCRYPTION_KEY` (recommended in production)

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
- `METRICS_SERVER_VERSION` (default `v0.8.1`; used to pin manifest URL)
- `ENABLE_ADMISSION_POLICIES` (default `1`)
- `INSTALL_KYVERNO` (default `1`)
- `KYVERNO_NAMESPACE`, `KYVERNO_RELEASE_NAME`, `KYVERNO_CHART_VERSION` (default `v3.7.1`)
- `MONITORING_CHART_VERSION` (default `v82.10.4`)
- `ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL`
- `KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE`
- `RUN_POST_DEPLOY_API_HEALTH_CHECK`
- `POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS`
- `RUN_POST_DEPLOY_SYNTHETIC_CHECK`
- `SYNTHETIC_CHECK_USERNAME`, `SYNTHETIC_CHECK_PASSWORD`

External secrets (optional):

- `USE_EXTERNAL_SECRETS`
- `EXTERNAL_SECRETS_NAMESPACE`, `EXTERNAL_SECRETS_RELEASE_NAME`
- `EXTERNAL_SECRETS_CHART_VERSION` (default `v2.1.0`)
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
CORS_ENTERPRISE_PROFILE=1 \
CORS_ALLOWED_ORIGINS="https://<UI_HOST>:30073,https://labs.example.edu" \
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
- Backend startup is fail-fast if no admin user exists and `ADMIN_BOOTSTRAP_PASSWORD` is empty.
- By default, setup prunes `BLABS_ADMIN_DEFAULT_PASSWORD` from the backend deployment after rollout to avoid long-lived bootstrap secrets in pod specs.
- Generated bootstrap secrets are written to `~/.config/bretter-labs/bootstrap-admin-<timestamp>.txt` (`600`).
- Enterprise CORS (`CORS_ENTERPRISE_PROFILE=1`) requires explicit `CORS_ALLOWED_ORIGINS`, blocks `CORS_ALLOWED_ORIGIN_REGEX`, and disallows wildcard methods/headers.
- Default image policy rejects mutable refs (for example `:latest`); use immutable tags/digests, or set `ALLOW_MUTABLE_IMAGE_TAGS=1` for explicit dev-only override.
- Setup no longer falls back to `:latest` when `VERSION` is invalid; fix `VERSION` or set explicit immutable image refs.
- Production values (`deploy/helm/values-production.yaml`) are digest-pinned and CI-enforced for backend/frontend/runner image refs.
- Setup phases can be run independently via `SETUP_PHASES` (`prereqs`, `deploy`, `postdeploy`, or `all`).
- `SETUP_DRY_RUN=1` performs validation and phase planning without cluster/package changes.
- Production metrics-server should run with `METRICS_SERVER_INSECURE_TLS=0`; use kubelet serving certs with valid SANs (the setup-installed CSR approver helps with future kubelet-serving cert rotation).
- Post-deploy API smoke validation now checks `https://<NODE_EXTERNAL_HOST>:30073/api/health` (or `http://...` when `PUBLIC_SCHEME=http`).
- If setup generated a new bootstrap admin secret and `SYNTHETIC_CHECK_PASSWORD` is not set, setup auto-disables the authenticated synthetic check to avoid login failures against existing admin credentials.
- To run synthetic validation on existing deployments, set `SYNTHETIC_CHECK_PASSWORD` explicitly (and `SYNTHETIC_CHECK_USERNAME` if not `admin`).
- When admission policies are enabled, setup installs/applies Kyverno policies that enforce immutable tags, non-root security context, dropped capabilities, and CPU/memory requests+limits for labeled Bretter core workloads.
- Storage settings page supports clearing overrides back to env defaults.
- Login background should be hosted locally (`/user/site-assets/...`) for reliability.
- LDAP requires backend schema migration `0018` and current frontend bundle to render settings tile.

## Related pages

- [Production Architecture](Production-Architecture)
- [Production Readiness Checklist](Production-Readiness-Checklist)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Operations Runbook](Operations-Runbook)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Security and Auth](Security-and-Auth)
- [LDAP Authentication](LDAP-Authentication)
