# Tenant Isolation and Namespaces

Last reviewed: April 2, 2026.

## Goal

Use per-tenant namespaces with explicit quota and network-policy boundaries instead of shared runtime sprawl.
Do not treat label-only isolation in a shared namespace as a strong boundary.

## Mode settings

`setup.sh` now supports explicit tenant namespace mode wiring:

- `TEAM_NAMESPACE_MODE=shared|per_team`
- `TEAM_NAMESPACE_PREFIX=<dns-prefix-ending-with-dash>`

Production baseline defaults:

- `TEAM_NAMESPACE_MODE=per_team`
- `TEAM_NAMESPACE_PREFIX=labs-team-`

Production guardrails and backend startup validation require `TEAM_NAMESPACE_MODE=per_team`.

## Enforcement model

Isolation is enforced at multiple layers:

- Namespace boundary: each tenant runs labs in its own namespace (`TEAM_NAMESPACE_PREFIX + <tenant-slug>` when using bootstrap naming).
- API tenant scope: tenant admins can only manage resources in their own tenant; platform admins can manage all tenants.
- Resource visibility: non-platform users/admins can only see tenant-scoped resources plus global shared resources.
- Quota accounting: active VM/container usage is counted per-tenant namespace for quota enforcement.
- Network policy: default-deny and same-namespace-only rules prevent cross-tenant east-west traffic by default.

Tenant roles:

- `platform_admin`: global scope across all tenants.
- `namespace_admin` / `tenant_admin`: admin scope limited to assigned namespace resources.

## Bootstrap a tenant namespace

Use the namespace bootstrap helper:

```bash
TEAM=physics TEAM_NAMESPACE_PREFIX=labs-team- ./scripts/bootstrap_team_namespace.sh
```

Optional overrides:

```bash
TEAM=physics \
TENANT_NAMESPACE=labs-team-physics \
CPU_REQUESTS=12 CPU_LIMITS=24 \
MEMORY_REQUESTS=24Gi MEMORY_LIMITS=48Gi \
STORAGE_REQUESTS=4Ti MAX_PODS=300 \
./scripts/bootstrap_team_namespace.sh
```

What this applies:

- Namespace labels for tenant/team identity
- ResourceQuota (`bretter-tenant-quota`)
- LimitRange defaults (`bretter-tenant-default-limits`)
- Default-deny ingress + egress
- DNS egress allow policy
- Same-namespace east-west allow policy
- Control-plane backend ingress allow policy (`allow-control-plane-ingress`)

## Verification

```bash
kubectl get ns labs-team-physics --show-labels
kubectl -n labs-team-physics get resourcequota bretter-tenant-quota -o yaml
kubectl -n labs-team-physics get limitrange bretter-tenant-default-limits -o yaml
kubectl -n labs-team-physics get networkpolicy
```

## Operational notes

- Keep tenant namespaces non-overlapping and deterministic.
- Prefer namespace-scoped quota updates through `/admin/settings/namespaces`, GitOps/PR-reviewed YAML, or bootstrap script reruns.
- Namespace picker/catalog endpoints intentionally expose only lab-managed namespaces (and actor-assigned scopes), not arbitrary cluster/system namespaces.
- Keep cross-namespace connectivity blocked by default; add explicit allow rules only for required dependencies.
- Use `scripts/namespace_config_backup.py` to export namespace policy/binding state before major changes.
- Use `POST /admin/settings/namespaces/<namespace>/decommission` for ordered cleanup with status reporting.
