# Post-Deploy Validation SOP

Last reviewed: March 26, 2026.

Run this after every deployment before closing the change.

Pre-deploy counterpart:

```bash
NAMESPACE=labs ./scripts/deploy_preflight.sh
```

## Scope

Validates:

- Login/auth session
- VM launch/connect/delete
- Container launch/connect/delete
- Container websocket readiness/frame validation (`/user/containers/{id}/connect-readiness`)
- Admin API read-path health (`/admin/*`)
- Idle timeout prompt visibility (user page + connect page)
- Runner image startup on runner node (postdeploy smoke pod)
- Core rollout health

## Phase 1: Rollout health gate

```bash
kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s
kubectl -n labs get pods -o wide
curl -skf https://<NODE_EXTERNAL_HOST>:30073/api/health
```

Fail if:

- Any control-plane deployment is unavailable.
- Pods are crashlooping or not ready.
- `/api/health` does not return `{"status":"ok"}`.
- Backend deployment env still includes `BLABS_ADMIN_DEFAULT_PASSWORD` after prune step.

Bootstrap env prune check:

```bash
kubectl -n labs get deploy bretter-backend -o yaml | rg BLABS_ADMIN_DEFAULT_PASSWORD
```

Expected: no output.

Optional one-shot proof report:

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

Quick websocket/connect diagnostics snapshot:

```bash
NAMESPACE=labs ./scripts/diagnose_connectivity.sh
```

Note:

- `setup.sh` runs this report automatically in `postdeploy` when `RUN_PRODUCTION_GO_LIVE_PROOF=1` (default for `PRODUCTION_PROFILE=1`).

## Phase 2: Synthetic job (preferred)

If setup admin API smoke and synthetic checks are enabled:

```bash
kubectl -n labs get job bretter-post-deploy-admin-api-smoke
kubectl -n labs logs job/bretter-post-deploy-admin-api-smoke --all-containers=true
kubectl -n labs get job bretter-post-deploy-check
kubectl -n labs logs job/bretter-post-deploy-check --all-containers=true
```

Pass criteria:

- Admin API smoke job completes successfully.
- Job completes successfully.
- Logs include end-to-end success markers for VM/container paths.

Notes:

- In production profile, setup requires explicit non-bootstrap credentials for admin/synthetic checks:
  - direct env (`ADMIN_API_SMOKE_PASSWORD`, `SYNTHETIC_CHECK_PASSWORD`), or
  - secret-backed mode via `POST_DEPLOY_AUTH_SECRET_NAME` + key variables.
- For non-production runs, setup can still auto-disable checks when a new bootstrap secret is generated and no explicit credentials are supplied.
- Setup runs a runner image smoke pod during `postdeploy` by default (`RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK=1`).
- For existing deployments, run authenticated synthetic validation with explicit credentials:

```bash
SETUP_PHASES=postdeploy \
RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 \
SYNTHETIC_CHECK_USERNAME=admin \
SYNTHETIC_CHECK_PASSWORD='<EXISTING_ADMIN_PASSWORD>' \
RUN_POST_DEPLOY_ADMIN_API_SMOKE_CHECK=1 \
ADMIN_API_SMOKE_USERNAME=admin \
ADMIN_API_SMOKE_PASSWORD='<EXISTING_ADMIN_PASSWORD>' \
./scripts/setup.sh
```

Secret-backed credential example (recommended):

```bash
SETUP_PHASES=postdeploy \
POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth \
POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY=admin_username \
POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY=admin_password \
POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY=synthetic_username \
POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY=synthetic_password \
./scripts/setup.sh
```

To rerun only runner smoke manually:

```bash
SETUP_PHASES=postdeploy \
RUN_POST_DEPLOY_API_HEALTH_CHECK=0 \
RUN_POST_DEPLOY_SYNTHETIC_CHECK=0 \
RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK=1 \
./scripts/setup.sh
```

## Phase 3: Manual UI validation

Use a clean browser profile.

1. Login succeeds.
2. Start one VM lab.
3. VM status reaches `Running`.
4. Connect to VM and confirm interactive screen.
5. Delete VM lab.
6. Start one container lab.
7. Status transitions: `Building` -> `Starting` -> `Running`.
8. Connect to container and confirm app response.
9. Verify idle timeout prompt appears on user page and connect tab.
10. Delete container and confirm lab list clears.

## Phase 4: Alert/error sanity

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -i 'error|exception|traceback|connect proxy failed|invalid token'
kubectl -n labs get events --sort-by=.lastTimestamp | tail -n 80
```

Fail if:

- New recurring high-severity errors are present.
- Scheduler/storage pressure blocks launches.

## Evidence to retain

- Deployment revision/hash
- Synthetic job log output
- Manual checklist pass confirmation
- Any exceptions and remediation notes

## Change close criteria

- All phases pass.
- No active critical alerts tied to the rollout.
- Login + VM + container + idle timeout behavior is verified.

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Error Catalog](Error-Catalog)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
