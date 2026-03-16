# Production Helm Values Reference

Last reviewed: March 16, 2026.

Canonical file:

- [`deploy/helm/values-production.yaml`](../../deploy/helm/values-production.yaml)
- Overlay template: [`deploy/helm/values-production-site.template.yaml`](../../deploy/helm/values-production-site.template.yaml)

Use `values-production.yaml` as the reusable hardened baseline and layer a site-specific override for each environment.

## Chart value model

- The repo chart consumes only `appTemplateValues` from values files.
- Unsupported top-level keys fail Helm template rendering by design.
- `deploy/helm/values-production.yaml` is intentionally non-secret and environment-neutral.
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
- `appTemplateValues.CONTAINER_SIGNATURE_KEY_REF`
- `appTemplateValues.CONTAINER_SIGNATURE_KEY_SECRET_NAME`
- `appTemplateValues.PUBLIC_SCHEME`
- `appTemplateValues.PRODUCTION_PROFILE` should remain `"1"` in production
- `appTemplateValues.SECRETS_ENCRYPTION_KEY` should remain empty in committed production values (inject runtime secret at deploy time)

## Image pinning policy

- `appTemplateValues.BACKEND_IMAGE`, `FRONTEND_IMAGE`, and `RUNNER_IMAGE` must remain digest-pinned (`@sha256:...`).
- This is CI-enforced by `scripts/check_release_discipline.py`.

## Key sections and intent

- `appTemplateValues` includes:
  - deployment coordinates (`NAMESPACE`, `CONTROL_NODE`, `NODE_EXTERNAL_HOST`)
  - image refs (`BACKEND_IMAGE`, `FRONTEND_IMAGE`, `RUNNER_IMAGE`)
  - TLS/public URL controls (`PUBLIC_SCHEME`, `TLS_SECRET_NAME`)
  - auth/cors hardening values
  - runtime/storage/network options consumed by `deploy/helm/files/app.yaml.tpl`

## Usage pattern

1. Copy the site template into an environment overlay.
2. Commit environment-specific override files without raw secret values.
3. Create/inject runtime secrets (`RUNTIME_SECRETS_SECRET_NAME`, signature key secret) at deploy time.
4. Validate production profile with baseline + overlay in order.
5. Deploy with explicit values files in order.
6. Run rollout status + post-deploy synthetic check (set `SYNTHETIC_CHECK_PASSWORD` explicitly on existing deployments).
7. Archive go-live proof output.

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
