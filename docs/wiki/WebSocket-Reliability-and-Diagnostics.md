# WebSocket Reliability and Diagnostics

Last reviewed: March 26, 2026.

This page covers the websocket reliability signals for VM/container connect flows and the one-command diagnostics path.

## Metrics emitted by backend

Backend `/metrics` now includes websocket proxy telemetry:

- `blabs_ws_proxy_handshake_total{resource_type,result}`
- `blabs_ws_proxy_disconnect_total{resource_type,direction,code}`
- `blabs_ws_proxy_active_connections{resource_type}`
- `blabs_ws_proxy_session_seconds_bucket{resource_type,le}`

`resource_type` is `vm` or `container`.

## Monitoring wiring

Setup applies a backend ServiceMonitor:

- `ServiceMonitor/bretter-backend` in `monitoring`
- target: `Service/bretter-backend` in runtime namespace
- scrape path: `/metrics`

Setup also adds websocket alerts in `PrometheusRule/bretter-labs-alerts`:

- `BretterWebsocketHandshakeFailuresHigh`
- `BretterWebsocketDisconnectBurst`

## One-command diagnostics

Run:

```bash
NAMESPACE=labs ./scripts/diagnose_connectivity.sh
```

What it collects:

- backend/frontend deployment status
- runtime VM/container pod snapshot
- ServiceMonitor + websocket alert-rule presence
- backend `/health` and websocket metrics snapshot via port-forward
- backend websocket/connect log sample

Common overrides:

```bash
NAMESPACE=labs MONITORING_NAMESPACE=monitoring ./scripts/diagnose_connectivity.sh
LOCAL_PORT=18081 WAIT_SECONDS=45 ./scripts/diagnose_connectivity.sh
```

## UI connect readiness behavior

User page container connect button is now gated by `/user/containers/{id}/connect-readiness`.

- Running container + readiness `ready=true`: connect button enabled.
- Running container + readiness `ready=false`: button disabled and detail is shown.
- Non-running container states: button remains disabled.

## Related pages

- [Operations Runbook](Operations-Runbook)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Console Providers and RDP Operations](Console-Providers-and-RDP-Operations)
