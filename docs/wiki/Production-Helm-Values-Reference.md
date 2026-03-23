# Production Helm Values Reference

Last reviewed: March 20, 2026.

Canonical file:

- [`deploy/helm/values-production.yaml`](../../deploy/helm/values-production.yaml)
- Overlay template: [`deploy/helm/values-production-site.template.yaml`](../../deploy/helm/values-production-site.template.yaml)

Use `values-production.yaml` as the reusable hardened baseline and layer a site-specific override for each environment.

## Chart value model

- The repo chart consumes only `appTemplateValues` from values files.
- Unsupported top-level keys fail Helm template rendering by design.
- `deploy/helm/values-production.yaml` is intentionally non-secret and serves as a hardened baseline with reference coordinates.
- Override site-specific coordinates in your overlay file (`values-production-site.template.yaml` -> `values-prod-site.yaml`).
- Use `deploy/helm/values-production-site.template.yaml` to create a site overlay with real coordinates and secret object names.

## Required production overrides

- `appTemplateValues.CONTROL_NODE`
- `appTemplateValues.NODE_EXTERNAL_HOST`
- `appTemplateValues.RUNNER_NODE_SELECTOR_VALUE`
- `appTemplateValues.CORS_ALLOWED_ORIGINS`
- `appTemplateValues.VM_STORAGE_CLASS`
- `appTemplateValues.TLS_SECRET_NAME`
- `appTemplateValues.RUNTIME_SECRETS_SECRET_NAME`
- `appTemplateValues.RUNTIME_SECRETS_ENCRYPTION_KEY_KEY`
- `appTemplateValues.IMAGE_IMPORT_BACKEND` should remain `crd` for controller-first image import reconciliation
- `appTemplateValues.LABIMAGEIMPORT_CONTROLLER_ENABLED` should remain `"1"` in production profile
- `appTemplateValues.CONTAINER_SIGNATURE_KEY_REF`
- `appTemplateValues.CONTAINER_SIGNATURE_KEY_SECRET_NAME`
- `appTemplateValues.KYVERNO_SIGNATURE_SCOPE` (`namespace_first_party` for production)
- `appTemplateValues.KYVERNO_SIGNATURE_IMAGE_PATTERNS` (first-party image patterns to enforce)
- `appTemplateValues.TEAM_NAMESPACE_MODE` (`per_team` recommended for stronger tenant isolation)
- `appTemplateValues.TEAM_NAMESPACE_PREFIX` (team namespace naming convention)
- `appTemplateValues.TEAM_NAMESPACE_BOOTSTRAP_ENABLED` should remain `"1"` in production profile
- `appTemplateValues.BACKEND_REPLICAS`
- `appTemplateValues.FRONTEND_REPLICAS`
- `appTemplateValues.BACKEND_HPA_MIN_REPLICAS`
- `appTemplateValues.BACKEND_HPA_MAX_REPLICAS`
- `appTemplateValues.BACKEND_HPA_TARGET_CPU_UTILIZATION_PERCENT`
- `appTemplateValues.FRONTEND_HPA_MIN_REPLICAS`
- `appTemplateValues.FRONTEND_HPA_MAX_REPLICAS`
- `appTemplateValues.FRONTEND_HPA_TARGET_CPU_UTILIZATION_PERCENT`
- `appTemplateValues.UVICORN_WORKERS`
- `appTemplateValues.PUBLIC_SCHEME`
- `appTemplateValues.PRODUCTION_PROFILE` should remain `"1"` in production
- `appTemplateValues.REQUIRE_SCHEMA_READY` should remain `"1"` in production
- `appTemplateValues.SECRETS_ENCRYPTION_KEY` should remain empty in committed production values (inject runtime secret at deploy time)
- `appTemplateValues.POST_DEPLOY_AUTH_SECRET_NAME` and credential key names for authenticated postdeploy checks
- `appTemplateValues.RUN_POST_DEPLOY_SYNTHETIC_CHECK` should remain `"1"` in production profile
- `appTemplateValues.SYNTHETIC_CHECK_REQUIRE_TEMPLATES` should remain `"1"` in production profile
- `appTemplateValues.ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE` should remain `"1"` with secret-backed auth keys
- `appTemplateValues.MONITORING_VM_PENDING_MINUTES` / `appTemplateValues.MONITORING_VM_DISK_PVC_PENDING_MINUTES` tuned for your storage and node startup SLO

## Image pinning policy

- `appTemplateValues.BACKEND_IMAGE`, `BACKEND_ADMIN_IMAGE`, `FRONTEND_IMAGE`, and `RUNNER_IMAGE` must remain digest-pinned (`@sha256:...`).
- Production refs must not use local/dev patterns (`localhost/*`, `:local*`, `local-*`).
- This is CI-enforced by `scripts/check_release_discipline.py`.
- `scripts/setup.sh` also enforces digest pinning when `PRODUCTION_PROFILE=1`.

## Key sections and intent

- `appTemplateValues` includes:
  - deployment coordinates (`NAMESPACE`, `CONTROL_NODE`, `NODE_EXTERNAL_HOST`)
  - image refs (`BACKEND_IMAGE`, `BACKEND_ADMIN_IMAGE`, `FRONTEND_IMAGE`, `RUNNER_IMAGE`)
  - backend/frontend autoscaling controls (`*_HPA_MIN/MAX_REPLICAS`, `*_HPA_TARGET_CPU_UTILIZATION_PERCENT`)
  - backend process-level concurrency (`UVICORN_WORKERS`)
  - TLS/public URL controls (`PUBLIC_SCHEME`, `TLS_SECRET_NAME`)
  - auth/cors hardening values
  - runtime/storage/network options consumed by `deploy/helm/files/app.yaml.tpl`

## Usage pattern

1. Copy the site template into an environment overlay.
2. Commit environment-specific override files without raw secret values.
3. Create/inject runtime secrets (`RUNTIME_SECRETS_SECRET_NAME`, signature key secret) at deploy time.
4. Validate production profile with baseline + overlay in order.
5. Deploy with explicit values files in order.
6. Run rollout status + post-deploy checks (API health, admin API smoke, synthetic check).
7. Run `scripts/deploy_preflight.sh` to validate secrets and per-node image pullability.
8. Archive go-live proof output.
9. Optional: include restore drill via `RUN_RESTORE_DRILL=1`.

Example:

```bash
cp deploy/helm/values-production-site.template.yaml deploy/helm/values-prod-site.yaml
```

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

```bash
NAMESPACE=labs ./scripts/deploy_preflight.sh
```

```bash
helm upgrade --install bretter-labs ./deploy/helm \
  -n labs \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

`setup.sh` equivalent:

```bash
PRODUCTION_PROFILE=1 SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh
```

When `PRODUCTION_PROFILE=1`, setup automatically runs go-live proof in postdeploy by default (`RUN_PRODUCTION_GO_LIVE_PROOF=1`).

## Related pages

- [Production Architecture](Production-Architecture)
- [Production Readiness Checklist](Production-Readiness-Checklist)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Secret Operations Runbook](Secret-Operations-Runbook)
