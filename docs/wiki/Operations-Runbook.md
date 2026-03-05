# Operations Runbook

## Core health checks

```bash
kubectl -n labs get pods
kubectl -n labs get deploy bretter-backend bretter-frontend
kubectl -n labs logs deploy/bretter-backend --tail=200
kubectl -n labs logs deploy/bretter-frontend --tail=200
```

## Rollout checks

```bash
kubectl -n labs rollout status deploy/bretter-backend
kubectl -n labs rollout status deploy/bretter-frontend
kubectl -n labs get pods -o wide
```

## Common issues

### Labs stuck in pending

- Check node allocatable CPU/memory and storage pressure.
- Check scheduler events for `FailedScheduling` details.
- Verify PVC availability and storage class health.

### Upload appears stuck at 100%

- This is often finalization/normalization time, not browser upload time.
- Check backend logs for finalize task progress.
- Confirm PVC free space and image conversion requirements.

### Connect fails

- Confirm app/API endpoints are reachable over HTTPS.
- Validate connect session cookies are present.
- Check backend and runner/container pod logs.

### Disk pressure alerts

- Inspect node filesystem usage and PVC usage.
- Clear stale images, stopped labs, and stale temp data.
- Expand storage or rebalance workload if sustained high usage continues.

## Quick validation after changes

1. Admin login succeeds on a new client/browser.
2. Upload a test VM image and create/update a template.
3. Launch one VM lab end-to-end.
4. Launch one container lab end-to-end.
5. Verify idle timeout prompt and cleanup behavior.
6. Verify alert/error pages render and update.
