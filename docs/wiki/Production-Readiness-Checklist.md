# Production Readiness Checklist

Last reviewed: March 26, 2026.

Use this checklist before first production deployment and for each release.

## Required configuration

- Start from `deploy/helm/values-production-site.template.yaml` and copy it to your environment overlay file.
- Keep `deploy/helm/values-production.yaml` as baseline and define site-specific values in an overlay (for example `deploy/helm/values-prod-site.yaml`).
- Set `appTemplateValues.CONTROL_NODE` in site overlay.
- Set `appTemplateValues.NODE_EXTERNAL_HOST` in site overlay.
- Set `appTemplateValues.RUNNER_NODE_SELECTOR_VALUE` in site overlay.
- Set `appTemplateValues.TEAM_NAMESPACE_MODE=per_team` and `appTemplateValues.TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1`.
- Set `appTemplateValues.CORS_ALLOWED_ORIGINS` to your real UI origins (no localhost/127.0.0.1).
- Set `appTemplateValues.VM_STORAGE_CLASS` to the intended production class.
- Set `appTemplateValues.TLS_SECRET_NAME` to the production certificate secret.
- Ensure `BACKEND_REPLICAS`/`FRONTEND_REPLICAS` are within configured HPA bounds (`*_HPA_MIN_REPLICAS`..`*_HPA_MAX_REPLICAS`).
- Tune `UVICORN_WORKERS` for backend pod CPU/memory limits and expected concurrent sessions.
- Keep `appTemplateValues.SECRETS_ENCRYPTION_KEY` empty in committed production values.
- Set `appTemplateValues.RUNTIME_SECRETS_SECRET_NAME` and `appTemplateValues.RUNTIME_SECRETS_ENCRYPTION_KEY_KEY`.
- Ensure runtime secret `bretter-runtime-secrets` exists with data key `secrets_encryption_key` (or your configured overrides) before/at rollout.
- Set `CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1` (required for production profile).
- Set `CONTAINER_SIGNATURE_KEY_REF` and `CONTAINER_SIGNATURE_KEY_SECRET_NAME` for managed-key verification.
- Verify `bretter-cosign-public-key` matches the expected official key fingerprint before rollout.
- Set `KYVERNO_SIGNATURE_SCOPE=namespace_first_party` and define `KYVERNO_SIGNATURE_IMAGE_PATTERNS`.
- Keep `REQUIRE_SCHEMA_READY=1` in production values/overlays.
- Set `POST_DEPLOY_AUTH_SECRET_NAME` and credential key names for authenticated post-deploy checks.
- Keep `RUN_POST_DEPLOY_SYNTHETIC_CHECK=1` and `SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1` for production profiles.
- Enable RDP connect-latency probe (`ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE=1`) with secret-based auth (`USERFLOW_SLO_API_AUTH_SECRET_NAME`, `USERFLOW_SLO_API_AUTH_*_KEY`, `USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0`).

## Image and supply chain

- Keep `BACKEND_IMAGE`, `BACKEND_ADMIN_IMAGE`, `FRONTEND_IMAGE`, and `RUNNER_IMAGE` digest-pinned (`@sha256:...`).
- Verify digest refs exist in your registry before rollout.
- Keep mutable-tag override disabled (`ALLOW_MUTABLE_IMAGE_TAGS=0`).
- Ensure production image refs are not local/dev references (`localhost/*`, `:local*`, `local-*`).
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
- Configure Alertmanager default receiver/grouping explicitly and wire webhook receiver secrets when external paging is required.

## Validation and operations

- Run CI guardrails (including TLS login smoke path) on release branch/PR.
- Run strict production profile validation before rollout (`-f values-production.yaml -f <site-overlay>.yaml`).
- Validate CRD schema and server-side apply path:
  - `python3 scripts/lint_crd_schema.py`
  - `kubectl apply --dry-run=server -k deploy/crds`
- Run `scripts/production_go_live_proof.sh` after rollout and archive the generated report.
- Keep `RUN_PRODUCTION_GO_LIVE_PROOF=1` for production postdeploy automation (default when `PRODUCTION_PROFILE=1`).
- Ensure CI deploy proof path validates authenticated synthetic VM launch, Guacamole RDP frame, and admin image upload/finalize/delete.
- If using `ORCHESTRATION_BACKEND=dual|crd`, run operator canary:
  - `NAMESPACE=labs CRD_CANARY_TEMPLATE_ID=<template-id> ./scripts/crd_canary_labinstance.sh`
- Run post-deploy API health, admin API smoke, and synthetic checks.
- Ensure release-branch required checks include post-deploy synthetic + restore drill + Playwright RDP smoke workflows.
- Use staged promotion workflow (`.github/workflows/promote-staging-to-production.yml`) to gate production deploy on staging preflight + go-live proof.
- Verify recurring probe CronJobs are healthy (`bretter-ghcr-access-check`, `bretter-slo-vm-launch`, `bretter-slo-rdp-readiness`, `bretter-slo-upload-finalize`).
- Verify pending-path alerts are configured for startup/storage bottlenecks (`BretterVmStartupSlow`, `BretterVmDiskPvcPendingTooLong`) and tune thresholds via `MONITORING_VM_PENDING_MINUTES` + `MONITORING_VM_DISK_PVC_PENDING_MINUTES`.
- Verify backup/restore path for Postgres before go-live:
  - `NAMESPACE=labs ./scripts/restore_drill_postgres.sh`
  - or `NAMESPACE=labs RUN_RESTORE_DRILL=1 ./scripts/production_go_live_proof.sh`
- Keep nightly restore drill strict backup validation enabled (`require_backup_cronjob=true`) so missing backup CronJobs fail the gate.
- Verify `bretter-postgres-backup-replication` CronJob succeeds and encrypted object uploads are present.
- Verify OpenAPI and frontend API type artifacts are up to date:
  - `python3 scripts/check_openapi_drift.py`
  - `npm --prefix frontend-vite run generate:api-types` (no diff expected)
- Confirm rollback plan is documented and executable (`scripts/rollback_release.sh`).

## Related pages

- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Operations Runbook](Operations-Runbook)
- [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)
