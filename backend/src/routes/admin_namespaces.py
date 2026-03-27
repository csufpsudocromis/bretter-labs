import json
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from kubernetes import client
from kubernetes.client import ApiException
from sqlmodel import Session, func, select

from ..auth import require_permission, require_user
from ..config import settings
from ..db import get_session
from ..models import (
    ManagedNamespaceCleanupStepOut,
    ManagedNamespaceCreate,
    ManagedNamespaceDecommissionOut,
    ManagedNamespaceObservabilityOut,
    ManagedNamespaceOut,
    ManagedNamespaceUpdate,
)
from ..rbac import Permission, Role, role_for_user
from ..services.kubernetes import kube
from ..services.team_quotas import normalize_namespace, normalize_team
from ..services.tenant_context import (
    GLOBAL_TENANT,
    actor_namespace_scopes,
    actor_tenant,
    assert_actor_can_access_namespace,
    is_platform_admin,
    normalize_tenant,
)
from ..services.tenant_namespace_bootstrap import NamespaceBootstrapPolicy, ensure_team_runtime_namespace
from ..tables import (
    AdminAuditEvent,
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    ImageUploadTask,
    Instance,
    ManagedNamespace,
    TeamQuota,
    Template,
    User,
)
from ..time_utils import utc_now

router = APIRouter(dependencies=[Depends(require_permission(Permission.ADMIN_ACCESS))])

_NAMESPACE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_ACTIVE_CONTAINER_STATUSES = {"queued", "pending", "running"}
_ACTIVE_VM_STATUSES = {"pending", "running"}
_FAILED_CONTAINER_STATUSES = {"failed"}
_FAILED_VM_STATUSES = {"failed"}
_QUEUED_CONTAINER_STATUSES = {"queued", "pending"}
_RUNNING_CONTAINER_STATUSES = {"running"}
_RUNNING_VM_STATUSES = {"running"}
_REQUIRED_NETWORK_POLICIES = {
    "default-deny-ingress",
    "default-deny-egress",
    "allow-dns-egress",
    "allow-same-namespace-traffic",
    "allow-control-plane-ingress",
}
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
            namespace=normalize_namespace(target_id) or normalize_namespace(settings.kube_namespace) or "labs",
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


def _namespace_scope_for_actor(actor: User) -> set[str] | None:
    return actor_namespace_scopes(actor)


def _can_write_unsafe_netpol(actor: User) -> bool:
    if bool(getattr(settings, "production_profile", False)):
        return False
    return is_platform_admin(actor)


def _validate_netpol_setting(actor: User, *, enforce_network_policies: bool) -> None:
    if enforce_network_policies:
        return
    if not _can_write_unsafe_netpol(actor):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="disabling default network policies is not allowed for this environment/role",
        )


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
        idle_timeout_minutes_default=max(1, int(payload.idle_timeout_minutes_default or 30)),
        vm_auto_delete_minutes_default=max(1, int(payload.vm_auto_delete_minutes_default or 60)),
        container_auto_delete_minutes_default=max(1, int(payload.container_auto_delete_minutes_default or 60)),
        queue_max_pending=max(1, int(payload.queue_max_pending or 25)),
        upload_max_bytes=max(1, int(payload.upload_max_bytes or (60 * 1024 * 1024 * 1024))),
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
    if payload.idle_timeout_minutes_default is not None:
        row.idle_timeout_minutes_default = max(1, int(payload.idle_timeout_minutes_default))
    if payload.vm_auto_delete_minutes_default is not None:
        row.vm_auto_delete_minutes_default = max(1, int(payload.vm_auto_delete_minutes_default))
    if payload.container_auto_delete_minutes_default is not None:
        row.container_auto_delete_minutes_default = max(1, int(payload.container_auto_delete_minutes_default))
    if payload.queue_max_pending is not None:
        row.queue_max_pending = max(1, int(payload.queue_max_pending))
    if payload.upload_max_bytes is not None:
        row.upload_max_bytes = max(1, int(payload.upload_max_bytes))
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
        idle_timeout_minutes_default=max(1, int(getattr(row, "idle_timeout_minutes_default", 30) or 30)),
        vm_auto_delete_minutes_default=max(1, int(getattr(row, "vm_auto_delete_minutes_default", 60) or 60)),
        container_auto_delete_minutes_default=max(
            1, int(getattr(row, "container_auto_delete_minutes_default", 60) or 60)
        ),
        queue_max_pending=max(1, int(getattr(row, "queue_max_pending", 25) or 25)),
        upload_max_bytes=max(1, int(getattr(row, "upload_max_bytes", 60 * 1024 * 1024 * 1024) or 1)),
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


