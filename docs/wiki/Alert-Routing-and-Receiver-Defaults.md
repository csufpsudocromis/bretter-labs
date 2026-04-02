# Alert Routing and Receiver Defaults

This page documents the Alertmanager defaults applied by `scripts/setup.sh` and how to wire a real receiver for production.

## Default behavior

When monitoring is enabled, setup now writes explicit Alertmanager routing defaults:

- Default receiver: `ALERTMANAGER_DEFAULT_RECEIVER_NAME` (default `null-receiver`)
- Grouping defaults:
  - `ALERTMANAGER_ROUTE_GROUP_BY` (default `alertname,namespace`)
  - `ALERTMANAGER_ROUTE_GROUP_WAIT` (default `30s`)
  - `ALERTMANAGER_ROUTE_GROUP_INTERVAL` (default `5m`)
  - `ALERTMANAGER_ROUTE_REPEAT_INTERVAL` (default `4h`)

This gives deterministic alert routing even before external notification channels are configured.

## Enabling webhook delivery

To route selected alerts to a webhook receiver:

1. Create a webhook URL secret in the app namespace:

```bash
kubectl -n labs create secret generic bretter-alertmanager-webhook \
  --from-literal=url='https://alerts.example.internal/hooks/bretter' \
  --dry-run=client -o yaml | kubectl apply -f -
```

2. Run setup with webhook receiver enabled:

```bash
ALERTMANAGER_WEBHOOK_RECEIVER_ENABLED=1 \
ALERTMANAGER_WEBHOOK_RECEIVER_NAME=ops-webhook \
ALERTMANAGER_WEBHOOK_SECRET_NAME=bretter-alertmanager-webhook \
ALERTMANAGER_WEBHOOK_SECRET_KEY=url \
ALERTMANAGER_WEBHOOK_MATCHERS='severity="critical"' \
./scripts/setup.sh
```

If the secret/key is missing when webhook routing is enabled, setup fails fast.

## Production guidance

- Keep an explicit default receiver (do not rely on chart implicit defaults).
- Keep paging webhook routes focused on high-signal alerts (default matcher is `severity="critical"`).
- Route lower-signal warning alerts to non-paging channels (dashboards, ticketing, or email digests).
- Keep receiver URLs in Kubernetes secrets, not committed values files.
- VM launch/RDP/upload burn-rate alerts now include `runbook_url` annotations that point to the Operations Runbook triage sections.
- Validate post-deploy health and alert wiring with:

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

## Troubleshooting

- Receiver not firing:
  - Check rendered Alertmanager config in the monitoring release values.
  - Confirm matcher syntax in `ALERTMANAGER_WEBHOOK_MATCHERS`.
- Setup fails on webhook config:
  - Verify `ALERTMANAGER_WEBHOOK_SECRET_NAME` exists in the target namespace.
  - Verify `ALERTMANAGER_WEBHOOK_SECRET_KEY` points to non-empty data.
