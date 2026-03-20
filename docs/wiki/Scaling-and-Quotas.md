# Scaling and Quotas

Last reviewed: March 19, 2026.

## Overview

`/admin/scaling-quotas` controls namespace quota limits used by VM and container launch paths.

UI behavior:

- Namespace is selected from a dropdown populated from cluster namespaces and saved quota rows.
- Team is editable in UI (defaults to `default`) and can be selected/typed for per-team quota rows.
- Empty fields mean unlimited for that limit.

## Available limits

Per namespace quota supports:

- Max concurrent labs
- CPU cap (millicores)
- RAM cap (MB)
- Storage cap (GiB)
- Idle timeout cap (minutes)
- Enabled/disabled toggle

## Enforcement behavior

Quota checks run on VM/container start and restart.

Possible outcomes:

- Allowed: launch continues.
- Rejected: HTTP 429 with specific reason.
- Queued (containers only when queue mode enabled): user sees queued/pending reason.

Typical quota detail message:

- `team quota reached in namespace labs: max concurrent labs is <n>`

The one-active-lab rule is still enforced separately:

`You already have a virtual lab running. Delete the current lab before starting a new one.`

## Backing API routes

- `GET /admin/quota-namespaces`
- `GET /admin/quota-teams`
- `GET /admin/team-quotas`
- `POST /admin/team-quotas`
- `PATCH /admin/team-quotas/{quota_id}`
- `DELETE /admin/team-quotas/{quota_id}`

## Operational checks

List configured quota rows from API:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=200 | rg -i 'team quota|quota reached|429'
```

Check namespace hard limits:

```bash
kubectl -n labs get resourcequota bretter-runtime-quota -o yaml
kubectl -n labs get limitrange bretter-default-container-limits -o yaml
```

## Troubleshooting

If quota page fails to load namespaces:

- Verify backend service account can list namespaces.
- Check backend logs for `Failed to list namespaces for quota selector` warnings.

If users are blocked unexpectedly:

- Verify stuck VM/container instances are not still marked active.
- Check pending pods and cleanup orphaned/stale workloads.
- Re-test after deleting stale runtime rows/pods.
- Review `/admin/audit-events` for recent quota edits that may explain behavior changes.

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Security and Auth](Security-and-Auth)
- [Container Labs](Container-Labs)
