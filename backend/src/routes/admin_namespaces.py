import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from kubernetes.client import ApiException
from sqlmodel import Session, func, select

from ..auth import require_permission, require_user
from ..config import settings
from ..db import get_session
from ..models import ManagedNamespaceCreate, ManagedNamespaceOut, ManagedNamespaceUpdate
from ..rbac import Permission, Role, role_for_user
from ..services.kubernetes import kube
from ..services.team_quotas import normalize_namespace, normalize_team
from ..services.tenant_context import GLOBAL_TENANT, actor_tenant, is_platform_admin, normalize_tenant
from ..services.tenant_namespace_bootstrap import NamespaceBootstrapPolicy, ensure_team_runtime_namespace
from ..tables import AdminAuditEvent, ContainerInstance, Instance, ManagedNamespace, User
from ..time_utils import utc_now

router = APIRouter(dependencies=[Depends(require_permission(Permission.ADMIN_ACCESS))])

_NAMESPACE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_ACTIVE_CONTAINER_STATUSES = {"queued", "pending", "running"}
_ACTIVE_VM_STATUSES = {"pending", "running"}
_ADMIN_AUDIT_EVENT_MAX_PER_TENANT = 50


def _record_admin_audit_event(
    session: Session,
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: str,
    tenant: str = GLOBAL_TENANT,
) -> None:
    normalized_tenant = normalize_tenant(tenant, default=GLOBAL_TENANT)
    session.add(
        AdminAuditEvent(
            id=str(uuid4()),
            actor=actor,
            tenant=normalized_tenant,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            created_at=utc_now(),
        )
    )
    _prune_admin_audit_events(session, tenant=normalized_tenant)


def _prune_admin_audit_events(session: Session, *, tenant: str) -> None:
    total_events = int(
        session.exec(select(func.count()).select_from(AdminAuditEvent).where(AdminAuditEvent.tenant == tenant)).one()
        or 0
    )
    overflow = total_events - _ADMIN_AUDIT_EVENT_MAX_PER_TENANT
    if overflow <= 0:
        return
    stale_rows = session.exec(
        select(AdminAuditEvent)
        .where(AdminAuditEvent.tenant == tenant)
        .order_by(AdminAuditEvent.created_at.asc(), AdminAuditEvent.id.asc())
        .limit(overflow)
    ).all()
    for row in stale_rows:
        session.delete(row)


def _require_namespace_admin(user: User) -> None:
    if is_platform_admin(user) or role_for_user(user) == Role.NAMESPACE_ADMIN:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="namespace admin required")


def _normalize_namespace_name_or_raise(raw: str | None) -> str:
    value = normalize_namespace(raw)
    if not value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="namespace is required")
    if len(value) > 63 or _NAMESPACE_NAME_RE.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="namespace must be a valid DNS-1123 label (max 63 chars)",
        )
    return value


