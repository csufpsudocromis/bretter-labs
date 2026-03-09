from typing import Iterable

from .tables import User


class Role:
    USER = "user"
    VIEWER = "viewer"
    IMAGE_MANAGER = "image_manager"
    TEMPLATE_MANAGER = "template_manager"
    LAB_OPERATOR = "lab_operator"
    PLATFORM_ADMIN = "platform_admin"


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


VALID_ROLES: tuple[str, ...] = (
    Role.USER,
    Role.VIEWER,
    Role.IMAGE_MANAGER,
    Role.TEMPLATE_MANAGER,
    Role.LAB_OPERATOR,
    Role.PLATFORM_ADMIN,
)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.USER: set(),
    Role.VIEWER: {
        Permission.ADMIN_ACCESS,
        Permission.IMAGES_READ,
        Permission.TEMPLATES_READ,
        Permission.OPERATIONS_READ,
        Permission.SETTINGS_READ,
    },
    Role.IMAGE_MANAGER: {
        Permission.ADMIN_ACCESS,
        Permission.IMAGES_READ,
        Permission.IMAGES_WRITE,
        Permission.TEMPLATES_READ,
        Permission.SETTINGS_READ,
    },
    Role.TEMPLATE_MANAGER: {
        Permission.ADMIN_ACCESS,
        Permission.IMAGES_READ,
        Permission.TEMPLATES_READ,
        Permission.TEMPLATES_WRITE,
        Permission.SETTINGS_READ,
    },
    Role.LAB_OPERATOR: {
        Permission.ADMIN_ACCESS,
        Permission.IMAGES_READ,
        Permission.TEMPLATES_READ,
        Permission.OPERATIONS_READ,
        Permission.OPERATIONS_WRITE,
        Permission.SETTINGS_READ,
    },
    Role.PLATFORM_ADMIN: {"*"},
}


def normalize_role(value: str | None) -> str:
    role = str(value or "").strip().lower()
    if role in VALID_ROLES:
        return role
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
    perms = ROLE_PERMISSIONS.get(normalized, set())
    return "*" in perms or Permission.ADMIN_ACCESS in perms


def role_permissions(role: str) -> set[str]:
    return set(ROLE_PERMISSIONS.get(normalize_role(role), set()))


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
        normalized = str(role).strip().lower()
        if normalized not in VALID_ROLES:
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
    return VALID_ROLES
