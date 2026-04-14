# VM Image Formats

Last reviewed: March 19, 2026.

## Allowed upload formats

- `.vhd`
- `.vhdx`
- `.qcow`
- `.qcow2`
- `.vdi`

Admin UI wording in `/admin/images`:

`Allowed: .vhd/.vhdx, .qcow/.qcow2, .vdi. QCOW is auto-converted to raw.`

## Upload and normalization pipeline

1. Browser uploads file to backend.
2. Backend creates upload task and writes staged source file/PVC.
3. Finalization runs on-cluster (copy/normalize/import).
4. QCOW/QCOW2 images are converted to raw when required for runtime compatibility.
5. Image is published into golden image storage and becomes template-selectable.

Finalization may use one of these paths depending on environment and flags:

- Direct storage path finalize
- Legacy upload/finalize fallback
- CDI DataVolume import flow

## Recommended source image guidance

- Prefer clean, shut-down source disks (avoid suspended/hibernated state).
- Keep one OS boot mode per template set (Windows UEFI/q35 vs Linux BIOS/pc).
- Use predictable filenames and versioned names for easier rollback.

## Pre-conversion examples (optional)

If you need to normalize before upload:

```bash
qemu-img convert -p -f vdi -O qcow2 source.vdi normalized.qcow2
qemu-img convert -p -f qcow2 -O raw normalized.qcow2 disk.raw
```

## Troubleshooting

### `failed to convert qcow to raw`

- Source image may be corrupt or malformed.
- Node/PVC disk may be full during conversion.
- Validate with `qemu-img info` and retry after freeing space.

### `failed to normalize image format`

- Unsupported extension or content mismatch.
- Re-export from source hypervisor and re-upload.

### Upload reaches 100% then pauses

- Usually cluster finalization is still running.
- Check backend logs for upload task progress/errors.
- Confirm storage pressure is not blocking conversion jobs.

### `BLABS_ERROR=input missing: <filename>`

Likely causes:

- Finalize job cannot find staged source file.
- Upload/finalize path mismatch between direct and fallback flow.
- Staging file was cleaned before finalize consumed it.

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'input missing|finaliz|upload|copy|normalize'
kubectl -n labs get jobs -n labs | rg -i 'image|upload|finaliz|copy'
```

### Finalize/import appears stuck for long time

Likely causes:

- PVC/storage pressure
- conversion/import pod restart/backoff
- insufficient temporary space for conversion

Checks:

```bash
kubectl -n labs get pvc
kubectl -n labs get pods | rg -i 'upload|import|cdi|pending|error'
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
```

### Upload sizing baseline

- Minimum upload/import PVC size is governed by `BLABS_MIN_UPLOAD_PVC_GIB` (default `80`).
- If large source images fail mid-finalize, increase this value and retry.

## Related pages

- [Golden Image and ISO Operations](Golden-Image-and-ISO-Operations)
- [Operations Runbook](Operations-Runbook)
- [Setup and Configuration](Setup-and-Configuration)