def _sanitize_quantity(value: str | None, *, fallback: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


def _managed_namespace_policy(row: ManagedNamespace) -> NamespaceBootstrapPolicy:
    profile = str(getattr(row, "security_profile", "") or "").strip().lower()
    return NamespaceBootstrapPolicy(
        team_label=normalize_team(getattr(row, "team_label", None)),
        security_profile=profile if profile in {"restricted", "baseline", "privileged"} else "baseline",
        enforce_network_policies=bool(getattr(row, "enforce_network_policies", True)),
        max_pods=_sanitize_quantity(getattr(row, "max_pods", None), fallback="200"),
        max_services=_sanitize_quantity(getattr(row, "max_services", None), fallback="100"),
        max_persistent_volume_claims=_sanitize_quantity(
            getattr(row, "max_persistent_volume_claims", None), fallback="200"
        ),
        requests_cpu=_sanitize_quantity(getattr(row, "requests_cpu", None), fallback="8"),
        limits_cpu=_sanitize_quantity(getattr(row, "limits_cpu", None), fallback="16"),
        requests_memory=_sanitize_quantity(getattr(row, "requests_memory", None), fallback="16Gi"),
        limits_memory=_sanitize_quantity(getattr(row, "limits_memory", None), fallback="32Gi"),
        requests_storage=_sanitize_quantity(getattr(row, "requests_storage", None), fallback="2Ti"),
        limit_min_cpu=_sanitize_quantity(getattr(row, "limit_min_cpu", None), fallback="50m"),
        limit_min_memory=_sanitize_quantity(getattr(row, "limit_min_memory", None), fallback="64Mi"),
        limit_default_request_cpu=_sanitize_quantity(getattr(row, "limit_default_request_cpu", None), fallback="250m"),
        limit_default_request_memory=_sanitize_quantity(
            getattr(row, "limit_default_request_memory", None), fallback="256Mi"
        ),
        limit_default_cpu=_sanitize_quantity(getattr(row, "limit_default_cpu", None), fallback="2"),
        limit_default_memory=_sanitize_quantity(getattr(row, "limit_default_memory", None), fallback="2Gi"),
        limit_max_cpu=_sanitize_quantity(getattr(row, "limit_max_cpu", None), fallback="8"),
        limit_max_memory=_sanitize_quantity(getattr(row, "limit_max_memory", None), fallback="16Gi"),
    )


def _apply_create_payload(payload: ManagedNamespaceCreate) -> ManagedNamespace:
    now = utc_now()
    return ManagedNamespace(
        id=str(uuid4()),
        namespace=_normalize_namespace_name_or_raise(payload.namespace),
        team_label=normalize_team(payload.team_label),
        security_profile=str(payload.security_profile or "baseline").strip().lower(),
        enforce_network_policies=bool(payload.enforce_network_policies),
        max_pods=_sanitize_quantity(payload.max_pods, fallback="200"),
        max_services=_sanitize_quantity(payload.max_services, fallback="100"),
        max_persistent_volume_claims=_sanitize_quantity(payload.max_persistent_volume_claims, fallback="200"),
        requests_cpu=_sanitize_quantity(payload.requests_cpu, fallback="8"),
        limits_cpu=_sanitize_quantity(payload.limits_cpu, fallback="16"),
        requests_memory=_sanitize_quantity(payload.requests_memory, fallback="16Gi"),
        limits_memory=_sanitize_quantity(payload.limits_memory, fallback="32Gi"),
        requests_storage=_sanitize_quantity(payload.requests_storage, fallback="2Ti"),
        limit_min_cpu=_sanitize_quantity(payload.limit_min_cpu, fallback="50m"),
        limit_min_memory=_sanitize_quantity(payload.limit_min_memory, fallback="64Mi"),
        limit_default_request_cpu=_sanitize_quantity(payload.limit_default_request_cpu, fallback="250m"),
        limit_default_request_memory=_sanitize_quantity(payload.limit_default_request_memory, fallback="256Mi"),
        limit_default_cpu=_sanitize_quantity(payload.limit_default_cpu, fallback="2"),
        limit_default_memory=_sanitize_quantity(payload.limit_default_memory, fallback="2Gi"),
        limit_max_cpu=_sanitize_quantity(payload.limit_max_cpu, fallback="8"),
        limit_max_memory=_sanitize_quantity(payload.limit_max_memory, fallback="16Gi"),
        enabled=bool(payload.enabled),
        created_at=now,
        updated_at=now,
    )


def _apply_update_payload(row: ManagedNamespace, payload: ManagedNamespaceUpdate) -> None:
    if payload.team_label is not None:
        row.team_label = normalize_team(payload.team_label)
    if payload.security_profile is not None:
        row.security_profile = str(payload.security_profile).strip().lower()
    if payload.enforce_network_policies is not None:
        row.enforce_network_policies = bool(payload.enforce_network_policies)
    if payload.max_pods is not None:
        row.max_pods = _sanitize_quantity(payload.max_pods, fallback=row.max_pods)
    if payload.max_services is not None:
        row.max_services = _sanitize_quantity(payload.max_services, fallback=row.max_services)
    if payload.max_persistent_volume_claims is not None:
        row.max_persistent_volume_claims = _sanitize_quantity(
            payload.max_persistent_volume_claims, fallback=row.max_persistent_volume_claims
        )
    if payload.requests_cpu is not None:
        row.requests_cpu = _sanitize_quantity(payload.requests_cpu, fallback=row.requests_cpu)
    if payload.limits_cpu is not None:
        row.limits_cpu = _sanitize_quantity(payload.limits_cpu, fallback=row.limits_cpu)
    if payload.requests_memory is not None:
        row.requests_memory = _sanitize_quantity(payload.requests_memory, fallback=row.requests_memory)
    if payload.limits_memory is not None:
        row.limits_memory = _sanitize_quantity(payload.limits_memory, fallback=row.limits_memory)
    if payload.requests_storage is not None:
        row.requests_storage = _sanitize_quantity(payload.requests_storage, fallback=row.requests_storage)
    if payload.limit_min_cpu is not None:
        row.limit_min_cpu = _sanitize_quantity(payload.limit_min_cpu, fallback=row.limit_min_cpu)
    if payload.limit_min_memory is not None:
        row.limit_min_memory = _sanitize_quantity(payload.limit_min_memory, fallback=row.limit_min_memory)
    if payload.limit_default_request_cpu is not None:
        row.limit_default_request_cpu = _sanitize_quantity(
            payload.limit_default_request_cpu, fallback=row.limit_default_request_cpu
        )
    if payload.limit_default_request_memory is not None:
        row.limit_default_request_memory = _sanitize_quantity(
            payload.limit_default_request_memory, fallback=row.limit_default_request_memory
        )
    if payload.limit_default_cpu is not None:
        row.limit_default_cpu = _sanitize_quantity(payload.limit_default_cpu, fallback=row.limit_default_cpu)
    if payload.limit_default_memory is not None:
        row.limit_default_memory = _sanitize_quantity(payload.limit_default_memory, fallback=row.limit_default_memory)
    if payload.limit_max_cpu is not None:
        row.limit_max_cpu = _sanitize_quantity(payload.limit_max_cpu, fallback=row.limit_max_cpu)
    if payload.limit_max_memory is not None:
        row.limit_max_memory = _sanitize_quantity(payload.limit_max_memory, fallback=row.limit_max_memory)
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
    row.updated_at = utc_now()


def _active_namespace_counts(session: Session, namespace: str) -> tuple[int, int]:
    vm_active = int(
        session.exec(
            select(func.count())
            .select_from(Instance)
            .where(Instance.namespace == namespace)
            .where(Instance.status.in_(list(_ACTIVE_VM_STATUSES)))
        ).one()
        or 0
    )
    container_active = int(
        session.exec(
            select(func.count())
            .select_from(ContainerInstance)
            .where(ContainerInstance.namespace == namespace)
            .where(ContainerInstance.status.in_(list(_ACTIVE_CONTAINER_STATUSES)))
        ).one()
        or 0
    )
    return vm_active, container_active


def _list_cluster_namespaces() -> set[str]:
    try:
        core = kube._client()
        return {
            normalize_namespace(getattr(item.metadata, "name", None))
            for item in core.list_namespace().items
            if normalize_namespace(getattr(item.metadata, "name", None))
        }
    except Exception:
        return set()


def _managed_namespace_out(
    session: Session,
    row: ManagedNamespace,
    *,
    present_names: set[str] | None = None,
) -> ManagedNamespaceOut:
    vm_active, container_active = _active_namespace_counts(session, row.namespace)
    present = row.namespace in (present_names or set())
    return ManagedNamespaceOut(
        id=row.id,
        namespace=row.namespace,
        team_label=normalize_team(row.team_label),
        security_profile=str(row.security_profile or "baseline").strip().lower(),
        enforce_network_policies=bool(row.enforce_network_policies),
        max_pods=_sanitize_quantity(row.max_pods, fallback="200"),
        max_services=_sanitize_quantity(row.max_services, fallback="100"),
        max_persistent_volume_claims=_sanitize_quantity(row.max_persistent_volume_claims, fallback="200"),
        requests_cpu=_sanitize_quantity(row.requests_cpu, fallback="8"),
        limits_cpu=_sanitize_quantity(row.limits_cpu, fallback="16"),
        requests_memory=_sanitize_quantity(row.requests_memory, fallback="16Gi"),
        limits_memory=_sanitize_quantity(row.limits_memory, fallback="32Gi"),
        requests_storage=_sanitize_quantity(row.requests_storage, fallback="2Ti"),
        limit_min_cpu=_sanitize_quantity(row.limit_min_cpu, fallback="50m"),
        limit_min_memory=_sanitize_quantity(row.limit_min_memory, fallback="64Mi"),
        limit_default_request_cpu=_sanitize_quantity(row.limit_default_request_cpu, fallback="250m"),
        limit_default_request_memory=_sanitize_quantity(row.limit_default_request_memory, fallback="256Mi"),
        limit_default_cpu=_sanitize_quantity(row.limit_default_cpu, fallback="2"),
        limit_default_memory=_sanitize_quantity(row.limit_default_memory, fallback="2Gi"),
        limit_max_cpu=_sanitize_quantity(row.limit_max_cpu, fallback="8"),
        limit_max_memory=_sanitize_quantity(row.limit_max_memory, fallback="16Gi"),
        enabled=bool(row.enabled),
        present_in_cluster=present,
        active_vm_instances=vm_active,
        active_container_instances=container_active,
        active_total_instances=vm_active + container_active,
        last_reconciled_at=row.last_reconciled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_managed_namespace_or_404(session: Session, namespace: str) -> ManagedNamespace:
    normalized = _normalize_namespace_name_or_raise(namespace)
    row = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == normalized)).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="managed namespace not found")
    return row


