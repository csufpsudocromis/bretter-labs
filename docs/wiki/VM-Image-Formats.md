# VM Image Formats

## Allowed upload formats

- `.vhd`
- `.vhdx`
- `.qcow`
- `.qcow2`
- `.vdi`

## Normalization behavior

- QCOW uploads are normalized to raw during finalization.
- The finalized disk is used for template-backed clone workflows.

## Admin UI text

Use this wording in `/admin/images`:

`Allowed: .vhd/.vhdx, .qcow/.qcow2, .vdi. QCOW is auto-converted to raw.`

## Upload troubleshooting

- Verify cluster storage free space before upload.
- If browser upload reaches 100% and pauses, check finalization status in backend logs.
- If conversion fails, verify source image integrity and format validity.
