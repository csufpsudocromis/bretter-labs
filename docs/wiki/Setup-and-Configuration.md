# Setup and Configuration

Last reviewed: March 20, 2026.

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
- `PRODUCTION_PROFILE` (`1` recommended for production)
- `ORCHESTRATION_BACKEND` (`db`/`dual`/`crd`; default `db`)
- `IMAGE_IMPORT_BACKEND` (`db`/`dual`/`crd`; default `crd`)
- `REQUIRE_SCHEMA_READY` (default `1`; fail startup if DB schema/head is not ready)
- `EXPECTED_ALEMBIC_REVISION` (optional explicit expected Alembic revision)
- `TLS_ENABLED`, `TLS_SECRET_NAME`
- `LABINSTANCE_CRD_GROUP` (default `labs.bretter.io`)
- `LABINSTANCE_CRD_VERSION` (default `v1alpha1`)
- `LABINSTANCE_CRD_PLURAL` (default `labinstances`)
- `LABINSTANCE_CRD_FINALIZER` (default `labs.bretter.io/finalizer`)
- `LABIMAGEIMPORT_CRD_GROUP` (default `labs.bretter.io`)
- `LABIMAGEIMPORT_CRD_VERSION` (default `v1alpha1`)
- `LABIMAGEIMPORT_CRD_PLURAL` (default `labimageimports`)
- `LABIMAGEIMPORT_CRD_FINALIZER` (default `labs.bretter.io/imageimport-finalizer`)
- `LABIMAGEIMPORT_CONTROLLER_ENABLED` (default `1` for `IMAGE_IMPORT_BACKEND=dual|crd`)
- `LABIMAGEIMPORT_CONTROLLER_LEADER_ELECTION_ENABLED` (default `1`)
- `LABIMAGEIMPORT_CONTROLLER_LEASE_NAME` (default `bretter-labimageimport-controller-leader`)
- `LABIMAGEIMPORT_CONTROLLER_LEASE_DURATION_SECONDS` (default `30`)
- `LABIMAGEIMPORT_CONTROLLER_RETRY_PERIOD_SECONDS` (default `5`)
- `LABIMAGEIMPORT_CONTROLLER_POLL_SECONDS` (default `10`)
- `LABIMAGEIMPORT_CONTROLLER_METRICS_BIND` (default `0.0.0.0`)
- `LABIMAGEIMPORT_CONTROLLER_METRICS_PORT` (default `9410`)
- `TEAM_NAMESPACE_MODE` (`shared`/`per_team`; production default `per_team`)
- `TEAM_NAMESPACE_PREFIX` (default `labs-team-` in per-team mode)
- `TEAM_NAMESPACE_BOOTSTRAP_ENABLED` (default `1`; auto-bootstrap per-team runtime namespaces)

Storage:

- `VM_STORAGE_CLASS`
- `GOLDEN_IMAGES_HOSTPATH`
- `BACKEND_DATA_HOSTPATH`
- `POSTGRES_DATA_HOSTPATH`

Images:

