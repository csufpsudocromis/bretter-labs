# Operations Runbook

Last reviewed: April 2, 2026.

## Production pre-rollout gate

Validate production profile values before deployment:

```bash
python3 scripts/check_release_discipline.py
python3 scripts/validate_production_profile.py --strict -f deploy/helm/values-production.yaml
./scripts/audit_tenant_isolation.sh
python3 scripts/check_openapi_drift.py
```

If you use environment overlays, include them in validation order:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

## Baseline checks

Confirm context/namespace first:

```bash
kubectl config current-context
kubectl get ns labs
```

Core platform checks:

```bash
kubectl -n labs get deploy bretter-backend bretter-frontend bretter-postgres
kubectl -n labs get pods -o wide
kubectl -n labs logs deploy/bretter-backend --tail=200
kubectl -n labs logs deploy/bretter-frontend --tail=200
```

One-command websocket/connect diagnostics:

```bash
NAMESPACE=labs ./scripts/diagnose_connectivity.sh
```

This captures websocket metrics (`blabs_ws_proxy_*`), backend websocket log sample, and monitoring rule wiring in one reportable pass.

Bootstrap env pruning check (after first bootstrap rollout):

```bash
kubectl -n labs get deploy bretter-backend -o yaml | rg BLABS_ADMIN_DEFAULT_PASSWORD
```

Expected: no output.

Runtime secret wiring checks:

```bash
kubectl -n labs get secret bretter-runtime-secrets -o go-template='{{index .data "secrets_encryption_key"}}' | wc -c
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | wc -c
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | base64 -d | sha256sum
```

Expected:

- First two commands print a value greater than `0`.
- Third command prints the expected SHA256 fingerprint for your official cosign public key.

Post-deploy auth + RDP probe auth secrets:

```bash
kubectl -n labs get secret bretter-postdeploy-auth -o go-template='{{index .data "admin_password"}}' | wc -c
kubectl -n labs get secret bretter-postdeploy-auth -o go-template='{{index .data "synthetic_password"}}' | wc -c
kubectl -n labs get secret bretter-userflow-slo-api-auth -o go-template='{{index .data "password"}}' | wc -c
```

Expected: each command prints a value greater than `0`.

If off-cluster backup replication is enabled, verify the CronJob and secret wiring:

```bash
kubectl -n labs get cronjob bretter-postgres-backup-replication
kubectl -n labs get secret bretter-postgres-backup-replication -o go-template='{{index .data "aws_access_key_id"}}' | wc -c
kubectl -n labs get secret bretter-postgres-backup-replication -o go-template='{{index .data "aws_secret_access_key"}}' | wc -c
```

## VM and container workload visibility

List only user runtime pods:

```bash
kubectl -n labs get pods | rg '^vm-|^virt-launcher-|^ct-'
```

Separate quick filters:

```bash
kubectl -n labs get pods | rg '^ct-'
kubectl -n labs get pods | rg '^vm-|^virt-launcher-'
```

## VM launch preflight and pending triage

Check user-facing launch preflight for a specific template before escalating:

```bash
TOKEN='<session-or-api-token>'
TEMPLATE_ID='<template-id>'
curl -k -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://<NODE_EXTERNAL_HOST>:30073/api/user/templates/${TEMPLATE_ID}/preflight" | jq .
```

Inspect preflight blockers quickly:

- `checks[].key=placement`: cluster selection/policy issue.
- `checks[].key=namespace`: tenant namespace bootstrap/RBAC/secret-sync issue.
- `checks[].key=source_pvc`: image clone source PVC missing/inaccessible.
- `checks[].key=storage_class`: storage class lookup mismatch.
- `checks[].key=runner_image`: node-level image pull issue.

For pods stuck in `pending`/`building`:

```bash
kubectl -n labs get pods | rg '^vm-|^virt-launcher-'
kubectl -n labs describe pod <vm-pod-name>
kubectl -n labs get pvc | rg '^vm-disk-'
kubectl -n labs describe pvc <vm-disk-pvc-name>
kubectl -n labs get events --sort-by=.metadata.creationTimestamp | tail -n 80
```

Storage class health check:

```bash
kubectl get storageclass
kubectl get csinodes
```

Alert-focused checks for pending VM startup/PVC provisioning:

```bash
kubectl -n monitoring get prometheusrules | rg BretterVmStartupSlow
kubectl -n monitoring get prometheusrules | rg BretterVmDiskPvcPendingTooLong
```

## Rollout verification

