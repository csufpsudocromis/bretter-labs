from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from .team_quotas import normalize_namespace
from ..tables import ManagedNamespace

_DEFAULT_UPLOAD_MAX_BYTES = 60 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class NamespaceRuntimePolicy:
    idle_timeout_minutes_default: int = 30
    vm_auto_delete_minutes_default: int = 60
    container_auto_delete_minutes_default: int = 60
    queue_max_pending: int = 25
    upload_max_bytes: int = _DEFAULT_UPLOAD_MAX_BYTES


def _safe_int(value: object, *, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        parsed = int(default)
    return max(minimum, parsed)


def get_namespace_runtime_policy(session: Session, namespace: str | None) -> NamespaceRuntimePolicy:
    target = normalize_namespace(namespace)
    if not target:
        return NamespaceRuntimePolicy()
    row = session.exec(
        select(ManagedNamespace)
        .where(ManagedNamespace.namespace == target)
        .where(ManagedNamespace.enabled == True)  # noqa: E712
    ).first()
    if not row:
        return NamespaceRuntimePolicy()
    return NamespaceRuntimePolicy(
        idle_timeout_minutes_default=_safe_int(
            getattr(row, "idle_timeout_minutes_default", None),
            default=30,
            minimum=1,
        ),
        vm_auto_delete_minutes_default=_safe_int(
            getattr(row, "vm_auto_delete_minutes_default", None),
            default=60,
            minimum=1,
        ),
        container_auto_delete_minutes_default=_safe_int(
            getattr(row, "container_auto_delete_minutes_default", None),
            default=60,
            minimum=1,
        ),
        queue_max_pending=_safe_int(getattr(row, "queue_max_pending", None), default=25, minimum=1),
        upload_max_bytes=_safe_int(
            getattr(row, "upload_max_bytes", None),
            default=_DEFAULT_UPLOAD_MAX_BYTES,
            minimum=1,
        ),
    )