def _parse_enabled_namespaces(raw: str | None) -> list[str]:
    payload = str(raw or "").strip()
    if not payload:
        return []
    try:
        decoded = json.loads(payload)
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    normalized: set[str] = set()
    for item in decoded:
        value = normalize_namespace(item)
        if value:
            normalized.add(value)
    return sorted(normalized)


def _serialize_enabled_namespaces(values: list[str]) -> str:
    deduped = sorted({normalize_namespace(item) for item in values if normalize_namespace(item)})
    return json.dumps(deduped, separators=(",", ":"))


def _strip_namespace_from_template_bindings(session: Session, namespace: str) -> int:
    removed = 0
    for row in session.exec(select(Template)).all():
        enabled = _parse_enabled_namespaces(getattr(row, "enabled_namespaces_json", "[]"))
        if namespace not in enabled:
            continue
        row.enabled_namespaces_json = _serialize_enabled_namespaces([item for item in enabled if item != namespace])
        session.add(row)
        removed += 1
    for row in session.exec(select(ContainerTemplate)).all():
        enabled = _parse_enabled_namespaces(getattr(row, "enabled_namespaces_json", "[]"))
        if namespace not in enabled:
            continue
        row.enabled_namespaces_json = _serialize_enabled_namespaces([item for item in enabled if item != namespace])
        session.add(row)
        removed += 1
    return removed


def _delete_rows_for_namespace(session: Session, model: type, namespace: str) -> int:
    deleted = 0
    rows = session.exec(select(model).where(model.namespace == namespace)).all()
    for row in rows:
        session.delete(row)
        deleted += 1
    return deleted


def _count_rows_for_namespace(
    session: Session, model: type, namespace: str, *, statuses: set[str] | None = None
) -> int:
    stmt = select(func.count()).select_from(model).where(model.namespace == namespace)
    if statuses:
        stmt = stmt.where(model.status.in_(sorted(statuses)))
    return int(session.exec(stmt).one() or 0)


def _namespace_policy_presence(namespace: str) -> tuple[bool, bool, int, list[str]]:
    quota_present = False
    limit_range_present = False
    policy_count = 0
    required_missing = sorted(_REQUIRED_NETWORK_POLICIES)
    try:
        core = kube._client()
        networking = kube._networking_client()
        try:
            core.read_namespaced_resource_quota(name="bretter-tenant-quota", namespace=namespace)
            quota_present = True
        except ApiException as exc:
            if exc.status != 404:
                raise
        try:
            core.read_namespaced_limit_range(name="bretter-tenant-default-limits", namespace=namespace)
            limit_range_present = True
        except ApiException as exc:
            if exc.status != 404:
                raise
        names: set[str] = set()
        try:
            for item in networking.list_namespaced_network_policy(namespace=namespace).items:
                name = str(getattr(getattr(item, "metadata", None), "name", "") or "").strip()
                if name:
                    names.add(name)
        except ApiException as exc:
            if exc.status != 404:
                raise
        policy_count = len(names)
        required_missing = sorted([name for name in _REQUIRED_NETWORK_POLICIES if name not in names])
    except Exception:
        pass
    return quota_present, limit_range_present, policy_count, required_missing


