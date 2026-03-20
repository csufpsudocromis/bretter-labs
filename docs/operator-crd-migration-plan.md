# Operator/CRD Migration Plan

Last reviewed: March 20, 2026.

This document defines the concrete migration from backend-imperative orchestration to Kubernetes-native reconciliation.

## Goals

- Move lifecycle state to Kubernetes custom resources with explicit desired/observed state.
- Replace fragile polling/one-off retries with controller reconciliation loops.
- Keep existing API/UI contracts stable during migration.
- Improve operations visibility with CRD `status.conditions` as the source of truth.

## Non-goals

- No big-bang rewrite of FastAPI routes.
- No forced migration of every existing database model to Kubernetes objects in one release.
- No SAML work in this phase (OIDC remains current identity adapter).

## Target control plane

1. UI/API writes desired state to CRDs (`LabInstance`, `LabImageImport`) and reads status.
2. Operator reconciles CRDs into Pods/PVCs/Services/Jobs/Policies.
3. Operator updates status conditions and events.
4. API exposes status to frontend without owning imperative step-by-step orchestration.

## API vs operator boundaries

| Area | FastAPI (kept) | Operator (new owner) |
| --- | --- | --- |
| Auth/session/RBAC | Login, OIDC/LDAP auth, cookies/tokens, role checks | None |
| Admin config | Templates, site settings, quotas, feature toggles | Reads config via projected ConfigMap/Secrets |
| VM/container launch request | Validate request and create/update CRD | None |
| Runtime lifecycle | Read status and return user/admin views | Create/update/delete runtime K8s resources |
| Image upload registration | Persist metadata and create import CRD | Execute CDI/import/convert/checksum lifecycle |
| Cleanup | Request deletion and set retention policy | Finalizers and deterministic teardown |
| Health/alerts API | Aggregate status/alerts and expose API payload | Emit conditions/events/metrics |

## Phase plan

### Phase 0: Preconditions and observability

- Add CRD status vocabulary and map current backend statuses to future conditions.
- Add API compatibility layer: status adapters can read DB state or CRD status.
- Add metrics parity checks (launch latency, failure rate, cleanup duration).

Exit criteria:

- All current user-facing status values map to normalized condition reasons.
- Existing CI and smoke tests remain green.

### Phase 1: Introduce `LabInstance` CRD (shadow mode)

- Add CRD definition and register in cluster.
- Backend creates CRD in shadow mode while still performing current orchestration.
- Operator watches CRD and computes status only (no write actions to workloads yet).
- Compare operator-derived status with backend status and log drift.

Exit criteria:

- Shadow status drift < 1% across 7 days.
- No increase in launch failure SLO.

### Phase 2: VM lifecycle handoff

- Enable operator ownership for VM resource creation/update/delete.
- Backend switches VM start/stop paths to create/update/delete `LabInstance`.
- Keep backend fallback path behind feature flag (`ENABLE_VM_OPERATOR_FALLBACK=1`).

Exit criteria:

- 95th percentile VM launch time is within agreed SLO band.
- VM cleanup success >= 99%.

### Phase 3: Image import/finalize handoff with `LabImageImport`

- Add `LabImageImport` CRD and controller.
- Move CDI DataVolume copy/normalize/checksum/retry logic into controller.
- Keep admin upload API unchanged; it writes `LabImageImport` and reads status.

Exit criteria:

- Upload completion/finalize success >= current baseline.
- No task orphan leaks after controller restarts.

### Phase 4: Container lifecycle handoff

- Extend `LabInstance` to support `spec.workload.kind=container`.
- Operator owns container pod/service lifecycle and readiness conditions.

Exit criteria:

- Container connect readiness false-positive rate < 1%.

### Phase 5: Remove imperative orchestration code

- Remove backend paths that directly create VM/container runtime resources.
- Keep only CRD write/read adapters and policy validation.
- Retain emergency feature flag rollback for one full release.

Exit criteria:

- Two consecutive releases with operator path as default and no rollback.

## CRD schema boundaries

### `LabInstance` (`labs.bretter.io/v1alpha1`)

Spec ownership:

- `spec.owner.username`: authenticated requester.
- `spec.templateRef.name`: template identifier.
- `spec.workload.kind`: `vm` or `container`.
- `spec.workload.consoleProvider`: `spice`, `guacamole_vnc`, `guacamole_rdp`.
- `spec.resources`: cpu/memory/storage requests.
- `spec.network.mode`: `bridge`, `masquerade`, etc.
- `spec.idleTimeoutMinutes`: effective timeout cap after policy enforcement.

Status ownership:

- `status.phase`: normalized high-level phase (`Pending`, `Building`, `Starting`, `Running`, `Stopping`, `Failed`).
- `status.conditions[]`: reasoned conditions (`ResourcesReady`, `ConsoleReady`, `ConnectReady`, `CleanupComplete`).
- `status.runtime`: pod names, service names, endpoint hints, last transition times.
- `status.failure`: machine-readable error code + message for UI/admin.

