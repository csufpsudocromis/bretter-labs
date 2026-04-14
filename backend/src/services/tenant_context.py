import json
import logging
import re

from fastapi import HTTPException, Request, status

from ..config import settings
from ..rbac import Role, role_for_user
from ..tables import User
from .team_quotas import normalize_namespace

GLOBAL_TENANT = "global"
_NAMESPACE_SCOPE_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_NAMESPACE_HEADER_KEYS = ("x-bretter-namespace", "x-blabs-namespace")
logger = logging.getLogger(__name__)


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
    if role_for_user(user) == Role.NAMESPACE_ADMIN:
        scopes = user_namespace_scopes(user)
        if scopes:
            return scopes[0]
        return ""
    scopes = user_namespace_scopes(user)
    if scopes:
        return scopes[0]
    return tenant_namespace_for_team(getattr(user, "team", None))


def default_namespace() -> str:
    configured = normalize_namespace(getattr(settings, "kube_namespace", None))
    return configured or "labs"


def namespace_from_request(request: Request | None = None, *, fallback: str | None = None) -> str:
    selected, _source = _namespace_from_request_with_source(request=request, fallback=fallback)
    return selected


def _namespace_from_request_with_source(
    *, request: Request | None = None, fallback: str | None = None
) -> tuple[str, str]:
    raw = ""
    source = "none"
    if request is not None:
        for header in _NAMESPACE_HEADER_KEYS:
            header_value = str(request.headers.get(header) or "").strip()
            if header_value:
                raw = header_value
                source = f"header:{header}"
                break
        if not raw:
            raw = str(request.query_params.get("namespace") or "").strip()
            if raw:
                source = "query:namespace"
    normalized = normalize_namespace(raw)
    if normalized:
        return normalized, source or "request"
    fallback_normalized = normalize_namespace(fallback)
    if fallback_normalized:
        return fallback_normalized, "fallback"
    return default_namespace(), "default"


def normalize_namespace_scopes(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        namespace = normalize_namespace(raw)
        if not namespace:
            continue
        if len(namespace) > 63 or not _NAMESPACE_SCOPE_RE.fullmatch(namespace):
            raise ValueError(f"invalid namespace scope: {raw}")
        if namespace in seen:
            continue
        normalized.append(namespace)
        seen.add(namespace)
    return normalized


def user_namespace_scopes(user: User) -> list[str]:
    raw = getattr(user, "namespace_scopes_json", "[]")
    payload: list[str] = []
    if isinstance(raw, list):
        payload = [str(item) for item in raw]
    else:
        try:
            decoded = json.loads(str(raw or "[]"))
            if isinstance(decoded, list):
                payload = [str(item) for item in decoded]
        except Exception:
            payload = []
    try:
        return normalize_namespace_scopes(payload)
    except ValueError:
        return []


def set_user_namespace_scopes(user: User, values: list[str] | None) -> list[str]:
    normalized = normalize_namespace_scopes(values)
    user.namespace_scopes_json = json.dumps(normalized, separators=(",", ":"))
    return normalized


def actor_namespace_scopes(actor: User) -> set[str] | None:
    if is_platform_admin(actor):
        return None
    role = role_for_user(actor)
    scopes = set(user_namespace_scopes(actor))
    if role == Role.NAMESPACE_ADMIN:
        return {normalize_namespace(item) for item in scopes if normalize_namespace(item)}
    if scopes:
        return {normalize_namespace(item) for item in scopes if normalize_namespace(item)}
    scopes.add(tenant_namespace_for_team(getattr(actor, "team", None)))
    scopes.add(default_namespace())
    return {normalize_namespace(item) for item in scopes if normalize_namespace(item)}


def actor_can_access_namespace(actor: User, namespace: str | None) -> bool:
    normalized = normalize_namespace(namespace)
    if not normalized:
        return False
    scope = actor_namespace_scopes(actor)
    if scope is None:
        return True
    return normalized in scope


def assert_actor_can_access_namespace(actor: User, namespace: str | None) -> str:
    normalized = normalize_namespace(namespace)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="namespace is required")
    if not actor_can_access_namespace(actor, normalized):
        actor_scope = actor_namespace_scopes(actor)
        logger.warning(
            "namespace_access_denied actor=%s role=%s requested_namespace=%s scope=%s",
            str(getattr(actor, "username", "") or "<unknown>"),
            role_for_user(actor),
            normalized,
            sorted(actor_scope) if actor_scope is not None else "all",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"namespace access denied: {normalized}")
    return normalized


def _default_namespace_for_actor(actor: User, *, fallback_namespace: str | None = None) -> str:
    fallback_normalized = normalize_namespace(fallback_namespace)
    actor_scope = actor_namespace_scopes(actor)
    if actor_scope is None:
        return (
            fallback_normalized
            or normalize_namespace(tenant_namespace_for_user(actor))
            or normalize_namespace(default_namespace())
            or "labs"
        )

    preferred_candidates = (
        normalize_namespace(tenant_namespace_for_user(actor)),
        fallback_normalized,
        normalize_namespace(default_namespace()),
    )
    for candidate in preferred_candidates:
        if candidate and candidate in actor_scope:
            return candidate
    if actor_scope:
        return sorted(actor_scope)[0]
    return fallback_normalized or normalize_namespace(default_namespace()) or "labs"


def resolve_resource_namespace(
    actor: User,
    *,
    request: Request | None = None,
    requested_namespace: str | None = None,
    fallback_namespace: str | None = None,
) -> str:
    role = role_for_user(actor)
    if role == Role.NAMESPACE_ADMIN and not user_namespace_scopes(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="namespace admin account has no namespace scopes configured",
        )
    source = "requested_namespace"
    if requested_namespace is not None and str(requested_namespace).strip():
        chosen = normalize_namespace(requested_namespace)
        source = "requested_namespace"
    else:
        explicit, explicit_source = _namespace_from_request_with_source(request=request, fallback=None)
        if explicit and explicit_source.startswith(("header:", "query:")):
            chosen = explicit
            source = explicit_source
        else:
            chosen = _default_namespace_for_actor(actor, fallback_namespace=fallback_namespace)
            source = "actor_default"
    resolved = assert_actor_can_access_namespace(actor, chosen)
    logger.debug(
        "namespace_resolved actor=%s role=%s source=%s requested_namespace=%s fallback=%s resolved=%s",
        str(getattr(actor, "username", "") or "<unknown>"),
        role,
        source,
        str(requested_namespace or "").strip() or "-",
        str(fallback_namespace or "").strip() or "-",
        resolved,
    )
    return resolved


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
