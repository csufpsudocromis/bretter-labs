import json
import re
import threading
from copy import deepcopy
from typing import Iterable

from .tables import User


class Role:
    USER = "user"
    LAB_ADMIN = "lab_admin"
    NAMESPACE_ADMIN = "namespace_admin"
    PLATFORM_ADMIN = "platform_admin"

    # Legacy role identifiers retained for compatibility with existing data/config.
    VIEWER = "viewer"
    IMAGE_MANAGER = "image_manager"
    TEMPLATE_MANAGER = "template_manager"
    LAB_OPERATOR = "lab_operator"
    TENANT_ADMIN = "tenant_admin"


class Permission:
    ADMIN_ACCESS = "admin.access"
    USERS_READ = "admin.users.read"
    USERS_WRITE = "admin.users.write"
    IMAGES_READ = "admin.images.read"
    IMAGES_WRITE = "admin.images.write"
    TEMPLATES_READ = "admin.templates.read"
    TEMPLATES_WRITE = "admin.templates.write"
    OPERATIONS_READ = "admin.operations.read"
    OPERATIONS_WRITE = "admin.operations.write"
    SETTINGS_READ = "admin.settings.read"
    SETTINGS_WRITE = "admin.settings.write"


LEGACY_ROLE_ALIASES: dict[str, str] = {
    # Viewer is retired; default to least privilege.
    Role.VIEWER: Role.USER,
    # Manager/operator roles are merged into Lab Admin.
    Role.IMAGE_MANAGER: Role.LAB_ADMIN,
    Role.TEMPLATE_MANAGER: Role.LAB_ADMIN,
    Role.LAB_OPERATOR: Role.LAB_ADMIN,
    # Tenant admin is renamed to Namespace Admin.
    Role.TENANT_ADMIN: Role.NAMESPACE_ADMIN,
}

_PERMISSION_CATALOG: tuple[str, ...] = (
    Permission.ADMIN_ACCESS,
    Permission.USERS_READ,
    Permission.USERS_WRITE,
    Permission.IMAGES_READ,
    Permission.IMAGES_WRITE,
    Permission.TEMPLATES_READ,
    Permission.TEMPLATES_WRITE,
    Permission.OPERATIONS_READ,
    Permission.OPERATIONS_WRITE,
    Permission.SETTINGS_READ,
    Permission.SETTINGS_WRITE,
)
_PERMISSION_CATALOG_SET = set(_PERMISSION_CATALOG)
_ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ROLE_SORT_WEIGHT = {
    Role.USER: 10,
    Role.LAB_ADMIN: 20,
    Role.NAMESPACE_ADMIN: 30,
    Role.PLATFORM_ADMIN: 999,
}


_DEFAULT_ROLE_DEFINITIONS: dict[str, dict[str, object]] = {
    Role.USER: {
        "label": "User",
        "description": "Can only access the user launch experience (VMs/containers).",
        "permissions": set(),
        "editable": True,
        "deletable": False,
    },
    Role.LAB_ADMIN: {
        "label": "Lab Admin",
        "description": "Can manage images/templates and operate running labs.",
        "permissions": {
            Permission.ADMIN_ACCESS,
            Permission.IMAGES_READ,
            Permission.IMAGES_WRITE,
            Permission.TEMPLATES_READ,
            Permission.TEMPLATES_WRITE,
            Permission.OPERATIONS_READ,
            Permission.OPERATIONS_WRITE,
        },
        "editable": True,
        "deletable": False,
    },
    Role.NAMESPACE_ADMIN: {
        "label": "Namespace Admin",
        "description": "Can fully manage namespace-scoped operations and settings.",
        "permissions": {
            Permission.ADMIN_ACCESS,
            Permission.USERS_READ,
            Permission.USERS_WRITE,
            Permission.IMAGES_READ,
            Permission.IMAGES_WRITE,
            Permission.TEMPLATES_READ,
            Permission.TEMPLATES_WRITE,
            Permission.OPERATIONS_READ,
            Permission.OPERATIONS_WRITE,
            Permission.SETTINGS_READ,
            Permission.SETTINGS_WRITE,
        },
        "editable": True,
        "deletable": False,
    },
    Role.PLATFORM_ADMIN: {
        "label": "Platform Admin",
        "description": "Full platform-wide administrative access.",
        "permissions": {"*"},
        "editable": False,
        "deletable": False,
    },
}

_ROLE_DEFINITIONS_LOCK = threading.RLock()
_ROLE_DEFINITIONS: dict[str, dict[str, object]] = deepcopy(_DEFAULT_ROLE_DEFINITIONS)


def _clone_role_definitions() -> dict[str, dict[str, object]]:
    return deepcopy(_DEFAULT_ROLE_DEFINITIONS)