```bash
kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s
kubectl -n labs get pods -o wide
```

## Operator/CRD checks (when `ORCHESTRATION_BACKEND=dual|crd`)

If you are not running the external LabInstance operator deployment, keep these checks for incident triage only and rely on the automatic go-live canary skip behavior (`RUN_CRD_OPERATOR_CANARY=auto`).

Controller health:

```bash
kubectl -n labs get deploy bretter-labinstance-operator
kubectl -n labs logs deploy/bretter-labinstance-operator --tail=300
kubectl -n labs get labinstances.labs.bretter.io
```

Backfill active DB rows into CRDs:

```bash
.venv/bin/python scripts/backfill_labinstances_from_db.py --dry-run
.venv/bin/python scripts/backfill_labinstances_from_db.py
```

Canary lifecycle test:

```bash
NAMESPACE=labs CRD_CANARY_TEMPLATE_ID=<template-id> ./scripts/crd_canary_labinstance.sh
```

Go-live proof canary behavior:

- `RUN_CRD_OPERATOR_CANARY=auto`: run canary only when `bretter-labinstance-operator` exists and is ready.
- `RUN_CRD_OPERATOR_CANARY=1`: strict mode; fail if the operator is missing/unready.
- `RUN_CRD_OPERATOR_CANARY=0`: always skip canary.

## Pre-deploy gate

Run this before rollout to catch config/secret blockers early:
- strict merged values validation (`values-production.yaml` + `values-prod-site.yaml`)
- runtime/signature secret presence checks
- per-node image pull smoke checks for backend/frontend/runner

```bash
NAMESPACE=labs ./scripts/deploy_preflight.sh
```

For CI/static-only usage (skip cluster calls):

```bash
SKIP_CLUSTER_CHECKS=1 ./scripts/deploy_preflight.sh
```

Explicit PostgreSQL migration gate (recommended before production rollout and now required in CI):

```bash
ALEMBIC_POSTGRES_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/bretter_ci_gate' \
ALEMBIC_POSTGRES_ADMIN_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres' \
./scripts/check_alembic_postgres.sh
```

If you need to bypass node image pull checks temporarily (dev-only):

```bash
PREDEPLOY_VERIFY_NODE_IMAGE_PULLS=0 NAMESPACE=labs ./scripts/deploy_preflight.sh
```

If image-based runner changes were deployed, verify node placement and runner startup:

```bash
kubectl -n labs get pods -o wide | rg 'vm-|virt-launcher|ct-'
```

## Deploy/user-flow smoke pipeline

GitHub workflow:

- `.github/workflows/deploy-userflow-smoke.yml`
- `.github/workflows/post-deploy-synthetic.yml`
- `.github/workflows/nightly-restore-drill.yml`
- `.github/workflows/playwright-rdp-smoke.yml`

Coverage:

- API login/RBAC/OIDC smoke regressions
- Kind-based LabInstance controller smoke
- Kind-based LabImageImport controller smoke
- Post-deploy API synthetic flow (login, VM launch, Guacamole RDP readiness/frame, teardown)
- Browser-level Guacamole RDP launch/connect smoke (Playwright)
- Restore-drill execution gate for release branches

Release-branch required checks:

- `post-deploy-synthetic.yml` and `nightly-restore-drill.yml` now run on push/PR to `release/**`.
- Configure branch protection/rulesets to require:
  - `synthetic` job from post-deploy synthetic workflow
  - `restore-drill` job from nightly restore workflow
  - `rdp-smoke` job from Playwright RDP smoke workflow

Main branch required smoke:

- `.github/workflows/deploy-userflow-smoke.yml` now runs on `push` and `pull_request` to `main`.
- Configure branch protection/rulesets to require the `userflow-smoke` job.
- Use `.github/workflows/enforce-branch-protection.yml` (or scripts below) to keep required checks enforced as code.

Branch protection automation (manual from local clone):

```bash
GH_TOKEN=<repo-admin-token> REQUIRED_CHECKS='guardrails,userflow-smoke,synthetic,restore-drill,rdp-smoke' ./scripts/apply_branch_protection.sh
GH_TOKEN=<repo-admin-token> REQUIRED_CHECKS='guardrails,userflow-smoke,synthetic,restore-drill,rdp-smoke' ./scripts/check_branch_protection.sh
```

Manual trigger in GitHub Actions:

1. Open **Actions**.
2. Select **Deploy Userflow Smoke**.
3. Click **Run workflow**.

## Post-deploy synthetic validation (manual)

