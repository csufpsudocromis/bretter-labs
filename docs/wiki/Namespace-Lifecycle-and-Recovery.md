# Namespace Lifecycle and Recovery

Last reviewed: March 27, 2026.

## Scope

This runbook covers day-2 operations for managed namespaces:

- observability/health checks
- safe decommission flow
- configuration backup and restore
- reconciliation and drift correction

## Namespace observability

UI path:

- `Admin -> Settings -> Namespaces`
- `Namespace Observability` section shows:
  - runtime status (present/missing in cluster)
  - running/failed/queued labs
  - image upload task health
  - policy objects: ResourceQuota, LimitRange, NetworkPolicy

API path:

- `GET /admin/settings/namespaces/observability`

Response includes:

- `resource_quota_present`
- `limit_range_present`
- `network_policy_count`
- `required_network_policies_missing`
- workload and upload-task counters per namespace

## Reconciliation and drift correction

Manual reconcile:

```bash
curl -k -X POST "https://<host>/admin/settings/namespaces/<namespace>/reconcile" \
  -H "Cookie: auth=<session>"
```

Bulk reconcile:

```bash
curl -k -X POST "https://<host>/admin/settings/namespaces/reconcile-all" \
  -H "Cookie: auth=<session>"
```

Scheduled reconcile:

- setup creates `CronJob/bretter-namespace-reconciler` (default every 15 minutes)
- disable only if you run an external GitOps reconciler:
  - `ENABLE_NAMESPACE_RECONCILER=0`

## Safe decommission workflow

Preferred API:

- `POST /admin/settings/namespaces/{namespace}/decommission`

Behavior:

1. checks for active labs
2. optional force cleanup of runtime resources
3. removes namespace bindings from VM/container templates
4. deletes namespace-scoped DB records (labs, uploads, namespace artifacts, quotas)
5. optionally deletes Kubernetes namespace
6. removes managed-namespace record

Default behavior blocks on active labs.

Force cleanup example:

```bash
curl -k -X POST \
  "https://<host>/admin/settings/namespaces/labs-team-red/decommission?force_cleanup=true&delete_cluster_namespace=true" \
  -H "Cookie: auth=<session>"
```

Compatibility endpoint:

- `DELETE /admin/settings/namespaces/{namespace}`
- accepts the same query params:
  - `force_cleanup=true|false`
  - `delete_cluster_namespace=true|false`

## Namespace config backup and restore

Tool:

- `scripts/namespace_config_backup.py`

Exports:

- managed namespace policy rows
- namespace quota rows
- template enabled-namespace bindings
- container-template enabled-namespace bindings
- namespace-admin user scope bindings

Export example:

```bash
PYTHONPATH=backend python3 scripts/namespace_config_backup.py export \
  --output backups/namespaces-$(date +%Y%m%d).json
```

Validate restore (dry-run):

```bash
PYTHONPATH=backend python3 scripts/namespace_config_backup.py import \
  --input backups/namespaces-20260327.json \
  --dry-run
```

Apply restore:

```bash
PYTHONPATH=backend python3 scripts/namespace_config_backup.py import \
  --input backups/namespaces-20260327.json
```

## Security defaults and required controls

Managed namespace reconciliation enforces:

- `ResourceQuota: bretter-tenant-quota`
- `LimitRange: bretter-tenant-default-limits`
- NetworkPolicies:
  - `default-deny-ingress`
  - `default-deny-egress`
  - `allow-dns-egress`
  - `allow-same-namespace-traffic`
  - `allow-control-plane-ingress`

Disabling namespace network policies is blocked for production profile and non-platform-admin roles.

