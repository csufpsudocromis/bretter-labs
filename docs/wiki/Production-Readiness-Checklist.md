# Production Readiness Checklist

Last reviewed: March 16, 2026.

Use this checklist before first production deployment and for each release.

## Required configuration

- Set `appTemplateValues.CONTROL_NODE` in `deploy/helm/values-production.yaml`.
- Set `appTemplateValues.NODE_EXTERNAL_HOST` in `deploy/helm/values-production.yaml`.
- Set `appTemplateValues.RUNNER_NODE_SELECTOR_VALUE` in `deploy/helm/values-production.yaml`.
- Set `appTemplateValues.CORS_ALLOWED_ORIGINS` to your real UI origins (no localhost/127.0.0.1).
- Set `appTemplateValues.VM_STORAGE_CLASS` to the intended production class.
- Set `appTemplateValues.TLS_SECRET_NAME` to the production certificate secret.
- Set a strong `SECRETS_ENCRYPTION_KEY` before production rollout.

## Image and supply chain

- Keep `BACKEND_IMAGE`, `FRONTEND_IMAGE`, and `RUNNER_IMAGE` digest-pinned (`@sha256:...`).
- Verify digest refs exist in your registry before rollout.
- Keep mutable-tag override disabled (`ALLOW_MUTABLE_IMAGE_TAGS=0`).
- Keep chart/tool versions pinned (for example `MONITORING_CHART_VERSION`, `KYVERNO_CHART_VERSION`, `EXTERNAL_SECRETS_CHART_VERSION`, `METRICS_SERVER_VERSION`).

## Auth/bootstrap

- Use one-time bootstrap admin secret for first deployment only.
- Keep `PRUNE_BOOTSTRAP_ADMIN_ENV=1` so bootstrap secret is removed from running backend pod specs.
- Confirm first-login password reset flow (`force_password_change=true`) is functioning.
- Capture generated bootstrap secret file (`~/.config/bretter-labs/bootstrap-admin-<timestamp>.txt`) into secure credential storage.
- After first-login reset, verify backend deployment env no longer includes `BLABS_ADMIN_DEFAULT_PASSWORD`.

## Network and runtime hardening

- Keep `CORS_ENTERPRISE_PROFILE=1` with explicit origin allowlist.
- Keep `METRICS_SERVER_INSECURE_TLS=0` for production.
- Keep `VM_CONNECT_INSECURE_TLS=0` and `CONTAINER_CONNECT_INSECURE_TLS=0`.
- Verify admission policies are enabled and enforced.

## Validation and operations

- Run CI guardrails (including TLS login smoke path) on release branch/PR.
- Run post-deploy API health and synthetic checks.
- Verify backup/restore path for Postgres before go-live.
- Confirm rollback plan is documented for backend/frontend image digest rollbacks.