1. Login on a fresh browser profile.
2. Start one VM lab and verify states progress to `Running`.
3. Connect to VM and verify interactive session.
4. Delete VM lab.
5. Start one container lab and verify `Building -> Starting -> Running`.
6. Connect to container app and verify app response.
7. Confirm idle prompt appears on both user page and connect tab.
8. Confirm deleting the running lab clears single-lab-limit message.

## Go-live proof artifact

Generate and archive a single report covering rollout, production env checks, runtime/signature secret wiring, tenant namespace baseline object checks (quota/limits/netpol), bootstrap pruning, and API health:

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

Optional restore drill as part of go-live:

```bash
NAMESPACE=labs RUN_RESTORE_DRILL=1 ./scripts/production_go_live_proof.sh
```

Standalone restore drill:

```bash
NAMESPACE=labs ./scripts/restore_drill_postgres.sh
```

Nightly restore evidence workflow:

- `.github/workflows/nightly-restore-drill.yml` (requires `KUBECONFIG_B64` repo secret)

Staging failure-drill workflow:

- `.github/workflows/staging-failure-drills.yml` (requires `STAGING_KUBECONFIG_B64` or `KUBECONFIG_B64`)
- Script entrypoint: `scripts/failure_drill_control_plane.sh`

Production deploy + drift workflows:

- `.github/workflows/deploy-production.yml` deploys digest-pinned production values and runs go-live proof + drift check.
- `.github/workflows/promote-staging-to-production.yml` enforces staging preflight + staging go-live proof before production deployment.
- `.github/workflows/config-drift-check.yml` runs scheduled/manual live-vs-rendered drift checks using `scripts/check_live_config_drift.py`.

Tenant isolation impersonation smoke:

- Script: `scripts/smoke_tenant_isolation_impersonation.sh`
- CI: included in `.github/workflows/ci-guardrails.yml` against kind.

Grafana SLO dashboard pack:

- ConfigMap manifest: `deploy/monitoring/grafana-userflow-slo-dashboard.yaml`
- Applied by `setup.sh` postdeploy when monitoring is enabled.

## Rollback command

Use the one-command rollback helper:

```bash
NAMESPACE=labs ./scripts/rollback_release.sh
```

Optional explicit revision:

```bash
TARGET_REVISION=12 NAMESPACE=labs ./scripts/rollback_release.sh
```

By default this script:
- rolls Helm back to the most recent prior deployed/superseded revision
- waits for backend/frontend rollout
- runs go-live proof unless `RUN_GO_LIVE_PROOF=0`

## Continuous probe checks

`setup.sh` deploy phase now applies recurring probe CronJobs:

- `bretter-ghcr-access-check`: verifies GHCR registry/API + manifest pullability for backend/frontend/runner refs.
- `bretter-slo-vm-launch`: fails when VM launch failure rate breaches configured threshold.
- `bretter-slo-rdp-readiness`: fails when too many RDP instances remain stuck in starting states.
- `bretter-slo-upload-finalize`: fails when upload-finalize failure rate breaches configured threshold.
- `bretter-slo-image-import-queue-age`: fails when oldest in-progress image import exceeds configured max age.
- `bretter-slo-rdp-connect-latency` (optional): authenticated RDP connect-token/page latency probe.

RDP connect-latency probe auth model:

- Uses `USERFLOW_SLO_API_AUTH_SECRET_NAME` (`username` + `password` keys by default).
- In production, keep `USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0` and pre-provision the secret.
- Avoid plaintext probe passwords in setup env for production runs.

Quick status:

```bash
kubectl -n labs get cronjob bretter-ghcr-access-check bretter-slo-vm-launch bretter-slo-rdp-readiness bretter-slo-upload-finalize bretter-slo-image-import-queue-age bretter-slo-rdp-connect-latency
kubectl -n labs get jobs --sort-by=.metadata.creationTimestamp | rg 'bretter-ghcr-access-check|bretter-slo-'
```

## Alert routing

Alertmanager routing defaults are now explicit in setup-managed monitoring values.

- Default receiver/grouping fields are controlled by:
  - `ALERTMANAGER_DEFAULT_RECEIVER_NAME`
  - `ALERTMANAGER_ROUTE_GROUP_BY`
  - `ALERTMANAGER_ROUTE_GROUP_WAIT`
  - `ALERTMANAGER_ROUTE_GROUP_INTERVAL`
  - `ALERTMANAGER_ROUTE_REPEAT_INTERVAL`
