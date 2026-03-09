# Post-Deploy Validation SOP

Last reviewed: March 9, 2026.

Run this after every deployment before closing the change.

## Scope

Validates:

- Login/auth session
- VM launch/connect/delete
- Container launch/connect/delete
- Idle timeout prompt visibility (user page + connect page)
- Core rollout health

## Phase 1: Rollout health gate

```bash
kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s
kubectl -n labs get pods -o wide
```

Fail if:

- Any control-plane deployment is unavailable.
- Pods are crashlooping or not ready.

## Phase 2: Synthetic job (preferred)

If setup synthetic check is enabled:

```bash
kubectl -n labs get job bretter-post-deploy-check
kubectl -n labs logs job/bretter-post-deploy-check --all-containers=true
```

Pass criteria:

- Job completes successfully.
- Logs include end-to-end success markers for VM/container paths.

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

- [Operations Runbook](Operations-Runbook.md)
- [Error Catalog](Error-Catalog.md)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive.md)
