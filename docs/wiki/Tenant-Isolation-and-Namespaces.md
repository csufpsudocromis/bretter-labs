# Tenant Isolation and Namespaces

Last reviewed: March 20, 2026.

## Goal

Use per-team namespaces with explicit quota and network-policy boundaries instead of shared runtime sprawl.

## Mode settings

`setup.sh` now supports explicit tenant namespace mode wiring:

- `TEAM_NAMESPACE_MODE=shared|per_team`
- `TEAM_NAMESPACE_PREFIX=<dns-prefix-ending-with-dash>`

Production baseline defaults:

- `TEAM_NAMESPACE_MODE=per_team`
- `TEAM_NAMESPACE_PREFIX=labs-team-`

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

## Verification

```bash
kubectl get ns labs-team-physics --show-labels
kubectl -n labs-team-physics get resourcequota bretter-tenant-quota -o yaml
kubectl -n labs-team-physics get limitrange bretter-tenant-default-limits -o yaml
kubectl -n labs-team-physics get networkpolicy
```

## Operational notes

- Keep per-team namespaces non-overlapping and deterministic.
- Prefer team-scoped quota updates through GitOps/PR-reviewed YAML or the bootstrap script rerun.
- Keep cross-namespace connectivity blocked by default; add explicit allow rules only for required dependencies.