- Optional webhook routing uses secret-backed URL keys:
  - `ALERTMANAGER_WEBHOOK_RECEIVER_ENABLED=1`
  - `ALERTMANAGER_WEBHOOK_SECRET_NAME`
  - `ALERTMANAGER_WEBHOOK_SECRET_KEY`

See full procedure: [Alert Routing and Receiver Defaults](Alert-Routing-and-Receiver-Defaults).

## Quotas and scaling checks

```bash
kubectl -n labs get resourcequota bretter-runtime-quota -o yaml
kubectl -n labs describe resourcequota bretter-runtime-quota
kubectl -n labs get limitrange bretter-default-container-limits -o yaml
```

Admin UI checks:

- `/admin/settings/namespaces` should load available namespaces and limits.
- Legacy aliases `/admin/scaling-quotas` and `/admin/team-quotas` should route to the same namespace settings page.
- Quota changes should apply to both VM and container starts.
- When hit, users should receive quota detail (HTTP 429) or queued reason.
- `/admin/audit-events` should show recent admin mutations for templates/images/quotas/settings.

## Tenant namespace bootstrap

Bootstrap tenant namespace guardrails:

```bash
TEAM=physics TEAM_NAMESPACE_PREFIX=labs-team- ./scripts/bootstrap_team_namespace.sh
```

Verify:

```bash
kubectl get ns labs-team-physics --show-labels
kubectl -n labs-team-physics get resourcequota bretter-tenant-quota
kubectl -n labs-team-physics get networkpolicy
```

Managed namespace lifecycle operations:

- Observability endpoint: `GET /admin/settings/namespaces/observability`
- Ordered cleanup endpoint: `POST /admin/settings/namespaces/{namespace}/decommission`
- Compatibility delete endpoint: `DELETE /admin/settings/namespaces/{namespace}`

Namespace configuration backup/restore:

```bash
PYTHONPATH=backend python3 scripts/namespace_config_backup.py export --output backups/namespaces-$(date +%Y%m%d).json
PYTHONPATH=backend python3 scripts/namespace_config_backup.py import --input backups/namespaces-YYYYMMDD.json --dry-run
```

## Common incidents and triage

### Dev-only unsigned image exception flow

Use only for non-production troubleshooting of local/unsigned images:

```bash
NAMESPACE=labs MODE=apply ./scripts/apply_dev_signature_exception.sh
```

Only pods with label `security.bretter-labs.io/allow-unsigned-dev=true` are excepted from `bretter-verify-image-signatures`.
Remove immediately after testing:

```bash
NAMESPACE=labs MODE=delete ./scripts/apply_dev_signature_exception.sh
```

### CI guardrail run fails with missing `httpx`

Symptom:

- GitHub Actions guardrail job fails while loading `fastapi.testclient` with:
  - `RuntimeError: ... requires the httpx package to be installed`

Actions:

1. Confirm the run is for latest `main`, not a historical commit.
2. Re-trigger CI from current `main` head.
3. Validate install/import step in workflow:

```bash
.venv/bin/pip install -r backend/requirements.txt pytest httpx
.venv/bin/python -c "import httpx; from fastapi.testclient import TestClient"
```

Operational note:

- Re-running a failed historical workflow run can still fail because it executes old commit code.
- Trigger a fresh run from latest `main` for current behavior.

### CI guardrails fails on `smoke_tls_login.sh` with backend startup exit

Symptoms:

- `ERROR: frontend TLS health probe failed`
- Backend exits during startup while logs show Alembic baseline stamping and early migration failure.

Likely cause:

- Legacy baseline stamping was applied to a non-legacy partial schema state.

Actions:

1. Ensure run is on latest `main` with migration baseline fix in `backend/src/migrations.py`.
2. Re-run CI guardrails from latest commit.
3. Reproduce locally if needed:

```bash
PYTHONPATH=backend .venv/bin/pytest backend/tests/test_ci_guardrails.py -k alembic
./scripts/smoke_tls_login.sh
```

### Publish workflow fails pushing to GHCR with `403 Forbidden`

Symptoms:

- `docker/build-push-action` fails during push with GHCR blob/manifests `403 Forbidden`.

Likely cause:

- `GITHUB_TOKEN` is insufficient for existing private package namespace state.

Actions:

1. Configure repository Actions secrets:
   - `GHCR_USERNAME`
   - `GHCR_PAT` (scope: `write:packages`)
2. Re-run `.github/workflows/publish-and-pin-images.yml`.
3. Confirm latest publish run is green and package timestamps update.