def _validate_role_identifier(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if not _ROLE_ID_RE.fullmatch(normalized):
        raise ValueError("role id must match ^[a-z][a-z0-9_]{1,63}$")
    if normalized in LEGACY_ROLE_ALIASES:
        raise ValueError(f"role id '{normalized}' is reserved for a legacy alias")
    return normalized


def _normalize_permissions(permissions: Iterable[str], *, allow_wildcard: bool) -> set[str]:
    normalized: set[str] = set()
    for entry in permissions:
        perm = str(entry or "").strip().lower()
        if not perm:
            continue
        if perm == "*":
            if not allow_wildcard:
                raise ValueError("wildcard permission '*' is reserved for platform_admin")
            normalized.add("*")
            continue
        if perm not in _PERMISSION_CATALOG_SET:
            raise ValueError(f"unknown permission: {perm}")
        normalized.add(perm)
    return normalized


def _catalog_sort_key(role: str) -> tuple[int, str]:
    if role in _ROLE_SORT_WEIGHT:
        return (_ROLE_SORT_WEIGHT[role], role)
    return (100, role)


def _role_definitions_for_update() -> dict[str, dict[str, object]]:
    return deepcopy(_ROLE_DEFINITIONS)


def _apply_role_definitions(definitions: dict[str, dict[str, object]]) -> None:
    global _ROLE_DEFINITIONS
    _ROLE_DEFINITIONS = definitions


def _normalize_role_config_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("roles")
    if isinstance(nested, dict):
        return nested
    return payload


def reset_role_definitions() -> None:
    with _ROLE_DEFINITIONS_LOCK:
        _apply_role_definitions(_clone_role_definitions())


def configure_roles_from_json(raw_json: str | None) -> None:
    raw = str(raw_json or "").strip()
    if not raw:
        reset_role_definitions()
        return
    try:
        parsed = json.loads(raw)
    except ValueError:
        reset_role_definitions()
        return
    payload = _normalize_role_config_payload(parsed)
    if not isinstance(payload, dict):
        reset_role_definitions()
        return

    next_defs = _clone_role_definitions()
    for raw_role, raw_meta in payload.items():
        try:
            role = _validate_role_identifier(str(raw_role or ""))
        except ValueError:
            continue
        if role == Role.PLATFORM_ADMIN:
            continue
        if not isinstance(raw_meta, dict):
            continue

        if role in next_defs:
            base_label = str(next_defs[role].get("label") or role).strip()
            base_description = str(next_defs[role].get("description") or "").strip()
        else:
            base_label = role.replace("_", " ").title()
            base_description = ""

        label = str(raw_meta.get("label", base_label) or "").strip() or base_label
        description = str(raw_meta.get("description", base_description) or "").strip()
        try:
            permissions = _normalize_permissions(raw_meta.get("permissions", []), allow_wildcard=False)
        except (TypeError, ValueError):
            continue

        existing = next_defs.get(role)
        if existing:
            existing["label"] = label
            existing["description"] = description
            existing["permissions"] = permissions
        else:
            next_defs[role] = {
                "label": label,
                "description": description,
                "permissions": permissions,
                "editable": True,
                "deletable": True,
            }

    with _ROLE_DEFINITIONS_LOCK:
        _apply_role_definitions(next_defs)


def role_config_payload() -> dict[str, dict[str, object]]:
    with _ROLE_DEFINITIONS_LOCK:
        payload: dict[str, dict[str, object]] = {}
        for role, meta in _ROLE_DEFINITIONS.items():
            if role == Role.PLATFORM_ADMIN:
                continue
            payload[role] = {
                "label": str(meta.get("label") or role),
                "description": str(meta.get("description") or ""),
                "permissions": list_permissions_for_role(role),
            }
        return payload


def permission_catalog() -> list[str]:
    return list(_PERMISSION_CATALOG)


def normalize_role(value: str | None) -> str:
    role = str(value or "").strip().lower()
    with _ROLE_DEFINITIONS_LOCK:
        if role in _ROLE_DEFINITIONS:
            return role
        aliased = LEGACY_ROLE_ALIASES.get(role)
        if aliased in _ROLE_DEFINITIONS:
            return aliased
    return Role.USER


def legacy_admin_to_role(is_admin: bool) -> str:
    return Role.PLATFORM_ADMIN if bool(is_admin) else Role.USER


def role_for_user(user: User) -> str:
    normalized = normalize_role(getattr(user, "role", None))
    if normalized != Role.USER:
        return normalized
    if bool(getattr(user, "is_admin", False)):
        return Role.PLATFORM_ADMIN
    return Role.USER


def role_implies_admin_access(role: str) -> bool:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        perms = set(_ROLE_DEFINITIONS.get(normalized, {}).get("permissions", set()))
    return "*" in perms or Permission.ADMIN_ACCESS in perms


def role_permissions(role: str) -> set[str]:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        return set(_ROLE_DEFINITIONS.get(normalized, {}).get("permissions", set()))


def user_permissions(user: User) -> set[str]:
    return role_permissions(role_for_user(user))


def has_permission(user: User, permission: str) -> bool:
    perms = user_permissions(user)
    if "*" in perms:
        return True
    if permission in perms:
        return True
    for granted in perms:
        if granted.endswith(".*") and permission.startswith(granted[:-1]):
            return True
    return False


def ensure_user_role_fields(user: User) -> bool:
    role = role_for_user(user)
    is_admin = role_implies_admin_access(role)
    changed = False
    if getattr(user, "role", None) != role:
        user.role = role
        changed = True
    if bool(getattr(user, "is_admin", False)) != is_admin:
        user.is_admin = is_admin
        changed = True
    return changed


def normalize_requested_role(role: str | None, is_admin: bool | None = None) -> str:
    if role is not None:
        raw = str(role).strip().lower()
        if not raw:
            raise ValueError("invalid role: empty role")
        normalized = normalize_role(raw)
        with _ROLE_DEFINITIONS_LOCK:
            if raw not in _ROLE_DEFINITIONS and raw not in LEGACY_ROLE_ALIASES:
                raise ValueError(f"invalid role: {role}")
            if normalized not in _ROLE_DEFINITIONS:
                raise ValueError(f"invalid role: {role}")
        return normalized
    return legacy_admin_to_role(bool(is_admin))


def list_permissions_for_role(role: str) -> list[str]:
    perms = role_permissions(role)
    if "*" in perms:
        return ["*"]
    return sorted(perms)


def can_access_admin(role: str) -> bool:
    return role_implies_admin_access(role)


def roles_catalog() -> Iterable[str]:
    with _ROLE_DEFINITIONS_LOCK:
        return [role for role in sorted(_ROLE_DEFINITIONS.keys(), key=_catalog_sort_key)]


def role_label(role: str) -> str:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        value = _ROLE_DEFINITIONS.get(normalized, {}).get("label", normalized)
    return str(value or normalized)


def role_description(role: str) -> str:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        value = _ROLE_DEFINITIONS.get(normalized, {}).get("description", "")
    return str(value or "")


def role_is_editable(role: str) -> bool:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        return bool(_ROLE_DEFINITIONS.get(normalized, {}).get("editable", False))


def role_is_deletable(role: str) -> bool:
    normalized = normalize_role(role)
    with _ROLE_DEFINITIONS_LOCK:
        return bool(_ROLE_DEFINITIONS.get(normalized, {}).get("deletable", False))


def set_role_definition(*, role: str, label: str, description: str, permissions: list[str]) -> None:
    normalized_role = _validate_role_identifier(role)
    if normalized_role == Role.PLATFORM_ADMIN:
        raise ValueError("platform_admin cannot be modified")
    normalized_permissions = _normalize_permissions(permissions, allow_wildcard=False)
    normalized_label = str(label or "").strip() or normalized_role.replace("_", " ").title()
    normalized_description = str(description or "").strip()

    with _ROLE_DEFINITIONS_LOCK:
        next_defs = _role_definitions_for_update()
        existing = next_defs.get(normalized_role)
        if existing is not None and not bool(existing.get("editable", False)):
            raise ValueError(f"role '{normalized_role}' is not editable")
        if existing is None:
            next_defs[normalized_role] = {
                "label": normalized_label,
                "description": normalized_description,
                "permissions": normalized_permissions,
                "editable": True,
                "deletable": True,
            }
        else:
            existing["label"] = normalized_label
            existing["description"] = normalized_description
            existing["permissions"] = normalized_permissions
        _apply_role_definitions(next_defs)


def delete_role_definition(role: str) -> None:
    normalized = normalize_role(role)
    if normalized == Role.PLATFORM_ADMIN:
        raise ValueError("platform_admin cannot be deleted")
    with _ROLE_DEFINITIONS_LOCK:
        next_defs = _role_definitions_for_update()
        current = next_defs.get(normalized)
        if current is None:
            raise ValueError(f"role '{role}' not found")
        if not bool(current.get("deletable", False)):
            raise ValueError(f"role '{normalized}' cannot be deleted")
        next_defs.pop(normalized, None)
        _apply_role_definitions(next_defs)


def can_non_platform_assign_role(role: str) -> bool:
    target_perms = role_permissions(role)
    if "*" in target_perms:
        return False
    lab_admin_perms = role_permissions(Role.LAB_ADMIN)
    if "*" in lab_admin_perms:
        lab_admin_perms.discard("*")
    return target_perms.issubset(lab_admin_perms)