def _delete_namespaced_runtime_resources(namespace: str) -> int:
    deleted = 0
    core = kube._client()
    batch = client.BatchV1Api(core.api_client)
    networking = kube._networking_client()
    rbac = client.RbacAuthorizationV1Api(core.api_client)
    label_selector = "app.kubernetes.io/part-of=bretter-labs"

    def _safe_items(call) -> list:
        try:
            return list((call().items) or [])
        except ApiException as exc:
            if exc.status == 404:
                return []
            raise

    for pod in _safe_items(lambda: core.list_namespaced_pod(namespace=namespace, label_selector=label_selector)):
        name = str(getattr(getattr(pod, "metadata", None), "name", "") or "").strip()
        if not name:
            continue
        try:
            core.delete_namespaced_pod(name=name, namespace=namespace, grace_period_seconds=0)
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise

    for service in _safe_items(
        lambda: core.list_namespaced_service(namespace=namespace, label_selector=label_selector)
    ):
        name = str(getattr(getattr(service, "metadata", None), "name", "") or "").strip()
        if not name:
            continue
        try:
            core.delete_namespaced_service(name=name, namespace=namespace)
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise

    for claim in _safe_items(
        lambda: core.list_namespaced_persistent_volume_claim(namespace=namespace, label_selector=label_selector)
    ):
        name = str(getattr(getattr(claim, "metadata", None), "name", "") or "").strip()
        if not name:
            continue
        try:
            core.delete_namespaced_persistent_volume_claim(name=name, namespace=namespace)
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise

    for job in _safe_items(lambda: batch.list_namespaced_job(namespace=namespace, label_selector=label_selector)):
        name = str(getattr(getattr(job, "metadata", None), "name", "") or "").strip()
        if not name:
            continue
        try:
            batch.delete_namespaced_job(name=name, namespace=namespace, propagation_policy="Background")
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise

    try:
        custom = client.CustomObjectsApi(core.api_client)
        for group, version, plural in [
            ("cdi.kubevirt.io", "v1beta1", "datavolumes"),
            (settings.labinstance_crd_group, settings.labinstance_crd_version, settings.labinstance_crd_plural),
            (
                settings.labimageimport_crd_group,
                settings.labimageimport_crd_version,
                settings.labimageimport_crd_plural,
            ),
        ]:
            try:
                items = custom.list_namespaced_custom_object(
                    group=group, version=version, namespace=namespace, plural=plural
                )
            except ApiException as exc:
                if exc.status == 404:
                    continue
                raise
            for item in items.get("items", []):
                name = str((item.get("metadata") or {}).get("name") or "").strip()
                if not name:
                    continue
                try:
                    custom.delete_namespaced_custom_object(
                        group=group,
                        version=version,
                        namespace=namespace,
                        plural=plural,
                        name=name,
                        body=client.V1DeleteOptions(),
                    )
                    deleted += 1
                except ApiException as exc:
                    if exc.status != 404:
                        raise
    except Exception:
        pass

    for policy in sorted(_REQUIRED_NETWORK_POLICIES):
        try:
            networking.delete_namespaced_network_policy(name=policy, namespace=namespace)
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise

    for name in ("bretter-backend-runtime", "bretter-tenant-quota", "bretter-tenant-default-limits"):
        try:
            if name == "bretter-backend-runtime":
                rbac.delete_namespaced_role(name=name, namespace=namespace)
            elif name == "bretter-tenant-quota":
                core.delete_namespaced_resource_quota(name=name, namespace=namespace)
            else:
                core.delete_namespaced_limit_range(name=name, namespace=namespace)
            deleted += 1
        except ApiException as exc:
            if exc.status != 404:
                raise
    try:
        rbac.delete_namespaced_role_binding(name="bretter-backend-runtime", namespace=namespace)
        deleted += 1
    except ApiException as exc:
        if exc.status != 404:
            raise
    return deleted