Minimal example:

```yaml
apiVersion: labs.bretter.io/v1alpha1
kind: LabInstance
metadata:
  name: vm-admin-91bca72a
  namespace: labs
spec:
  owner:
    username: admin
  templateRef:
    name: windows11-rdp
  workload:
    kind: vm
    consoleProvider: guacamole_rdp
  resources:
    cpuMillicores: 2000
    memoryMiB: 8192
    diskGiB: 80
  network:
    mode: bridge
  idleTimeoutMinutes: 30
status:
  phase: Starting
  conditions:
    - type: ConsoleReady
      status: "False"
      reason: RdpPortNotReady
      message: Waiting for RDP listener on guest
      lastTransitionTime: "2026-03-20T08:00:00Z"
```

### `LabImageImport` (`labs.bretter.io/v1alpha1`)

Spec ownership:

- `spec.requestedBy`: admin username.
- `spec.source.filename`: uploaded artifact name.
- `spec.source.pvc`: upload scratch PVC reference.
- `spec.target.imageId`: resulting image id.
- `spec.transform.format`: `raw` or `qcow2` policy.
- `spec.retries.maxAttempts`: bounded retry policy.

Status ownership:

- `status.phase`: `Queued`, `Importing`, `Finalizing`, `Completed`, `Failed`.
- `status.progress.percent`: best-effort progress.
- `status.artifacts`: checksum, source PVC, canonical filename.
- `status.lastError`: operator-provided terminal error.

Minimal example:

```yaml
apiVersion: labs.bretter.io/v1alpha1
kind: LabImageImport
metadata:
  name: import-7e40c3e9
  namespace: labs
spec:
  requestedBy: admin
  source:
    filename: win11.vdi
    pvc: upload-7e40c3e9
  target:
    imageId: img-win11-20260320
  transform:
    format: raw
  retries:
    maxAttempts: 3
status:
  phase: Finalizing
  progress:
    percent: 62
    detail: Finalizing image format/checksum on cluster
```

## Controller design and ownership

### `LabInstanceController`

- Reconcile `LabInstance` objects by generation.
- Apply finalizer `labs.bretter.io/finalizer`.
- Create/update runtime resources with deterministic labels:
  - `labs.bretter.io/instance-id`
  - `labs.bretter.io/owner`
  - `labs.bretter.io/workload-kind`
- Write condition transitions and clear stale condition reasons.
- On delete, teardown all owned resources and remove finalizer.

### `LabImageImportController`

- Reconcile upload/import/finalize workflow by phase transitions.
- Control retries with exponential backoff in status.
- Own temporary job/pod/PVC lifecycle and cleanup on completion/failure.
- Produce stable failure codes for frontend/admin error surfacing.

## Compatibility and migration mechanics

- Keep existing REST endpoints and payloads stable.
- Introduce an internal storage adapter:
  - `orchestration_backend=db` (current)
  - `orchestration_backend=crd` (target)
  - `orchestration_backend=dual` (migration)
- In `dual`, backend writes DB + CRD and reads CRD-first with DB fallback.
- Backfill script maps active DB instances into `LabInstance` resources.

## CI/CD and rollout controls

- Add schema validation gate for CRDs (`kubectl apply --dry-run=server -k deploy/crds`).
- Add controller unit tests for reconcile idempotency and finalizer behavior.
- Add integration smoke:
  - create `LabInstance` -> running -> delete -> fully cleaned.
  - create `LabImageImport` -> completed.
- Add canary gate:
  - enable operator path for admin team only via namespace/label selector.

## Rollback strategy

- Feature flags:
  - `ENABLE_LABINSTANCE_OPERATOR`
  - `ENABLE_IMAGEIMPORT_OPERATOR`
  - `ENABLE_VM_OPERATOR_FALLBACK`
- Rollback path:
  1. Disable operator flags.
  2. Backend resumes imperative orchestration.
  3. Existing CRDs remain for audit/history and are drained by cleanup job.

## Operational SLOs and hard metrics

- VM launch success rate >= 99% over rolling 24h.
- Container launch success rate >= 99%.
- Median VM connect-ready <= current baseline + 15%.
- Image import finalize failures reduced by 30% vs imperative baseline.
- Orphan runtime resources older than 2h = 0.

## Security requirements during migration

- No plaintext secret material in CRD spec/status.
- Reference Kubernetes Secrets by name/key only.
- Controller service accounts least privilege:
  - CRUD only for managed resource kinds in managed namespace.
- Preserve existing auth/session/token hardening in backend.

## Deliverables checklist

- [ ] CRD manifests checked in under `deploy/crds/`.
- [ ] Controller repo/module skeleton with reconcile loop tests.
- [ ] Backend adapter layer (`db|dual|crd`) with parity tests.
- [ ] End-to-end canary in CI + post-deploy smoke.
- [ ] Operator runbook and rollback SOP.

