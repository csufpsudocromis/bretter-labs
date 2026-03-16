# Production Helm Values Reference

Last reviewed: March 16, 2026.

Canonical file:

- [`deploy/helm/values-production.yaml`](../../deploy/helm/values-production.yaml)

Use that file as the baseline and override per environment.

## Chart value model

- The repo chart consumes only `appTemplateValues` from values files.
- Unsupported top-level keys fail Helm template rendering by design.
- `deploy/helm/values-production.yaml` starts with neutral placeholders for environment-specific fields.

## Required production overrides

- `appTemplateValues.CONTROL_NODE`
- `appTemplateValues.NODE_EXTERNAL_HOST`
- `appTemplateValues.CORS_ALLOWED_ORIGINS` (replace default `https://localhost:30073`)
- `appTemplateValues.VM_STORAGE_CLASS`
- `appTemplateValues.TLS_SECRET_NAME`
- `appTemplateValues.PUBLIC_SCHEME`

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
2. Commit environment-specific override files.
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