def _reconcile_managed_namespace(row: ManagedNamespace) -> None:
    policy = _managed_namespace_policy(row)
    privileged_runtime = str(policy.security_profile or "").strip().lower() == "privileged"
    ensure_team_runtime_namespace(
        kube,
        team=row.team_label,
        namespace=row.namespace,
        privileged_runtime=privileged_runtime,
        policy=policy,
        enforce_per_team_mode=False,
    )


@router.get(
    "/settings/namespaces",
    response_model=list[ManagedNamespaceOut],
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def list_managed_namespaces(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ManagedNamespaceOut]:
    stmt = select(ManagedNamespace)
    rows = session.exec(stmt).all()
    rows.sort(key=lambda item: item.namespace)
    present_names = _list_cluster_namespaces()
    return [_managed_namespace_out(session, row, present_names=present_names) for row in rows]


@router.post(
    "/settings/namespaces",
    response_model=ManagedNamespaceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def create_managed_namespace(
    payload: ManagedNamespaceCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ManagedNamespaceOut:
    _require_namespace_admin(actor)
    row = _apply_create_payload(payload)
    control_namespace = normalize_namespace(settings.kube_namespace)
    if row.namespace == control_namespace:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot manage the control-plane namespace")
    existing = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == row.namespace)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="managed namespace already exists")
    if row.enabled:
        try:
            _reconcile_managed_namespace(row)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        row.last_reconciled_at = utc_now()
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="create",
        target_type="managed_namespace",
        target_id=row.namespace,
        detail=f"profile={row.security_profile} team_label={row.team_label}",
    )
    session.commit()
    session.refresh(row)
    return _managed_namespace_out(session, row, present_names=_list_cluster_namespaces())


