import re

from ..config import settings
from ..rbac import Role, role_for_user
from ..tables import User
from .team_quotas import normalize_namespace

GLOBAL_TENANT = "global"


def normalize_tenant(value: str | None, *, default: str = GLOBAL_TENANT) -> str:
    _ = value
    _ = default
    # Team/tenant scoping is retired; all resources resolve to global scope.
    return GLOBAL_TENANT


def actor_tenant(user: User) -> str:
    _ = user
    return GLOBAL_TENANT


def is_platform_admin(user: User) -> bool:
    return role_for_user(user) == Role.PLATFORM_ADMIN


def is_tenant_admin(user: User) -> bool:
    return role_for_user(user) == Role.NAMESPACE_ADMIN


def resolve_resource_tenant(actor: User, requested_tenant: str | None = None) -> str:
    _ = actor
    _ = requested_tenant
    return GLOBAL_TENANT


def actor_can_access_tenant(actor: User, tenant: str | None) -> bool:
    _ = actor
    _ = tenant
    return True


def assert_actor_can_manage_tenant(actor: User, tenant: str | None) -> str:
    _ = actor
    _ = tenant
    return GLOBAL_TENANT


def tenant_namespace_for_team(team: str | None) -> str:
    _ = team
    mode = str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()
    if mode != "per_team":
        return normalize_namespace(getattr(settings, "kube_namespace", None))
    prefix = str(getattr(settings, "team_namespace_prefix", "labs-team-") or "labs-team-").strip().lower()
    if not prefix:
        prefix = "labs-team-"
    namespace = f"{prefix}default"
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
    _ = team
    mode = str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()
    if mode != "per_team":
        return normalize_namespace(getattr(settings, "kube_namespace", None))
    prefix = (
        str(getattr(settings, "vm_privileged_namespace_prefix", "labs-vm-priv-") or "labs-vm-priv-").strip().lower()
    )
    if not prefix:
        prefix = "labs-vm-priv-"
    namespace = f"{prefix}default"
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
