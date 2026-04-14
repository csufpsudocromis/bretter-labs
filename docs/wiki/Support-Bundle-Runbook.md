# Support Bundle Runbook

Use the support bundle script to capture high-signal diagnostics during incidents.

## Command

```bash
NAMESPACE=labs ./scripts/export_support_bundle.sh
```

Optional overrides:

- `MONITORING_NAMESPACE` (default `monitoring`)
- `OUT_DIR` (default `artifacts/support-bundles`)
- `TAIL_LINES` (default `500`)
- `EVENT_LIMIT` (default `250`)
- `ALERTMANAGER_API_URL` (default internal Alertmanager API)

Example:

```bash
NAMESPACE=labs \
MONITORING_NAMESPACE=monitoring \
TAIL_LINES=800 \
EVENT_LIMIT=400 \
./scripts/export_support_bundle.sh
```

## Collected Artifacts

- Namespace inventory (`deploy`, `pods`, `services`, `PVCs`, `jobs`, `cronjobs`)
- Recent namespace events
- Deployment logs (`bretter-backend`, `bretter-frontend`, `bretter-postgres`, `bretter-labimageimport-controller`)
- Failed job descriptions and logs
- Pod/job/PVC JSON snapshots
- Alertmanager API alert snapshot (when reachable)

Output:

- Directory: `artifacts/support-bundles/support-bundle-<namespace>-<timestamp>/`
- Archive: `artifacts/support-bundles/support-bundle-<namespace>-<timestamp>.tar.gz`

## Usage Guidance

- Generate immediately after user-impacting failures (launch/connect/import incidents).
- Attach the `.tar.gz` artifact to incident tickets.
- Redact credentials/secrets before sharing outside trusted operator channels.
