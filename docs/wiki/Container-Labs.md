# Container Labs

Last reviewed: March 6, 2026.

## Overview

Container labs are launched from admin-managed container templates and run as isolated per-instance pods.

User flow:

1. User clicks `Start Lab`.
2. Status progresses through runtime stages.
3. `Connect` enables only when app readiness is satisfied.
4. User deletes lab when done.

## Admin workflow

### 1) Register container images (`/admin/container-images`)

- Supports Docker Hub, other OCI registries, or direct image refs.
- Optional actions per image:
  - `Scan` (queues image scan)
  - `Pre-pull` (warms image on cluster nodes)
- Image deletion is blocked while templates still reference it.

### 2) Create container templates (`/admin/container-templates`)

Template fields include:

- Name, description, image
- CPU cores, memory MB, container port
- Access strategy (`nodeport` or `ingress`)
- Network mode (`bridge`, `isolated`, `none`, `unrestricted`)
- Readiness checks (TCP/HTTP, path, expected status, optional success path)
- Startup timeout and dependency checks
- Security flags (run as non-root, read-only root filesystem)
- Command/args/env overrides
- Idle timeout
- Enabled/disabled toggle

Edit flow uses the top form area (same as create form) and returns to create mode after save/cancel.

## Runtime statuses

Common stage labels shown to users:

- `Queued`
- `Pending`
- `Building`
- `Starting`
- `Running`
- `Completed`
- `Failed`

Notes:

- `Connect` is disabled until status is `Running`.
- Pending may include reason such as waiting for available resources.
- Startup errors show status detail and launch diagnostics.

## One active lab rule

The global launch rule applies across VM + container:

`You already have a virtual lab running. Delete the current lab before starting a new one.`

## Network mode behavior (template-level)

- `bridge`: normal DNS/web egress for typical app labs
- `isolated`: restricted/deny egress policy
- `none`: no egress policy
- `unrestricted`: no network policy restrictions

Choose per template based on lab security model.

## Troubleshooting quick checks

Container stuck in `Starting`:

```bash
kubectl -n labs get pods | rg '^ct-'
kubectl -n labs describe pod <ct-pod-name>
kubectl -n labs logs <ct-pod-name> --tail=200
```

Connect proxy failures:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'container connect proxy|websocket|upstream|error'
kubectl -n labs get svc,endpoints | rg '<ct-pod-name-prefix>'
```

## Related pages

- [Operations Runbook](Operations-Runbook.md)
- [Security and Auth](Security-and-Auth.md)
