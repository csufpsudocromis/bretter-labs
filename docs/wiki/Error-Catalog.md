# Error Catalog

Last reviewed: March 16, 2026.

Use this page for exact UI/API errors and first-response fixes.

## Login and auth

### `No admin user exists and BLABS_ADMIN_DEFAULT_PASSWORD is empty`

Meaning:

- Backend startup is fail-fast when first bootstrap secret is missing and no admin row exists.

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=200 | rg -n 'BLABS_ADMIN_DEFAULT_PASSWORD|No admin user exists'
```

Fix:

- Set `ADMIN_BOOTSTRAP_PASSWORD` during initial deploy (`scripts/setup.sh` handles this automatically by default).
- Re-run deploy phase, then verify backend rollout.

### `Invalid production startup configuration`

Meaning:

- `BLABS_PRODUCTION_PROFILE=true` is set and one or more required production settings are unsafe or missing.

Common causes:

- Runtime secret backing `BLABS_SECRETS_ENCRYPTION_KEY` is missing/weak.
- `BLABS_CORS_ALLOWED_ORIGINS` contains localhost/127.0.0.1.
- `BLABS_KUBE_NODE_SELECTOR_VALUE` is empty.
- Insecure TLS toggles are enabled.

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -n 'Invalid production startup configuration|BLABS_'
python3 scripts/validate_production_profile.py --strict -f deploy/helm/values-production.yaml
```

Fix:

- Set required production values and redeploy.
- Keep `PRODUCTION_PROFILE=1` and use strict validator output as the source of truth.

### `Login failed`

Likely causes:

- Missing frontend origin in CORS allowlist
- HTTPS cookie flags enabled but request is HTTP
- Session cookie domain/origin mismatch

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -i 'login|auth|cors|cookie|forbidden|unauthorized'
```

Fix:

- Add correct frontend origin to `BLABS_CORS_ALLOWED_ORIGINS`
- Keep scheme/origin consistent (`https://...`)
- Re-run deployment and test in a fresh browser profile

If using production profile:

- Remove localhost/127.0.0.1 origins from `BLABS_CORS_ALLOWED_ORIGINS`.

## VM/image ingest

### `failed to normalize image format`

Likely causes:

- Unsupported extension/format
- Conversion tool missing in runtime path
- Corrupt or partial upload

Fix:

- Use supported formats (`.vhd`, `.vhdx`, `.qcow`, `.qcow2`, `.vdi`)
- Re-upload image and check backend convert logs
- Verify node disk free space

### `failed to convert qcow to raw`

Likely causes:

- `qemu-img` failure from invalid qcow file
- No disk space on conversion path

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'qemu-img|convert|qcow|raw|space|error'
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
```

### VM boot errors: `No bootable device`, `grub>`, `grub rescue>`

Likely causes:

- Wrong firmware/machine type for guest OS
- Source image not generalized/bootable
- Incomplete conversion chain

Fix:

- Verify OS template settings (EFI + machine type)
- Re-import known-good image
- Validate template network/boot config before enabling for users

## Storage and scheduling

### `A PVC in namespace labs is above 95% usage`

Meaning:

- Storage use exceeded alert threshold. New labs/uploads can fail or stall.

Immediate action order:

1. Delete stale labs/uploads.
2. Prune old images not in use.
3. Expand PVC/underlying node storage.

Checks:

```bash
kubectl -n labs get pvc
kubectl -n labs describe pvc
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
```

### Labs stuck in `Pending` or `Starting`

Likely causes:

- CPU/RAM quota exhaustion
- Node pressure
- Pending PVC attach/provision

Checks:

```bash
kubectl -n labs get pods | rg 'Pending|ContainerCreating'
kubectl -n labs describe pod <pod-name>
kubectl -n labs get resourcequota
```

## Connect path

### `container connect proxy failed: ('Connection aborted.', RemoteDisconnected(...))`

Likely causes:

- Container app not ready yet
- Service/endpoints not ready
- Upstream closed before first response

Checks:

```bash
kubectl -n labs get svc,endpoints | rg 'ctsvc-|ct-'
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'connect proxy|remote|disconnect|timeout'
```

### Repeated console warning: `Received message from unexpected origin ...`

Meaning:

- Connect frame/postMessage origin mismatch between allowed origins and actual connect origin.

Fix:

- Allow both expected app/connect origins in backend allowlist and frontend bridge checks.

### `This application requires a secure connection (HTTPS)`

Meaning:

- App or middleware rejects plain HTTP.

Fix:

- Use HTTPS connect URLs only.
- Ensure TLS termination and forwarded proto headers are correct.

## API/schema operations

### `StorageClass <name> lookup failed: Forbidden`

Meaning:

- Backend service account does not have permissions for storageclass reads.

Fix:

- Add minimal RBAC read permission on `storageclasses` if validation endpoint needs it.

### Duplicate OpenAPI operation ID warnings

Meaning:

- Route decorators share operation IDs.

Fix:

- Set unique `operation_id` values per route and add CI check.

## CI/automation

### `starlette.testclient module requires the httpx package to be installed`

Meaning:

- Test environment did not install `httpx` in the virtual environment used by `scripts/ci_guardrails.sh`.

Fix:

- Ensure CI install step includes `httpx` explicitly.
- Confirm with:

```bash
.venv/bin/python -c "import httpx; from fastapi.testclient import TestClient"
```

Operational note:

- Re-running a failed historical workflow run may still fail because it executes the old commit.
- Trigger a fresh run from latest `main`.

## Quick triage bundle

```bash
kubectl -n labs get pods -o wide
kubectl -n labs get events --sort-by=.lastTimestamp | tail -n 80
kubectl -n labs logs deploy/bretter-backend --tail=400
kubectl -n labs logs deploy/bretter-frontend --tail=200
```

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