- `BACKEND_IMAGE`
- `BACKEND_ADMIN_IMAGE` (defaults to `BACKEND_IMAGE`; used by ops jobs that need admin tooling)
- `FRONTEND_IMAGE`
- `RUNNER_IMAGE`
- `BACKEND_REPLICAS` (default `1`)
- `FRONTEND_REPLICAS` (default `2`)
- `BACKEND_HPA_MIN_REPLICAS` (default `BACKEND_REPLICAS`)
- `BACKEND_HPA_MAX_REPLICAS` (default `BACKEND_REPLICAS`)
- `BACKEND_HPA_TARGET_CPU_UTILIZATION_PERCENT` (default `70`)
- `FRONTEND_HPA_MIN_REPLICAS` (default `FRONTEND_REPLICAS`)
- `FRONTEND_HPA_MAX_REPLICAS` (default `FRONTEND_REPLICAS`)
- `FRONTEND_HPA_TARGET_CPU_UTILIZATION_PERCENT` (default `70`)
- `UVICORN_WORKERS` (default `1`; backend process concurrency per pod)
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
- `SECRETS_ENCRYPTION_KEY` (optional bootstrap input; setup writes to runtime secret when provided)
- `RUNTIME_SECRETS_SECRET_NAME` (default `bretter-runtime-secrets`)
- `RUNTIME_SECRETS_ENCRYPTION_KEY_KEY` (default `secrets_encryption_key`)
- `CONTAINER_SIGNATURE_KEY_REF` (required when signature verification is enabled)
- `CONTAINER_SIGNATURE_KEY_SECRET_NAME` (default `bretter-cosign-public-key` for `/etc/bretter-signing/*` key refs)
- `CONTAINER_SIGNATURE_PUBLIC_KEY` (optional inline setup input to create/update signature key secret)
- `CONTAINER_SIGNATURE_PUBLIC_KEY_FILE` (optional setup input to create/update signature key secret)

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
- `KYVERNO_SIGNATURE_SCOPE` (`namespace_first_party` recommended for production)
- `KYVERNO_SIGNATURE_IMAGE_PATTERNS` (default `ghcr.io/csufpsudocromis/*`)
- `KYVERNO_SIGNATURE_REGISTRY_SECRET_NAME` (default `ghcr-creds`; synced into `KYVERNO_NAMESPACE` for `verifyImages` auth)
- `KYVERNO_SIGNATURE_REGISTRY_SECRET_SOURCE_NAMESPACE` (default app namespace; source for Kyverno registry-credential sync)
- `MONITORING_CHART_VERSION` (default `v82.10.4`)
- `ALERTMANAGER_DEFAULT_RECEIVER_NAME`
- `ALERTMANAGER_ROUTE_GROUP_BY`, `ALERTMANAGER_ROUTE_GROUP_WAIT`, `ALERTMANAGER_ROUTE_GROUP_INTERVAL`, `ALERTMANAGER_ROUTE_REPEAT_INTERVAL`
- `ALERTMANAGER_WEBHOOK_RECEIVER_ENABLED`
- `ALERTMANAGER_WEBHOOK_SECRET_NAME`, `ALERTMANAGER_WEBHOOK_SECRET_KEY`
- `ALERTMANAGER_WEBHOOK_MATCHERS`
- `ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL`
- `KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE`
- `RUN_POST_DEPLOY_API_HEALTH_CHECK`
- `POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS`
- `RUN_POST_DEPLOY_ADMIN_API_SMOKE_CHECK`
- `POST_DEPLOY_ADMIN_API_SMOKE_TIMEOUT_SECONDS`
- `ADMIN_API_SMOKE_USERNAME`, `ADMIN_API_SMOKE_PASSWORD`
- `RUN_POST_DEPLOY_SYNTHETIC_CHECK`
- `SYNTHETIC_CHECK_REQUIRE_TEMPLATES` (set `1` to require launchable templates during synthetic checks)
- `SYNTHETIC_CHECK_USERNAME`, `SYNTHETIC_CHECK_PASSWORD`
- `POST_DEPLOY_AUTH_SECRET_NAME`
- `POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY`, `POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY`
- `POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY`, `POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY`
- `RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK`
- `POST_DEPLOY_RUNNER_SMOKE_TIMEOUT_SECONDS`
- `POST_DEPLOY_RUNNER_SMOKE_IMAGE_PULL_POLICY`
- `ENABLE_POSTGRES_BACKUP_AUTOMATION`
- `POSTGRES_BACKUP_SCHEDULE`
- `POSTGRES_BACKUP_RETENTION_DAYS`
- `POSTGRES_BACKUP_PVC_NAME`, `POSTGRES_BACKUP_PVC_SIZE`
- `POSTGRES_BACKUP_STORAGE_CLASS`, `POSTGRES_BACKUP_MOUNT_PATH`
- `POSTGRES_BACKUP_IMAGE`
- `ENABLE_POSTGRES_BACKUP_REPLICATION`
- `POSTGRES_BACKUP_REPLICATION_SCHEDULE`
- `POSTGRES_BACKUP_REPLICATION_BUCKET`, `POSTGRES_BACKUP_REPLICATION_PREFIX`
- `POSTGRES_BACKUP_REPLICATION_REGION`, `POSTGRES_BACKUP_REPLICATION_ENDPOINT`
- `POSTGRES_BACKUP_REPLICATION_SECRET_NAME`
- `POSTGRES_BACKUP_REPLICATION_ACCESS_KEY_ID_KEY`, `POSTGRES_BACKUP_REPLICATION_SECRET_ACCESS_KEY_KEY`, `POSTGRES_BACKUP_REPLICATION_SESSION_TOKEN_KEY`
- `POSTGRES_BACKUP_REPLICATION_SSE_MODE`, `POSTGRES_BACKUP_REPLICATION_SSE_KMS_KEY_ID`
- `POSTGRES_BACKUP_REPLICATION_IMAGE`
- `ENABLE_GHCR_ACCESS_HEALTHCHECK`
- `GHCR_ACCESS_HEALTHCHECK_SCHEDULE`
- `GHCR_ACCESS_HEALTHCHECK_TIMEOUT_SECONDS`
- `GHCR_ACCESS_HEALTHCHECK_IMAGE_PULL_SECRET`
- `MONITORING_VM_PENDING_MINUTES` (default `10`; warning threshold for VM pods stuck pending/not-ready)
- `MONITORING_VM_DISK_PVC_PENDING_MINUTES` (default `8`; warning threshold for `vm-disk-*` PVCs stuck pending)
- `ENABLE_USERFLOW_SLO_PROBES`
- `USERFLOW_SLO_PROBE_SCHEDULE`
- `USERFLOW_SLO_LOOKBACK_MINUTES`
- `USERFLOW_SLO_VM_LAUNCH_FAILURE_RATE_PCT`
- `USERFLOW_SLO_RDP_STUCK_MINUTES`
- `USERFLOW_SLO_RDP_STUCK_MAX`
- `USERFLOW_SLO_RDP_FAILURE_RATE_PCT`
- `USERFLOW_SLO_UPLOAD_FINALIZE_FAILURE_RATE_PCT`
- `USERFLOW_SLO_IMAGE_IMPORT_QUEUE_MAX_AGE_MINUTES`
- `USERFLOW_SLO_IMAGE_IMPORT_QUEUE_FAILURE_RATE_PCT`
- `ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE`
- `USERFLOW_SLO_RDP_CONNECT_LATENCY_SECONDS`
- `USERFLOW_SLO_RDP_CONNECT_FAILURE_RATE_PCT`
- `USERFLOW_SLO_API_BASE`, `USERFLOW_SLO_API_VERIFY_TLS`
- `USERFLOW_SLO_API_USERNAME`, `USERFLOW_SLO_API_PASSWORD`
- `USERFLOW_SLO_API_AUTH_SECRET_NAME`, `USERFLOW_SLO_API_AUTH_USERNAME_KEY`, `USERFLOW_SLO_API_AUTH_PASSWORD_KEY`
- `USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP`
- `RUN_PRODUCTION_GO_LIVE_PROOF` (defaults to `PRODUCTION_PROFILE`)
- `PRODUCTION_GO_LIVE_REPORT_DIR`
- `PRODUCTION_GO_LIVE_HEALTH_TIMEOUT_SECONDS`
- `RUN_RESTORE_DRILL` (optional go-live proof extension)
- `RESTORE_DRILL_KEEP_DB` (optional keep restored drill DB)

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
- Production profile rejects localhost/127.0.0.1 CORS origins; set real UI origins.
- Production profile requires `RUNNER_NODE_SELECTOR_VALUE`.
- Production profile now also requires explicit non-placeholder `CONTROL_NODE`, `NODE_EXTERNAL_HOST`, and `VM_STORAGE_CLASS`.
- Production profile requires `TEAM_NAMESPACE_MODE=per_team` and `TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1`.
- Keep `SECRETS_ENCRYPTION_KEY` empty in committed production values and inject the runtime key via `RUNTIME_SECRETS_SECRET_NAME`/`RUNTIME_SECRETS_ENCRYPTION_KEY_KEY`.
- Production profile requires `CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1` with a non-empty `CONTAINER_SIGNATURE_KEY_REF`.
- If `CONTAINER_SIGNATURE_KEY_REF` uses `/etc/bretter-signing/*`, ensure secret `CONTAINER_SIGNATURE_KEY_SECRET_NAME` contains that key file.
- Default image policy rejects mutable refs (for example `:latest`); use immutable tags/digests, or set `ALLOW_MUTABLE_IMAGE_TAGS=1` for explicit dev-only override.
- `PRODUCTION_PROFILE=1` additionally requires digest-pinned backend/frontend/runner image refs (`@sha256`).
- `PRODUCTION_PROFILE=1` rejects local/dev image references (`localhost/*`, `:local*`, `local-*`) for backend/frontend/runner.
- Setup no longer falls back to `:latest` when `VERSION` is invalid; fix `VERSION` or set explicit immutable image refs.
- Production values baseline (`deploy/helm/values-production.yaml`) is digest-pinned and CI-enforced for backend/frontend/runner image refs.
- Use `deploy/helm/values-production-site.template.yaml` to create site overlays (for example `deploy/helm/values-prod-site.yaml`) and validate with `-f` layering.
- Setup phases can be run independently via `SETUP_PHASES` (`prereqs`, `deploy`, `postdeploy`, or `all`).
- `SETUP_DRY_RUN=1` performs validation and phase planning without cluster/package changes.
- Use `python3 scripts/validate_production_profile.py --strict -f deploy/helm/values-production.yaml` before production rollouts (add additional `-f <site-values>.yaml` overlays when used).
- Use `NAMESPACE=labs ./scripts/deploy_preflight.sh` before rollout to enforce merged-values validation, secret wiring, and per-node image pullability.
- `postdeploy` now runs `scripts/production_go_live_proof.sh` automatically when `RUN_PRODUCTION_GO_LIVE_PROOF=1` (default in production profile).
- `postdeploy` also runs a runner image startup smoke pod by default (`RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK=1`).
- Production metrics-server should run with `METRICS_SERVER_INSECURE_TLS=0`; use kubelet serving certs with valid SANs (the setup-installed CSR approver helps with future kubelet-serving cert rotation).
- Post-deploy API smoke validation now checks `https://<NODE_EXTERNAL_HOST>:30073/api/health` (or `http://...` when `PUBLIC_SCHEME=http`).
- Post-deploy admin API smoke validation now runs by default (`RUN_POST_DEPLOY_ADMIN_API_SMOKE_CHECK=1`) using authenticated `/admin/*` read paths.
- Setup deploy phase now installs recurring GHCR access checks (`bretter-ghcr-access-check`) and user-flow SLO probe CronJobs (`bretter-slo-vm-launch`, `bretter-slo-rdp-readiness`, `bretter-slo-upload-finalize`).
- User-flow SLO alerting now evaluates burn-rate (failure ratio) for VM launch, RDP readiness, and upload finalize probes.
- `IMAGE_IMPORT_BACKEND=dual|crd` requires the dedicated LabImageImport controller (`scripts/smoke_labimageimport_controller.sh` validates startup/metrics behavior).
- Admission policy apply now includes Kyverno `verifyImages` signature checks when `CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1` (Audit in non-production, Enforce in production).
- For non-production unsigned/local-image testing, use a scoped Kyverno exception instead of relaxing global policy:
  `NAMESPACE=labs MODE=apply ./scripts/apply_dev_signature_exception.sh`
  and label only intended pods with `security.bretter-labs.io/allow-unsigned-dev=true`.
