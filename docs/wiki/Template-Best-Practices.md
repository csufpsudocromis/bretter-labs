# Template Best Practices

Last reviewed: March 9, 2026.

Use these defaults to reduce failed launches and improve startup consistency.

## VM templates

### Sizing

- Start with smallest viable profile per OS.
- Avoid oversizing defaults; bigger defaults increase pending incidents.
- Keep enough node headroom for one additional emergency lab.

### Image/boot compatibility

- Match OS image with template firmware/machine settings.
- Validate one known-good boot after every image refresh.
- Keep image conversion path consistent (avoid mixed/manual conversions).

### Network mode

- Default to `bridge`.
- Use `isolated`/`none` for offline training.
- Use `unrestricted` only when required.

### Console provider

- Default to `spice` for Windows-focused labs and SPICE agent features.
- Use `guacamole` (VNC transport) when you want a simpler VNC console path.
- Use `guacamole_rdp` when guest-native RDP is enabled and you want browser RDP via Guacamole.
- Keep one provider per template to simplify troubleshooting and operator runbooks.

### Warm pool and launch limits

- Use pre-clone pool for frequently used templates.
- Set sensible `max_active_instances` per template to avoid noisy-neighbor impact.

## Container templates

### Sizing

- Set CPU/memory requests based on observed baseline.
- Avoid burst-only sizing without requests.
- Revisit limits after real user load data.

### Readiness rules

- Prefer HTTP readiness for web apps.
- Set expected status code and success path.
- Tune startup timeout per image/app behavior.

### Dependency checks

- Add DB/cache dependency checks (DNS+TCP).
- Fail early instead of launching into degraded state.

### Runtime hardening

- Enable `run_as_non_root` where image supports it.
- Enable read-only root filesystem for compatible workloads.
- Keep env/command overrides minimal and documented.

### Exposure/network

- Prefer TLS-enabled ingress for user-facing apps.
- Keep `bridge` network mode as default.

## Universal defaults (VM + container)

- Keep templates disabled until validation is complete.
- Set idle timeout according to policy and cost profile.
- Keep one-active-lab-per-user policy enabled.
- Record a template owner and last validation date.

## Validation checklist before enabling template

1. Launch succeeds on a fresh user session.
2. Connect succeeds and app/OS is interactive.
3. Status transitions are accurate (`Building` -> `Starting` -> `Running`).
4. Idle timeout prompt appears on user + connect views.
5. Delete path cleans pod/service/policy artifacts.

## Related pages

- [Network Modes Reference](Network-Modes-Reference)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Error Catalog](Error-Catalog)
