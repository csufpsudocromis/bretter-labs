# Operations Runbook

Last reviewed: March 6, 2026.

## Baseline checks

Confirm context/namespace first:

```bash
kubectl config current-context
kubectl get ns labs
```

Core platform checks:

```bash
kubectl -n labs get deploy bretter-backend bretter-frontend
kubectl -n labs get pods -o wide
kubectl -n labs logs deploy/bretter-backend --tail=200
kubectl -n labs logs deploy/bretter-frontend --tail=200
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

## Rollout verification

```bash
kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s
kubectl -n labs get pods -o wide
```

If image-based runner changes were deployed, verify both nodes can pull/start:

```bash
kubectl -n labs get pods -o wide | rg 'vm-|virt-launcher|ct-'
```

## Post-deploy synthetic validation (manual)

1. Login on a fresh browser profile.
2. Start one VM lab and verify states progress to `Running`.
3. Connect to VM and verify interactive session.
4. Delete VM lab.
5. Start one container lab and verify `Building -> Starting -> Running`.
6. Connect to container app and verify app response.
7. Confirm idle prompt appears on both user page and connect tab.
8. Confirm deleting the running lab clears single-lab-limit message.

## Common incidents and triage

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
- Template/cluster concurrency guardrails

### Upload appears stuck at 100%

Usually this means browser upload is done and cluster finalization is still running.

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

- App not ready yet (`Starting` state)
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
- "Clear Error Log" truncates backend error logs.

## Single active lab enforcement

The platform enforces one active workload per user across VM + container starts.

Expected user message:

`You already have a virtual lab running. Delete the current lab before starting a new one.`

If this persists unexpectedly, verify the user has no active VM/container instance records and no stuck runtime pods.
