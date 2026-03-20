# Container Labs

Last reviewed: March 19, 2026.

## Overview

Container labs are launched from admin-managed templates and run as isolated per-instance Kubernetes pods (`ct-*`).

Primary flow:

1. User clicks `Start Lab`.
2. Backend creates a container instance record and launches a pod/service.
3. UI stage progresses through launch/readiness states.
4. `Connect` enables only when the instance is `Running` and has an active access URL.
5. User deletes the lab when done.

Container labs appear in the same user panels as VM labs:

- `Available Virtual Labs` (template catalog)
- `My Running Labs` (active workload list)

## Admin workflow

### 1) Register images (`/admin/container-images`)

- Source input is a direct OCI image reference (for example `ghcr.io/org/app:v1.2.3`).
- Name auto-derives from image reference if left blank.
- Per-image actions:
  - `Scan`: queue vulnerability scan
  - `Pre-pull`: warm image across nodes
  - `Edit`: change display name/image reference
  - `Delete`: remove image (blocked when templates still reference it)

Signature policy behavior:

- When signature verification is enabled, image registration runs `cosign verify`.
- If an image has no signatures, registration is currently warning-only by policy:
  - UI message: `Container image added. Warning: Image has no signatures.`
- Other signature verification failures remain hard errors.

Registry policy behavior:

- Allowed registries are controlled by `BLABS_CONTAINER_ALLOWED_REGISTRIES`.
- In production profile, local/dev image references are blocked.

### 2) Create templates (`/admin/container-templates`)

Template fields include:

- Name, description, image
- CPU/memory sizing
- Exposed container port
- Access strategy (`nodeport` or `ingress`)
- Network mode (`bridge`, `isolated`, `none`, `unrestricted`)
- Readiness checks (TCP/HTTP, path, expected status, optional success path)
- Dependency checks (host/port/protocol)
- Startup timeout
- Security flags (`run_as_non_root`, `read_only_root_fs`)
- Optional command/args/env overrides
- Idle timeout
- Enabled/disabled toggle

## Runtime statuses and connect behavior

Common stage labels shown to users:

- `Queued`
- `Pending`
- `Building`
- `Starting`
- `Running`
- `Completed`
- `Failed`

Connect button behavior:

- Disabled while lab is not ready.
- Enabled only when status is `Running` and runtime access URL is present.
- Startup diagnostics are shown only when startup fails (to reduce normal-path noise).

## Safety controls

- One-active-lab rule applies across VM + container workloads.
- Namespace/team quotas can block starts with clear quota feedback.
- Idle timeout policy applies to running labs and connect views.

## Quick triage

Container stuck in `Starting`:

```bash
kubectl -n labs get pods | rg '^ct-'
kubectl -n labs describe pod <ct-pod-name>
kubectl -n labs logs <ct-pod-name> --tail=200
kubectl -n labs get svc,endpoints | rg 'ctsvc-|ct-'
```

Connect path errors:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'container connect proxy|websocket|upstream|timeout|error'
kubectl -n labs get svc,endpoints | rg '<ct-pod-name-prefix>'
```

Signature/registration issues:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'signature|cosign|verify|no signatures found'
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED|BLABS_CONTAINER_SIGNATURE_KEY_REF'
```

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Security and Auth](Security-and-Auth)
- [Template Best Practices](Template-Best-Practices)
- [Scaling and Quotas](Scaling-and-Quotas)
