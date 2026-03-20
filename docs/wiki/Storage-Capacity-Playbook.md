# Storage Capacity Playbook

Last reviewed: March 19, 2026.

Use this playbook when node filesystem or PVC utilization is rising.

## Alert thresholds

Default thresholds in setup:

- Warn: 70%
- Critical: 85%
- Emergency: 95%

Config keys:

- `AUTOCLEANUP_NODEFS_WARN_PCT`, `AUTOCLEANUP_NODEFS_CRITICAL_PCT`, `AUTOCLEANUP_NODEFS_EMERGENCY_PCT`
- `AUTOCLEANUP_PVC_WARN_PCT`, `AUTOCLEANUP_PVC_CRITICAL_PCT`, `AUTOCLEANUP_PVC_EMERGENCY_PCT`

## What to check first

```bash
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
kubectl -n labs get pvc
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n labs get pods | rg 'Pending|ContainerCreating|Evicted'
kubectl -n labs get jobs --sort-by=.metadata.creationTimestamp | tail -n 40
```

## Cleanup order (least risk to most disruptive)

1. Delete stale failed/pending runtime pods.
2. Delete stale DataVolumes/uploads no longer needed.
3. Delete stopped lab records that should have auto-cleaned.
4. Prune unused/old VM images not used by active templates.
5. Prune orphaned `ctsvc-*` services and stale temporary artifacts.
6. Expand storage if pressure persists.

Useful cleanup commands:

```bash
kubectl -n labs get pods --field-selector=status.phase=Failed -o name | xargs -r kubectl -n labs delete
kubectl -n labs get pods --field-selector=status.phase=Succeeded -o name | xargs -r kubectl -n labs delete
```

## Expansion order

1. Expand node filesystem for kubelet/container runtime paths.
2. Expand Longhorn backing disk capacity.
3. Expand app PVC sizes (golden images/postgres/backend data).
4. Rebalance workloads across nodes if one node is hot.

Upload/import capacity note:

- Upload/import flows rely on temporary PVCs; baseline minimum is `BLABS_MIN_UPLOAD_PVC_GIB` (default `80`).
- If image finalization/import fails due capacity, raise this value and redeploy.

## Longhorn-specific guidance

- Keep lab disks on low replica count for ephemeral workloads when acceptable.
- Keep golden image and clone classes aligned to avoid cross-class clone issues.
- Monitor degraded/unknown node robustness before peak usage windows.

## Proactive controls

- Keep automatic cleanup enabled in `setup.sh`.
- Keep alerting active for 70/85/95 thresholds.
- Add periodic report of top PVC consumers.
- Reserve node headroom for launch bursts.

## Incident close criteria

- No node `DiskPressure` condition.
- No PVC > 95% utilization.
- New VM and container launches complete successfully.
- Upload/finalization path completes without retries/timeouts.
- New VM launches and connect flows pass after cleanup.

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Error Catalog](Error-Catalog)
- [Scaling and Quotas](Scaling-and-Quotas)