@router.patch(
    "/settings/namespaces/{namespace}",
    response_model=ManagedNamespaceOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_managed_namespace(
    namespace: str,
    payload: ManagedNamespaceUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ManagedNamespaceOut:
    _require_namespace_admin(actor)
    row = _get_managed_namespace_or_404(session, namespace)
    _apply_update_payload(row, payload)
    if row.enabled:
        try:
            _reconcile_managed_namespace(row)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        row.last_reconciled_at = utc_now()
        row.updated_at = row.last_reconciled_at
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="managed_namespace",
        target_id=row.namespace,
        detail=f"profile={row.security_profile} team_label={row.team_label}",
    )
    session.commit()
    session.refresh(row)
    return _managed_namespace_out(session, row, present_names=_list_cluster_namespaces())


@router.post(
    "/settings/namespaces/{namespace}/reconcile",
    response_model=ManagedNamespaceOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def reconcile_managed_namespace(
    namespace: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ManagedNamespaceOut:
    _require_namespace_admin(actor)
    row = _get_managed_namespace_or_404(session, namespace)
    if not row.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="managed namespace is disabled")
    try:
        _reconcile_managed_namespace(row)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    row.last_reconciled_at = utc_now()
    row.updated_at = row.last_reconciled_at
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="reconcile",
        target_type="managed_namespace",
        target_id=row.namespace,
        detail=f"profile={row.security_profile} team_label={row.team_label}",
    )
    session.commit()
    session.refresh(row)
    return _managed_namespace_out(session, row, present_names=_list_cluster_namespaces())


@router.delete(
    "/settings/namespaces/{namespace}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def delete_managed_namespace(
    namespace: str,
    delete_cluster_namespace: bool = Query(default=True),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    _require_namespace_admin(actor)
    row = _get_managed_namespace_or_404(session, namespace)
    vm_active, container_active = _active_namespace_counts(session, row.namespace)
    if vm_active + container_active > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"namespace {row.namespace} has active labs "
                f"(vm={vm_active}, container={container_active}); stop them before delete"
            ),
        )
    if delete_cluster_namespace:
        control_namespace = normalize_namespace(settings.kube_namespace)
        if row.namespace == control_namespace:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="cannot delete the control-plane namespace"
            )
        try:
            kube._client().delete_namespace(name=row.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.reason or str(exc))
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="delete",
        target_type="managed_namespace",
        target_id=row.namespace,
        detail=f"delete_cluster_namespace={bool(delete_cluster_namespace)}",
    )
    session.delete(row)
    session.commit()
