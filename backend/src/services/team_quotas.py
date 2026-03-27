import math
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlmodel import Session, select

from ..config import settings
from ..tables import (
    ContainerInstance,
    ContainerTemplate,
    Image,
    Instance,
    TeamQuota,
    Template,
)

DEFAULT_TEAM = "default"
DEFAULT_NAMESPACE = "labs"
ACTIVE_STATUSES = {"pending", "running"}
DEFAULT_CONTAINER_STORAGE_GIB = 1
FALLBACK_VM_STORAGE_GIB = 20


def normalize_team(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw else DEFAULT_TEAM


def normalize_namespace(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw:
        return raw
    configured = str(getattr(settings, "kube_namespace", "") or "").strip().lower()
    return configured or DEFAULT_NAMESPACE


def normalize_optional_limit(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


@dataclass
class TeamQuotaCheckResult:
    effective_idle_timeout_minutes: int
    error_detail: str | None


def _estimate_vm_storage_gib(image: Image | None) -> int:
    if not image:
        return FALLBACK_VM_STORAGE_GIB
    size_bytes = max(0, int(getattr(image, "size_bytes", 0) or 0))
    if size_bytes <= 0:
        return FALLBACK_VM_STORAGE_GIB
    return max(1, int(math.ceil(size_bytes / float(1024**3))))


def _select_namespace_quota(quota_rows: list[TeamQuota]) -> TeamQuota | None:
    if not quota_rows:
        return None
    preferred = [row for row in quota_rows if normalize_team(getattr(row, "team", None)) == DEFAULT_TEAM]
    ranked = preferred or quota_rows
    ranked.sort(
        key=lambda row: (
            getattr(row, "updated_at", None) is not None,
            getattr(row, "updated_at", None) or getattr(row, "created_at", None),
            str(getattr(row, "id", "") or ""),
        ),
        reverse=True,
    )
    return ranked[0]


def _namespace_quota(session: Session, *, namespace: str) -> TeamQuota | None:
    rows = session.exec(
        select(TeamQuota).where(TeamQuota.namespace == namespace).where(TeamQuota.enabled == True)  # noqa: E712
    ).all()
    return _select_namespace_quota(list(rows))


def _active_namespace_usage(
    session: Session,
    *,
    namespace: str | None = None,
    exclude_vm_instance_id: str | None = None,
    exclude_container_instance_id: str | None = None,
) -> tuple[int, int, int, int]:
    namespace_name = normalize_namespace(namespace)
    vm_rows = session.exec(
        select(Instance).where(Instance.status.in_(ACTIVE_STATUSES)).where(Instance.namespace == namespace_name)
    ).all()
    if exclude_vm_instance_id:
        vm_rows = [row for row in vm_rows if row.id != exclude_vm_instance_id]

    vm_template_ids = {row.template_id for row in vm_rows}
    vm_templates: dict[str, Template] = {}
    if vm_template_ids:
        vm_templates = {
            tmpl.id: tmpl for tmpl in session.exec(select(Template).where(Template.id.in_(list(vm_template_ids)))).all()
        }
    image_ids = {tmpl.image_id for tmpl in vm_templates.values()}
    images: dict[str, Image] = {}
    if image_ids:
        images = {row.id: row for row in session.exec(select(Image).where(Image.id.in_(list(image_ids)))).all()}

    container_rows = session.exec(
        select(ContainerInstance)
        .where(ContainerInstance.status.in_(ACTIVE_STATUSES))
        .where(ContainerInstance.namespace == namespace_name)
    ).all()
    if exclude_container_instance_id:
        container_rows = [row for row in container_rows if row.id != exclude_container_instance_id]
    container_template_ids = {row.template_id for row in container_rows}
    container_templates: dict[str, ContainerTemplate] = {}
    if container_template_ids:
        container_templates = {
            tmpl.id: tmpl
            for tmpl in session.exec(
                select(ContainerTemplate).where(ContainerTemplate.id.in_(list(container_template_ids)))
            ).all()
        }

    active_labs = len(vm_rows) + len(container_rows)
    cpu_millicores = 0
    memory_mb = 0
    storage_gib = 0

    vm_memory_overhead = max(0, int(getattr(settings, "vm_memory_overhead_mb", 0) or 0))
    for row in vm_rows:
        tmpl = vm_templates.get(row.template_id)
        if tmpl:
            cpu_millicores += max(1, int(getattr(tmpl, "cpu_cores", 1) or 1)) * 1000
            memory_mb += max(1, int(getattr(tmpl, "ram_mb", 512) or 512)) + vm_memory_overhead
            storage_gib += _estimate_vm_storage_gib(images.get(tmpl.image_id))
        else:
            cpu_millicores += 1000
            memory_mb += 512 + vm_memory_overhead
            storage_gib += FALLBACK_VM_STORAGE_GIB

    for row in container_rows:
        tmpl = container_templates.get(row.template_id)
        cpu_millicores += max(1, int(getattr(tmpl, "cpu_millicores", 500) or 500))
        memory_mb += max(1, int(getattr(tmpl, "memory_mb", 512) or 512))
        storage_gib += max(
            1, int(getattr(tmpl, "storage_gib", DEFAULT_CONTAINER_STORAGE_GIB) or DEFAULT_CONTAINER_STORAGE_GIB)
        )

    return active_labs, cpu_millicores, memory_mb, storage_gib


def team_idle_timeout_cap(session: Session, team: str | None, namespace: str | None) -> int | None:
    _ = normalize_team(team)
    namespace_name = normalize_namespace(namespace)
    quota = _namespace_quota(session, namespace=namespace_name)
    if not quota:
        return None
    return normalize_optional_limit(getattr(quota, "idle_timeout_minutes_cap", None))


def enforce_team_quota(
    session: Session,
    *,
    team: str | None,
    namespace: str | None,
    requested_labs: int,
    requested_cpu_millicores: int,
    requested_memory_mb: int,
    requested_storage_gib: int,
    requested_idle_timeout_minutes: int,
    exclude_vm_instance_id: str | None = None,
    exclude_container_instance_id: str | None = None,
) -> TeamQuotaCheckResult:
    _ = normalize_team(team)
    namespace_name = normalize_namespace(namespace)
    effective_idle = max(1, int(requested_idle_timeout_minutes or settings.idle_timeout_minutes))

    quota = _namespace_quota(session, namespace=namespace_name)
    if not quota:
        return TeamQuotaCheckResult(effective_idle_timeout_minutes=effective_idle, error_detail=None)

    idle_cap = normalize_optional_limit(getattr(quota, "idle_timeout_minutes_cap", None))
    if idle_cap is not None:
        effective_idle = min(effective_idle, idle_cap)

    active_labs, used_cpu_m, used_mem_mb, used_storage_gib = _active_namespace_usage(
        session,
        namespace=namespace_name,
        exclude_vm_instance_id=exclude_vm_instance_id,
        exclude_container_instance_id=exclude_container_instance_id,
    )

    max_labs = normalize_optional_limit(getattr(quota, "max_concurrent_labs", None))
    if max_labs is not None and active_labs + max(0, int(requested_labs or 0)) > max_labs:
        return TeamQuotaCheckResult(
            effective_idle_timeout_minutes=effective_idle,
            error_detail=f"namespace quota reached in {namespace_name}: max concurrent labs is {max_labs}",
        )

    max_cpu = normalize_optional_limit(getattr(quota, "max_cpu_millicores", None))
    if max_cpu is not None and used_cpu_m + max(0, int(requested_cpu_millicores or 0)) > max_cpu:
        return TeamQuotaCheckResult(
            effective_idle_timeout_minutes=effective_idle,
            error_detail=f"namespace quota reached in {namespace_name}: CPU cap is {max_cpu} millicores",
        )

    max_mem = normalize_optional_limit(getattr(quota, "max_memory_mb", None))
    if max_mem is not None and used_mem_mb + max(0, int(requested_memory_mb or 0)) > max_mem:
        return TeamQuotaCheckResult(
            effective_idle_timeout_minutes=effective_idle,
            error_detail=f"namespace quota reached in {namespace_name}: memory cap is {max_mem} MB",
        )

    max_storage = normalize_optional_limit(getattr(quota, "max_storage_gib", None))
    if max_storage is not None and used_storage_gib + max(0, int(requested_storage_gib or 0)) > max_storage:
        return TeamQuotaCheckResult(
            effective_idle_timeout_minutes=effective_idle,
            error_detail=f"namespace quota reached in {namespace_name}: storage cap is {max_storage} GiB",
        )

    return TeamQuotaCheckResult(effective_idle_timeout_minutes=effective_idle, error_detail=None)


def enforce_team_quota_or_raise(session: Session, **kwargs: object) -> int:
    result = enforce_team_quota(session, **kwargs)
    if result.error_detail:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=result.error_detail)
    return result.effective_idle_timeout_minutes
