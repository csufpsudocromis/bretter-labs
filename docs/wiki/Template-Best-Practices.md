# Template Best Practices

Last reviewed: March 19, 2026.

Use these defaults to reduce launch failures and keep user experience predictable.

## VM templates

### Sizing and capacity

- Start with smallest viable CPU/RAM profile per OS.
- Avoid oversized defaults that create scheduler pressure.
- Keep enough node headroom for burst starts and one emergency lab.

### Image and boot compatibility

- Match image boot model to template firmware/machine type:
  - Windows: typically UEFI + `q35`
  - Linux legacy images: often BIOS + `pc`
- Validate one known-good boot after each image refresh.
- Keep conversion/import path consistent across images.

### Console provider selection

`spice`:

- Best default for traditional VM console workflow.
- Requires SPICE password path to resolve at connect time.

`guacamole` (VNC):

- Browser VNC path with simpler client experience.
- Good for Linux/utility workloads without guest RDP expectations.

`guacamole_rdp`:

- Use only when guest RDP service is expected/enabled.
- `Connect` remains disabled until runtime RDP readiness is detected.
- Configure per-template default RDP username/password in `/admin/templates` when needed.
- Do not use global plaintext shared RDP credentials; defaults are template-scoped and backend-stored as encrypted secret values.

### Warm pool and concurrency limits

- Use pre-clone pool for high-frequency templates.
- Set `preclone_pool_size` and `preclone_pool_max` based on observed demand.
- Set `max_active_instances` to prevent noisy-neighbor resource collapse.

### Network mode

- Default to `bridge`.
- Use `isolated`/`none` for offline or constrained labs.
- Use `unrestricted` only when explicitly required and documented.

## Container templates

### Image and runtime policy

- Use immutable tags or digests for stable behavior.
- Keep image source registries aligned with allowed registry policy.
- Prefer signed images in production, even though unsigned-image registration may be warning-only by policy.

### Sizing

- Set realistic CPU/memory requests and limits.
- Re-tune after observing actual workload usage.

### Readiness and startup

- Prefer HTTP readiness for web apps with explicit path/status.
- Add dependency checks (DB/cache endpoints) to fail early.
- Tune startup timeout per application characteristics.

### Security hardening

- Enable `run_as_non_root` when image supports it.
- Enable read-only root filesystem where compatible.
- Keep env/command overrides minimal and documented.

### Exposure and networking

- Prefer ingress + TLS for user-facing applications.
- Keep `bridge` as default network mode unless there is a clear requirement.

## Universal template controls (VM + container)

- Keep template disabled until validation is complete.
- Set idle timeout based on policy/cost expectations.
- Keep one-active-lab-per-user policy enabled.
- Track owner, purpose, and last validation date in template notes/runbook.

## Pre-enable validation checklist

1. Launch succeeds from a fresh user session.
2. Connect succeeds and is interactive.
3. Status transitions are accurate (`Building` -> `Starting` -> `Running` or VM equivalents).
4. Idle timeout prompt behavior is correct on user and connect views.
5. Delete path cleans runtime resources (pods/services/policies/records).

## Related pages

- [Network Modes Reference](Network-Modes-Reference)
- [Console Providers and RDP Operations](Console-Providers-and-RDP-Operations)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Error Catalog](Error-Catalog)