def _decommission_managed_namespace(
    session: Session,
    *,
    row: ManagedNamespace,
    delete_cluster_namespace: bool,
    force_cleanup: bool,
) -> ManagedNamespaceDecommissionOut:
    namespace = row.namespace
    steps: list[ManagedNamespaceCleanupStepOut] = []
    deleted_database_records = 0
    deleted_cluster_resources = 0

    vm_active, container_active = _active_namespace_counts(session, namespace)
    active_total = vm_active + container_active
    if active_total > 0 and not force_cleanup:
        steps.append(
            ManagedNamespaceCleanupStepOut(
                step="active_labs_check",
                status="error",
                detail=f"namespace has active labs (vm={vm_active}, container={container_active})",
            )
        )
        return ManagedNamespaceDecommissionOut(
            namespace=namespace,
            delete_cluster_namespace=bool(delete_cluster_namespace),
            force_cleanup=False,
            blocked=True,
            steps=steps,
            finished_at=utc_now(),
        )
    steps.append(
        ManagedNamespaceCleanupStepOut(
            step="active_labs_check",
            status="warning" if active_total > 0 else "ok",
            detail=f"vm={vm_active}, container={container_active}",
            affected=active_total,
        )
    )

    if force_cleanup:
        deleted_cluster_resources = _delete_namespaced_runtime_resources(namespace)
        steps.append(
            ManagedNamespaceCleanupStepOut(
                step="runtime_resource_cleanup",
                status="ok",
                detail="deleted namespaced runtime resources with bretter labels and known runtime CRs",
                affected=deleted_cluster_resources,
            )
        )
    else:
        steps.append(
            ManagedNamespaceCleanupStepOut(
                step="runtime_resource_cleanup",
                status="skipped",
                detail="force_cleanup=false",
            )
        )

    stripped_bindings = _strip_namespace_from_template_bindings(session, namespace)
    steps.append(
        ManagedNamespaceCleanupStepOut(
            step="template_binding_cleanup",
            status="ok",
            detail="removed namespace from template enabled lists",
            affected=stripped_bindings,
        )
    )

    for model in (
        Instance,
        ContainerInstance,
        ImageUploadTask,
        Template,
        ContainerTemplate,
        Image,
        ContainerImage,
        TeamQuota,
    ):
        deleted_database_records += _delete_rows_for_namespace(session, model, namespace)
    steps.append(
        ManagedNamespaceCleanupStepOut(
            step="database_cleanup",
            status="ok",
            detail="deleted namespace-scoped runtime records, artifacts, and quota rows",
            affected=deleted_database_records,
        )
    )

    if delete_cluster_namespace:
        control_namespace = normalize_namespace(settings.kube_namespace)
        if namespace == control_namespace:
            steps.append(
                ManagedNamespaceCleanupStepOut(
                    step="delete_cluster_namespace",
                    status="error",
                    detail="cannot delete the control-plane namespace",
                )
            )
            return ManagedNamespaceDecommissionOut(
                namespace=namespace,
                delete_cluster_namespace=True,
                force_cleanup=bool(force_cleanup),
                blocked=True,
                deleted_database_records=deleted_database_records,
                deleted_cluster_resources=deleted_cluster_resources,
                steps=steps,
                finished_at=utc_now(),
            )
        try:
            kube._client().delete_namespace(name=namespace)
            steps.append(
                ManagedNamespaceCleanupStepOut(
                    step="delete_cluster_namespace",
                    status="ok",
                    detail="namespace delete requested",
                    affected=1,
                )
            )
        except ApiException as exc:
            if exc.status == 404:
                steps.append(
                    ManagedNamespaceCleanupStepOut(
                        step="delete_cluster_namespace",
                        status="warning",
                        detail="namespace already absent in cluster",
                    )
                )
            else:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.reason or str(exc))
    else:
        steps.append(
            ManagedNamespaceCleanupStepOut(
                step="delete_cluster_namespace",
                status="skipped",
                detail="delete_cluster_namespace=false",
            )
        )

    session.delete(row)
    steps.append(ManagedNamespaceCleanupStepOut(step="remove_managed_namespace_row", status="ok", detail="deleted"))
    return ManagedNamespaceDecommissionOut(
        namespace=namespace,
        delete_cluster_namespace=bool(delete_cluster_namespace),
        force_cleanup=bool(force_cleanup),
        blocked=False,
        deleted_database_records=deleted_database_records,
        deleted_cluster_resources=deleted_cluster_resources,
        steps=steps,
        finished_at=utc_now(),
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
    scope = _namespace_scope_for_actor(actor)
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(ManagedNamespace.namespace.in_(sorted(scope)))
    rows = session.exec(stmt).all()
    rows.sort(key=lambda item: item.namespace)
    present_names = _list_cluster_namespaces()
    return [_managed_namespace_out(session, row, present_names=present_names) for row in rows]


@router.get(
    "/settings/namespaces/observability",
    response_model=list[ManagedNamespaceObservabilityOut],
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def list_managed_namespace_observability(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ManagedNamespaceObservabilityOut]:
    stmt = select(ManagedNamespace)
    scope = _namespace_scope_for_actor(actor)
    if scope is not None:
        if not scope:
            return []
        stmt = stmt.where(ManagedNamespace.namespace.in_(sorted(scope)))
    rows = session.exec(stmt).all()
    present = _list_cluster_namespaces()
    out: list[ManagedNamespaceObservabilityOut] = []
    for row in sorted(rows, key=lambda item: item.namespace):
        namespace = row.namespace
        quota_present, limit_range_present, network_policy_count, missing_policies = _namespace_policy_presence(
            namespace
        )
        out.append(
            ManagedNamespaceObservabilityOut(
                namespace=namespace,
                enabled=bool(row.enabled),
                present_in_cluster=namespace in present,
                active_vm_instances=_count_rows_for_namespace(
                    session, Instance, namespace, statuses=_ACTIVE_VM_STATUSES
                ),
                active_container_instances=_count_rows_for_namespace(
                    session, ContainerInstance, namespace, statuses=_ACTIVE_CONTAINER_STATUSES
                ),
                queued_container_instances=_count_rows_for_namespace(
                    session, ContainerInstance, namespace, statuses=_QUEUED_CONTAINER_STATUSES
                ),
                failed_total_instances=(
                    _count_rows_for_namespace(session, Instance, namespace, statuses=_FAILED_VM_STATUSES)
                    + _count_rows_for_namespace(
                        session, ContainerInstance, namespace, statuses=_FAILED_CONTAINER_STATUSES
                    )
                ),
                running_total_instances=(
                    _count_rows_for_namespace(session, Instance, namespace, statuses=_RUNNING_VM_STATUSES)
                    + _count_rows_for_namespace(
                        session, ContainerInstance, namespace, statuses=_RUNNING_CONTAINER_STATUSES
                    )
                ),
                image_upload_tasks_pending=_count_rows_for_namespace(
                    session,
                    ImageUploadTask,
                    namespace,
                    statuses={"queued", "uploading", "finalizing", "pending", "running"},
                ),
                image_upload_tasks_failed=_count_rows_for_namespace(
                    session, ImageUploadTask, namespace, statuses={"failed", "error"}
                ),
                resource_quota_present=quota_present,
                limit_range_present=limit_range_present,
                network_policy_count=network_policy_count,
                required_network_policies_missing=missing_policies if bool(row.enforce_network_policies) else [],
                last_reconciled_at=row.last_reconciled_at,
            )
        )
    return out


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
    _validate_netpol_setting(actor, enforce_network_policies=bool(row.enforce_network_policies))
    scope = _namespace_scope_for_actor(actor)
    if scope is not None and row.namespace not in scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"namespace access denied: {row.namespace}")
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
    assert_actor_can_access_namespace(actor, row.namespace)
    _apply_update_payload(row, payload)
    _validate_netpol_setting(actor, enforce_network_policies=bool(row.enforce_network_policies))
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
    assert_actor_can_access_namespace(actor, row.namespace)
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


@router.post(
    "/settings/namespaces/reconcile-all",
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def reconcile_all_managed_namespaces(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> dict[str, object]:
    _require_namespace_admin(actor)
    stmt = select(ManagedNamespace).where(ManagedNamespace.enabled == True)  # noqa: E712
    scope = _namespace_scope_for_actor(actor)
    if scope is not None:
        if not scope:
            return {"total": 0, "succeeded": 0, "failed": 0, "results": []}
        stmt = stmt.where(ManagedNamespace.namespace.in_(sorted(scope)))
    rows = session.exec(stmt).all()
    results: list[dict[str, str]] = []
    for row in rows:
        try:
            _reconcile_managed_namespace(row)
            row.last_reconciled_at = utc_now()
            row.updated_at = row.last_reconciled_at
            session.add(row)
            results.append({"namespace": row.namespace, "status": "ok", "detail": "reconciled"})
        except Exception as exc:
            results.append({"namespace": row.namespace, "status": "error", "detail": str(exc)})
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="reconcile_all",
        target_type="managed_namespace",
        target_id="*",
        detail=f"count={len(rows)}",
    )
    session.commit()
    failed = [item for item in results if item["status"] != "ok"]
    return {
        "total": len(results),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }


@router.delete(
    "/settings/namespaces/{namespace}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def delete_managed_namespace(
    namespace: str,
    delete_cluster_namespace: bool = Query(default=True),
    force_cleanup: bool = Query(default=False),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ManagedNamespaceDecommissionOut:
    return decommission_managed_namespace(
        namespace=namespace,
        delete_cluster_namespace=delete_cluster_namespace,
        force_cleanup=force_cleanup,
        session=session,
        actor=actor,
    )


@router.post(
    "/settings/namespaces/{namespace}/decommission",
    response_model=ManagedNamespaceDecommissionOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def decommission_managed_namespace(
    namespace: str,
    delete_cluster_namespace: bool = Query(default=True),
    force_cleanup: bool = Query(default=False),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ManagedNamespaceDecommissionOut:
    _require_namespace_admin(actor)
    row = _get_managed_namespace_or_404(session, namespace)
    assert_actor_can_access_namespace(actor, row.namespace)
    report = _decommission_managed_namespace(
        session,
        row=row,
        delete_cluster_namespace=bool(delete_cluster_namespace),
        force_cleanup=bool(force_cleanup),
    )
    if report.blocked:
        session.rollback()
        detail = ""
        for step in report.steps:
            if step.status == "error":
                detail = step.detail
                break
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail or f"namespace {row.namespace} could not be decommissioned",
        )
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="decommission",
        target_type="managed_namespace",
        target_id=row.namespace,
        detail=(
            f"delete_cluster_namespace={bool(delete_cluster_namespace)} "
            f"force_cleanup={bool(force_cleanup)} "
            f"deleted_db={int(report.deleted_database_records)} "
            f"deleted_cluster={int(report.deleted_cluster_resources)}"
        ),
    )
    session.commit()
    return report
