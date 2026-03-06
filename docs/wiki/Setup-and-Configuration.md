# Setup and Configuration

Last reviewed: March 6, 2026.

## Quick install

```bash
git clone https://github.com/csufpsudocromis/bretter-labs.git
cd bretter-labs
./scripts/setup.sh
```

## High-value setup variables

All values are read from environment variables by `scripts/setup.sh`.

Core:

- `NAMESPACE` (default `labs`)
- `NODE_EXTERNAL_HOST`
- `PUBLIC_SCHEME` (`https` recommended)
- `TLS_ENABLED`
- `TLS_SECRET_NAME`

Storage:

- `VM_STORAGE_CLASS`
- `GOLDEN_IMAGES_HOSTPATH`
- `BACKEND_DATA_HOSTPATH`
- `POSTGRES_DATA_HOSTPATH`

Container runtime:

- `CONTAINER_INGRESS_ENABLED`
- `CONTAINER_INGRESS_BASE_DOMAIN`
- `CONTAINER_ALLOWED_REGISTRIES`
- `CONTAINER_SCAN_ENABLED`
- `CONTAINER_START_QUEUE_ENABLED`

VM runtime:

- `WINDOWS_EFI_ENABLED`, `WINDOWS_MACHINE_TYPE`
- `LINUX_EFI_ENABLED`, `LINUX_MACHINE_TYPE`
- `VM_NET_BACKEND`
- `CPU_MANAGER_STATIC`

Monitoring/ops:

- `ENABLE_MONITORING`
- `ENABLE_AUTOCLEANUP`
- `AUTOCLEANUP_NODEFS_WARN_PCT`, `AUTOCLEANUP_NODEFS_CRITICAL_PCT`, `AUTOCLEANUP_NODEFS_EMERGENCY_PCT`
- `AUTOCLEANUP_PVC_WARN_PCT`, `AUTOCLEANUP_PVC_CRITICAL_PCT`, `AUTOCLEANUP_PVC_EMERGENCY_PCT`
- `RUN_POST_DEPLOY_SYNTHETIC_CHECK`

## Example

```bash
NAMESPACE=labs \
NODE_EXTERNAL_HOST=10.68.49.250 \
PUBLIC_SCHEME=https \
TLS_ENABLED=1 \
VM_STORAGE_CLASS=longhorn-r1 \
ENABLE_MONITORING=1 \
./scripts/setup.sh
```

## Runtime/admin settings pages

- `/admin/settings/storage`: writable storage overrides + validation
- `/admin/settings/runtime`: read-only runtime/env/drift visibility
- `/admin/settings/appearance`: theme, contrast targets, background upload, font sizing
- `/admin/settings/sso`: SSO provider config

## Notes

- Runtime settings page is read-only by design.
- Storage settings page supports clearing overrides back to env defaults.
- Login/background asset should be locally hosted for reliability.

## Related pages

- [Operations Runbook](Operations-Runbook.md)
- [Security and Auth](Security-and-Auth.md)
