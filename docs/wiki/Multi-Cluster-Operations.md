# Multi-Cluster Operations

Last reviewed: March 23, 2026.

## Scope

This control-plane now includes first-class multi-cluster primitives for:

- Cluster inventory/health metadata
- Tenant placement policy by region/compliance/cluster allowlist
- Artifact replication queue for VM/container images and templates
- Cluster-aware runtime dispatch for VM/container launch paths
- Runtime credential sourcing from Kubernetes Secret references

Failover and live migration are intentionally out of scope in this phase.

## Core concepts

- `Cluster`: schedulable/runtime cluster metadata (`region`, `compliance_tags`, `capacity_weight`, health, enable/disable flags)
- `TeamPlacementPolicy`: tenant-level placement constraints (`preferred_cluster_id`, hard pin, required regions/compliance tags, allowlist)
- `ArtifactReplication`: replication work queue (`vm_image`, `vm_template`, `container_image`, `container_template`)

All VM/container/image/template/instance records now carry `cluster_id`.

## Local cluster bootstrap

At backend startup, a local cluster record is auto-created/maintained:

- `id`: `MULTI_CLUSTER_LOCAL_CLUSTER_ID` (default `local`)
- `region`: `MULTI_CLUSTER_LOCAL_REGION` (default `local`)
- forced enabled/schedulable/runtime flags for local cluster
- default runtime namespace inherits `BLABS_KUBE_NAMESPACE`

## Admin API

Cluster inventory:

- `GET /admin/settings/clusters`
- `GET /admin/settings/clusters/telemetry`
- `POST /admin/settings/clusters`
- `PATCH /admin/settings/clusters/{cluster_id}`
- `POST /admin/settings/clusters/{cluster_id}/probe`
- `DELETE /admin/settings/clusters/{cluster_id}` (soft-disable; local cluster cannot be disabled)
  - supports `?force=true` to disable even when referenced by policies or active workloads

Tenant placement policy:

- `GET /admin/settings/placement-policies`
- `GET /admin/settings/placement-policies/explain`
- `PUT /admin/settings/placement-policies/{team}`
- `DELETE /admin/settings/placement-policies/{team}`

Replication queue:

- `GET /admin/replication/artifacts`
- `POST /admin/replication/artifacts`
- `POST /admin/replication/artifacts/process`
- `PATCH /admin/replication/artifacts/{replication_id}`

## Placement behavior

Launch placement (`vm`/`container`) is now policy-driven:

1. Start with enabled + schedule-enabled + runtime-enabled clusters
2. Apply tenant constraints (`required_regions`, `required_compliance_tags`, `allowed_cluster_ids`)
3. Apply template cluster pin (`template.cluster_id`) when set
4. Apply tenant preferred cluster if available
5. Otherwise pick highest `capacity_weight` (local tie-break preference)

If no candidate satisfies policy, launch fails fast with a placement error.
Clusters without runtime kubeconfig credentials are excluded from scheduling unless they are the local cluster.

## Runtime dispatch

VM/container runtime operations use cluster-aware kube clients:

- Launch/start/stop/delete paths dispatch to selected `cluster_id`
- Runtime namespace resolves from cluster setting (`runtime_namespace`) with fallback to `BLABS_KUBE_NAMESPACE`
- Cluster kubeconfig is resolved in this order:
  1. Kubernetes Secret reference (`kubeconfig_secret_name` + optional namespace/key)
  2. Legacy encrypted DB kubeconfig payload (backward compatibility)

Recommended production pattern: use Secret reference only, avoid persisted kubeconfig blobs.

## Replication workflow

Use replication queue records as the source of truth for sync state.

1. Enqueue with `POST /admin/replication/artifacts`
2. Background processor handles queued work (`POST /admin/replication/artifacts/process` or reaper loop)
3. Status transitions: `queued` -> `syncing` -> `ready`/`error`

Recommended status update cadence:

- Set `syncing` when work begins (`last_attempt_at`)
- Set `ready` on success (`last_synced_at`)
- Set `error` with actionable detail on failure

Current processor performs metadata replication for supported artifact types and updates queue status accordingly.

## Admin UI

Platform admins can manage this in:

- `/admin/settings/multi-cluster`

The page includes cluster inventory, telemetry, placement policy editing, placement explain dry-run, and replication queue controls.