### Labs stuck in pending/queued

Check scheduler pressure and events:

```bash
kubectl -n labs get pods | rg 'Pending|ContainerCreating'
kubectl -n labs describe pod <pod-name>
kubectl describe nodes | rg -n 'DiskPressure|MemoryPressure|PIDPressure|Ready'
```

Typical causes:

- Node resource pressure (CPU/memory/disk)
- PVC/storage class scheduling failure
- Namespace quota limits

### Token/session auth failures (`invalid token`, `session expired`, repeated login loops)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'invalid token|session expired|missing authorization token|auth'
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_AUTH_COOKIE_SECURE|BLABS_CONNECT_COOKIE_SECURE|BLABS_PUBLIC_SCHEME|BLABS_CORS_ALLOWED_ORIGINS'
```

If users report sudden login failures after rollout:

1. Verify frontend origin is included in `BLABS_CORS_ALLOWED_ORIGINS`.
2. Verify requests are using the same scheme/host (`https` in production).
3. Confirm system clocks are sane on control plane and worker nodes.

### Runner scheduling failures (node selector / taints / insufficient resources)

Checks:

```bash
kubectl -n labs get pods -o wide | rg '^vm-|^virt-launcher-'
kubectl -n labs describe pod <vm-runner-or-virt-launcher-pod>
kubectl get nodes --show-labels | rg 'kubernetes.io/hostname|runner'
kubectl describe nodes | rg -n 'Taints|Allocatable|DiskPressure|MemoryPressure'
```

Look for:

- `0/X nodes are available` scheduler messages.
- Node selector mismatch with `RUNNER_NODE_SELECTOR_VALUE`.
- Resource shortage, taint rejection, or storage attach constraints.

### Signature verification failures (container image registration/start)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'signature|cosign|verification|public key'
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | wc -c
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED|BLABS_CONTAINER_SIGNATURE_KEY_REF|container-signature-key'
```

If verification fails:

1. Confirm key secret contains the expected file (`cosign.pub` by default).
2. Confirm `BLABS_CONTAINER_SIGNATURE_KEY_REF` matches the mounted file path.
3. Re-check key fingerprint against your trusted source before retrying image registration.

### Runtime secret injection misconfiguration

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -n 'BLABS_SECRETS_ENCRYPTION_KEY|decrypt|Encrypted secret'
kubectl -n labs get secret bretter-runtime-secrets -o go-template='{{index .data "secrets_encryption_key"}}' | wc -c
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_SECRETS_ENCRYPTION_KEY'
```

Common outcomes:

- Missing key -> startup fails in production profile.
- Wrong key -> decrypt errors for encrypted settings.
- Secret key-name drift -> deployment references a different key than the secret provides.

### Upload appears stuck at 100%

Usually browser upload is done and cluster finalization is still running.

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'upload|finaliz|convert|cdi|data volume|error'
kubectl -n labs get pvc
kubectl describe nodes | rg -n 'DiskPressure|imagefs|nodefs'
```

### Connect issues (blank page, proxy error, delayed connect)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'connect|proxy|websocket|token|origin|error'
kubectl -n labs get svc | rg 'ct-|vm-|bretter'
kubectl -n labs get endpoints | rg 'ct-|vm-'
```

Common causes:

- App/VM not ready yet (`Starting`)
- Connect session cookie missing/expired
- Backend cannot reach container service/pod endpoint

### Storage pressure and disk alerts

Checks:

```bash
kubectl -n labs get pvc
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
kubectl -n longhorn-system get nodes.longhorn.io
kubectl -n longhorn-system get volumes.longhorn.io
```

If alerts indicate sustained high usage, clean stale labs/uploads and expand node storage.

## Alerts and error log behavior

`/admin/alerts-errors` behavior:

- Alertmanager alerts are pulled from configured API URL.
- Error log is capped at 10MB.
- Oldest log lines are dropped when cap is reached.
- UI shows 50 log entries per page with page navigation.
- `Clear Error Log` truncates backend error logs.

## Single active lab enforcement

The platform enforces one active workload per user across VM + container starts.

Expected user message:

`You already have a virtual lab running. Delete the current lab before starting a new one.`

If this persists unexpectedly, verify the user has no active VM/container instance records and no stuck runtime pods.

## Related pages

- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Error Catalog](Error-Catalog)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
- [Network Modes Reference](Network-Modes-Reference)
- [LDAP Authentication](LDAP-Authentication)
