import re

from fastapi import HTTPException, status

from ..config import settings
from ..rbac import Role, role_for_user
from ..tables import User
from .team_quotas import normalize_namespace, normalize_team

GLOBAL_TENANT = "global"

_TEAM_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def normalize_tenant(value: str | None, *, default: str = GLOBAL_TENANT) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"global", "*", "all"}:
        return GLOBAL_TENANT
    return normalize_team(raw)


def actor_tenant(user: User) -> str:
    return normalize_team(getattr(user, "team", None))


def is_platform_admin(user: User) -> bool:
    return role_for_user(user) == Role.PLATFORM_ADMIN


def is_tenant_admin(user: User) -> bool:
    return role_for_user(user) == Role.TENANT_ADMIN


def resolve_resource_tenant(actor: User, requested_tenant: str | None = None) -> str:
    if is_platform_admin(actor):
        return normalize_tenant(requested_tenant, default=GLOBAL_TENANT)
    return actor_tenant(actor)


def actor_can_access_tenant(actor: User, tenant: str | None) -> bool:
    normalized = normalize_tenant(tenant, default=GLOBAL_TENANT)
    if is_platform_admin(actor):
        return True
    team = actor_tenant(actor)
    return normalized in {team, GLOBAL_TENANT}


def assert_actor_can_manage_tenant(actor: User, tenant: str | None) -> str:
    normalized = normalize_tenant(tenant, default=GLOBAL_TENANT)
    if is_platform_admin(actor):
        return normalized
    team = actor_tenant(actor)
    if normalized != team:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant scope violation")
    return normalized


def tenant_namespace_for_team(team: str | None) -> str:
    mode = str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()
    if mode != "per_team":
        return normalize_namespace(getattr(settings, "kube_namespace", None))
    prefix = str(getattr(settings, "team_namespace_prefix", "labs-team-") or "labs-team-").strip().lower()
    if not prefix:
        prefix = "labs-team-"
    slug = _TEAM_SLUG_RE.sub("-", normalize_team(team)).strip("-")
    if not slug:
        slug = "default"
    namespace = f"{prefix}{slug}"
    namespace = re.sub(r"[^a-z0-9-]+", "-", namespace.lower()).strip("-")
    if not namespace:
        namespace = "labs"
    return namespace[:63].rstrip("-")


def tenant_namespace_for_user(user: User) -> str:
    return tenant_namespace_for_team(getattr(user, "team", None))


def vm_launch_requires_privileged_runtime() -> bool:
    return bool(
        getattr(settings, "vm_runner_privileged", False)
        or getattr(settings, "kube_use_kvm", True)
        or str(getattr(settings, "vm_net_backend", "user") or "user").strip().lower() == "tap-nat"
    )


def tenant_privileged_namespace_for_team(team: str | None) -> str:
    mode = str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()
    if mode != "per_team":
        return normalize_namespace(getattr(settings, "kube_namespace", None))
    prefix = (
        str(getattr(settings, "vm_privileged_namespace_prefix", "labs-vm-priv-") or "labs-vm-priv-").strip().lower()
    )
    if not prefix:
        prefix = "labs-vm-priv-"
    slug = _TEAM_SLUG_RE.sub("-", normalize_team(team)).strip("-")
    if not slug:
        slug = "default"
    namespace = f"{prefix}{slug}"
    namespace = re.sub(r"[^a-z0-9-]+", "-", namespace.lower()).strip("-")
    if not namespace:
        namespace = "labs-vm-priv"
    return namespace[:63].rstrip("-")


def vm_runtime_namespace_for_user(user: User) -> str:
    base_namespace = tenant_namespace_for_user(user)
    if not bool(getattr(settings, "vm_privileged_runtime_isolation_enabled", True)):
        return base_namespace
    if not vm_launch_requires_privileged_runtime():
        return base_namespace
    return tenant_privileged_namespace_for_team(getattr(user, "team", None))
