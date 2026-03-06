# VM Image Formats

Last reviewed: March 6, 2026.

## Allowed upload formats

- `.vhd`
- `.vhdx`
- `.qcow`
- `.qcow2`
- `.vdi`

Admin UI wording in `/admin/images`:

`Allowed: .vhd/.vhdx, .qcow/.qcow2, .vdi. QCOW is auto-converted to raw.`

## Upload and normalization pipeline

1. File uploads through direct upload path (with fallback).
2. Backend creates/updates upload task state.
3. Finalization runs in cluster (copy/normalize).
4. QCOW/QCOW2 images are converted to raw for runtime compatibility.
5. Final image is saved to golden image PVC/path and becomes template-selectable.

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

- Unsupported or invalid extension/format content mismatch.
- Re-export from source hypervisor and re-upload.

### Upload reaches 100% then pauses

- Usually cluster finalization still running.
- Check backend logs for upload task progress/errors.
- Confirm storage pressure is not blocking conversion jobs.

## Related pages

- [Operations Runbook](Operations-Runbook.md)
- [Setup and Configuration](Setup-and-Configuration.md)