- Remove that exception after testing:
  `NAMESPACE=labs MODE=delete ./scripts/apply_dev_signature_exception.sh`
- CI includes an explicit PostgreSQL Alembic gate (`scripts/check_alembic_postgres.sh`) to catch dialect-specific migration failures before deploy.
- API contract drift is guarded by checked-in OpenAPI snapshot + generated frontend types (`scripts/check_openapi_drift.py` + `npm --prefix frontend-vite run generate:api-types`).
- Use `scripts/bootstrap_team_namespace.sh` to scaffold per-team namespaces with quota and default network policies.
- Use `scripts/restore_drill_postgres.sh` to validate PostgreSQL logical restore, optionally from `scripts/production_go_live_proof.sh` via `RUN_RESTORE_DRILL=1`.
- For image publish workflows, set repo Actions secrets `GHCR_USERNAME` and `GHCR_PAT` (`write:packages`) when publishing to pre-existing private GHCR packages; workflow falls back to `GITHUB_TOKEN` when those secrets are absent.
- Admin container image registration uses direct OCI image references; if signature verification returns `no signatures found`, registration continues with warning-only policy messaging.
- In production profile, post-deploy authenticated checks require explicit non-bootstrap credentials (`ADMIN_API_SMOKE_PASSWORD`/`SYNTHETIC_CHECK_PASSWORD`) or secret-backed auth via `POST_DEPLOY_AUTH_SECRET_NAME`.
- Production profile requires `RUN_POST_DEPLOY_SYNTHETIC_CHECK=1` and `SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1`.
- In non-production profiles, if setup generated a new bootstrap admin secret and explicit check credentials were not set, setup can auto-disable authenticated checks to avoid false failures.
- To run synthetic validation on existing deployments, set `SYNTHETIC_CHECK_PASSWORD` explicitly (and `SYNTHETIC_CHECK_USERNAME` if not `admin`).
- For production RDP connect-latency probe auth, use pre-provisioned secret mode: `USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0`.
- After first-login reset, verify bootstrap env pruning: `kubectl -n labs get deploy bretter-backend -o yaml | grep BLABS_ADMIN_DEFAULT_PASSWORD` should return nothing.
- User-facing VM launch readiness preflight endpoint: `GET /user/templates/{template_id}/preflight`.
- When admission policies are enabled, setup installs/applies Kyverno policies that enforce immutable tags, non-root security context, dropped capabilities, and CPU/memory requests+limits for labeled Bretter core workloads.
- Storage settings page supports clearing overrides back to env defaults.
- Login background should be hosted locally (`/user/site-assets/...`) for reliability.
- LDAP requires backend schema at current Alembic head and a current frontend bundle to render settings tile.

## Related pages

- [Production Architecture](Production-Architecture)
- [Production Readiness Checklist](Production-Readiness-Checklist)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Operations Runbook](Operations-Runbook)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Alert Routing and Receiver Defaults](Alert-Routing-and-Receiver-Defaults)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Tenant Isolation and Namespaces](Tenant-Isolation-and-Namespaces)
- [Restore Drill and Backup SOP](Restore-Drill-and-Backup-SOP)
- [API Contract and Drift Guardrails](API-Contract-and-Drift-Guardrails)
- [Security and Auth](Security-and-Auth)
- [LDAP Authentication](LDAP-Authentication)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Console Providers and RDP Operations](Console-Providers-and-RDP-Operations)
