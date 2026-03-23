# Multi-Cluster Operations

Last reviewed: March 23, 2026.

## Scope

This control-plane now includes first-class multi-cluster primitives for:

- Cluster inventory/health metadata
- Tenant placement policy by region/compliance/cluster allowlist
- Artifact replication queue for VM/container images and templates

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

## Admin API

Cluster inventory:

- `GET /admin/settings/clusters`
- `POST /admin/settings/clusters`
- `PATCH /admin/settings/clusters/{cluster_id}`
- `POST /admin/settings/clusters/{cluster_id}/probe`
- `DELETE /admin/settings/clusters/{cluster_id}` (soft-disable; local cluster cannot be disabled)

Tenant placement policy:

- `GET /admin/settings/placement-policies`
- `PUT /admin/settings/placement-policies/{team}`
- `DELETE /admin/settings/placement-policies/{team}`

Replication queue:

- `GET /admin/replication/artifacts`
- `POST /admin/replication/artifacts`
- `PATCH /admin/replication/artifacts/{replication_id}`

## Placement behavior

Launch placement (`vm`/`container`) is now policy-driven:

1. Start with enabled + schedule-enabled + runtime-enabled clusters
2. Apply tenant constraints (`required_regions`, `required_compliance_tags`, `allowed_cluster_ids`)
3. Apply template cluster pin (`template.cluster_id`) when set
4. Apply tenant preferred cluster if available
5. Otherwise pick highest `capacity_weight` (local tie-break preference)

If no candidate satisfies policy, launch fails fast with a placement error.

## Runtime note

This phase wires cluster-aware placement metadata and queueing.
Current runtime launch adapter in this build remains local-cluster-only for VM/container execution paths.
Use cluster inventory, policy, and replication queue now; roll out remote runtime adapters as a follow-on phase.

## Replication workflow

Use replication queue records as the source of truth for sync state:

1. Enqueue with `POST /admin/replication/artifacts`
2. External controller/automation performs copy/sync
3. Update status via `PATCH /admin/replication/artifacts/{replication_id}` (`queued`, `syncing`, `ready`, `error`)

Recommended status update cadence:

- Set `syncing` when work begins (`last_attempt_at`)
- Set `ready` on success (`last_synced_at`)
- Set `error` with actionable detail on failure
