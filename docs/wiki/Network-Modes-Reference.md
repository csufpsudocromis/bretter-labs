# Network Modes Reference

Last reviewed: March 19, 2026.

This page documents network mode behavior for VM and container templates.

## Supported template modes

### `bridge` (recommended default)

Behavior:

- Runtime pod keeps policy enforcement.
- Egress allowed for DNS + HTTP/HTTPS only.
- Ingress allowed for required app/console port.

Use when:

- Labs need internet/package access but should stay constrained.

### `isolated`

Behavior:

- Ingress still allowed for connect path.
- Egress denied.

Use when:

- Fully offline labs or strict outbound isolation.

### `none`

Behavior:

- Equivalent restrictive behavior to `isolated` for egress.
- Ingress remains limited to connect/application port.

Use when:

- You want explicit no-egress intent in template config.

### `unrestricted`

Behavior:

- Per-instance NetworkPolicy is not enforced for that lab.
- Pod uses normal cluster networking with broad reachability.

Use when:

- App requires broad east-west or custom outbound access.

Risk:

- Highest blast radius; use sparingly.

## VM legacy value mapping

- Legacy VM template value `host` is normalized to `unrestricted`.

## VM runner backend mode (platform-level)

Set by `VM_NET_BACKEND` in `setup.sh`:

- `user`: user-mode networking path
- `tap-nat`: tap/NAT path (higher performance, more host coupling)

Important console coupling:

- `guacamole_rdp` VM console provider requires `VM_NET_BACKEND=user` for deterministic local RDP forwarding inside runner pod.
- If `guacamole_rdp` is used with non-`user` backend mode, runner startup fails fast by design.

This is separate from template `network_mode`; both affect final behavior.

## Container exposure strategy interaction

Container templates also choose exposure strategy:

- `nodeport`
- `ingress`

Recommended production posture:

- Prefer `ingress` with TLS for user-facing apps.
- Use `nodeport` for internal-only/lab-network access patterns.
- Keep ingress class/base domain explicit when ingress is enabled.

## Recommended defaults

- VM templates: `bridge`
- Container templates: `bridge`
- Elevate to `unrestricted` only when app requirements demand it.

## Troubleshooting

If a lab has no network access:

1. Verify template `network_mode`.
2. Verify generated NetworkPolicy for the pod.
3. Verify DNS egress and node-level connectivity.

Commands:

```bash
kubectl -n labs get networkpolicy
kubectl -n labs describe networkpolicy
kubectl -n labs get pods -o wide | rg '^vm-|^virt-launcher-|^ct-'
```

## Related pages

- [Template Best Practices](Template-Best-Practices)
- [Security and Auth](Security-and-Auth)
- [Operations Runbook](Operations-Runbook)
