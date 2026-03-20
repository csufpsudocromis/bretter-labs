# Console Providers and RDP Operations

Last reviewed: March 19, 2026.

This page documents VM console provider behavior (`spice`, `guacamole`, `guacamole_rdp`) and RDP-specific operational requirements.

## Provider summary

### `spice`

- Connect target: `spice-embed.html`
- Uses generated SPICE password in connect URL/cookie flow.
- Good default for broad VM support.

### `guacamole` (VNC)

- Connect target: `vnc.html`
- Browser-based VNC via Guacamole tunnel.
- Useful when RDP is not required and browser compatibility is the priority.

### `guacamole_rdp`

- Connect target: `rdp.html` + `rdp-tunnel`
- Browser-based RDP via Guacamole tunnel.
- Requires guest RDP service readiness before connect is enabled.

## Admin template configuration

Path:

- `/admin/templates`

Template options:

- `Console provider` selector includes:
  - `SPICE`
  - `Guacamole (VNC)`
  - `Guacamole (RDP)`
- When `Guacamole (RDP)` is selected, template form shows:
  - `RDP username` (optional)
  - `RDP password` (optional)

Security notes:

- RDP defaults are template-scoped, not global.
- Passwords are stored as encrypted secret values in backend storage.
- APIs expose password as configured/not-configured metadata, not plaintext.

## Runtime requirements for Guacamole RDP

Platform requirements:

- `VM_NET_BACKEND=user` (required for deterministic local RDP forwarding path in runner).
- Runner image must include `guac-rdp-server.js` and `rdp.html`.

Guest requirements:

- Guest OS must have RDP service enabled and reachable from runner.
- Initial boot/login policy must allow desktop frame generation.

## Connect gating and status behavior

For `guacamole_rdp` templates:

- VM may be process-running but still shown as `Starting` until `/rdp-ready` passes.
- `Connect` remains disabled until readiness succeeds.
- Connect-token endpoint returns `409` while waiting:
  - `VM process started; waiting for RDP service.`

This prevents users from opening non-functional RDP tabs during boot/service warm-up.

## Operational checks

Runtime status checks:

```bash
kubectl -n labs get pods -o wide | rg '^vm-|^virt-launcher-'
kubectl -n labs logs deploy/bretter-backend --tail=500 | rg -i 'rdp-ready|connect-token|guacamole|409'
```

Runner-level checks:

```bash
kubectl -n labs logs <vm-runner-pod> --tail=500 | rg -i 'guac|rdp|tunnel|disconnect|ready'
```

Template/config checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'console_provider|rdp_default|encrypt|decrypt'
```

## Common symptoms

`Connected. Waiting for desktop frame...`:

- Tunnel is up but guest desktop frames are not ready.
- Check guest RDP service and login state.

Immediate disconnect after connect attempt:

- Usually readiness race or guest RDP endpoint unavailable.
- Recheck VM status stage and backend `rdp-ready` logs.

`Guacamole is not defined` in browser:

- Frontend bundle/runtime assets mismatch.
- Confirm latest frontend rollout and hard-refresh browser.

## Related pages

- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Template Best Practices](Template-Best-Practices)
- [Operations Runbook](Operations-Runbook)
- [Error Catalog](Error-Catalog)
