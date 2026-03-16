# Production Helm Values Reference

Last reviewed: March 16, 2026.

Canonical file:

- [`deploy/helm/values-production.yaml`](../../deploy/helm/values-production.yaml)

Use that file as the baseline and override per environment.

## Chart value model

- The repo chart consumes only `appTemplateValues` from values files.
- Unsupported top-level keys fail Helm template rendering by design.
- `deploy/helm/values-production.yaml` now carries concrete production defaults for the primary cluster.
- For another environment, layer a site override file and run strict validation before rollout.

## Required production overrides

- `appTemplateValues.CONTROL_NODE`
- `appTemplateValues.NODE_EXTERNAL_HOST`
- `appTemplateValues.RUNNER_NODE_SELECTOR_VALUE`
- `appTemplateValues.CORS_ALLOWED_ORIGINS`
- `appTemplateValues.VM_STORAGE_CLASS`
- `appTemplateValues.TLS_SECRET_NAME`
- `appTemplateValues.SECRETS_ENCRYPTION_KEY`
- `appTemplateValues.PUBLIC_SCHEME`
- `appTemplateValues.PRODUCTION_PROFILE` should remain `"1"` in production

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

1. Copy production values into an environment overlay.
2. Commit environment-specific override files (excluding secret material when using private overlays).
3. Deploy with explicit values files in order.
4. Run rollout status + post-deploy synthetic check (set `SYNTHETIC_CHECK_PASSWORD` explicitly on existing deployments).

Example:

```bash
helm upgrade --install bretter-labs ./deploy/helm \
  -n labs \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

## Related pages

- [Production Architecture](Production-Architecture)
- [Production Readiness Checklist](Production-Readiness-Checklist)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
