# Golden Image and ISO Operations

Last reviewed: April 14, 2026.

This page documents admin workflows for ISO media and golden VM image lifecycle management.

## Scope and roles

- Platform admins can manage catalog-scope images/ISOs across all enabled lab namespaces.
- Namespace admins can manage namespace-scoped images/ISOs in namespaces they are assigned.
- Standard users launch template-backed VM clones and do not mutate golden source disks.

## ISO image catalog (`/admin/iso-images`)

Use this page to manage install/update media separately from VM disks.

Supported flow:

1. Upload ISO with name, filename, and description.
2. Track upload progress from browser to cluster finalization.
3. Reuse uploaded ISO as mounted CD media when creating/editing golden VM images.

Operational guidance:

- Keep bootable installer media and non-bootable driver/tools media clearly named.
- Use descriptions to mark purpose (`installer`, `virtio-drivers`, `tools`, etc.).

## Golden image lifecycle (`/admin/images`)

### Upload existing disk image

- Upload `.vhd/.vhdx`, `.qcow/.qcow2`, or `.vdi`.
- Finalization normalizes/imports to cluster storage.

### Create image from scratch

- Create from ISO by selecting:
  - OS family (`windows` or `linux`)
  - CPU / RAM defaults
  - Disk size
  - Optional mounted ISO as CD

### Edit and update image

- `Update VM` launches an admin update instance for image maintenance.
- `Save VM Update` stops/deletes the update pod and commits the updated golden disk for future clone launches.

### Copy image

- `Create Copy` clones an existing golden image into a new name/filename.
- Copy workflow supports namespace-scoped or shared catalog availability.
- Progress is reported during cluster-side copy execution.

## Runtime behavior and safety model

- User-launched VMs run from clone/snapshot overlays and should not write back to the golden source image.
- Admin update sessions are the write path for golden image changes.
- Template pre-clone/warm pool entries are reconciled against current template/image settings after updates.

## Troubleshooting checks

```bash
kubectl -n labs get pods | rg -i 'import|upload|copy|finaliz|cdi'
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'image|iso|upload|finaliz|copy|boot|cdrom'
kubectl -n labs get pvc | rg -i 'img-src|pool-|golden|upload'
```

## Related pages

- [VM Image Formats](VM-Image-Formats)
- [Template Best Practices](Template-Best-Practices)
- [Operations Runbook](Operations-Runbook)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
