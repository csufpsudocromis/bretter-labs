import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, func, select

from ..auth import require_permission, require_user
from ..models import (
    ArtifactReplicationCreate,
    ArtifactReplicationOut,
    ArtifactReplicationUpdate,
    ClusterConfigCreate,
    ClusterConfigOut,
    ClusterConfigUpdate,
    ClusterTelemetryOut,
    PlacementCandidateOut,
    PlacementExplainOut,
    TeamPlacementPolicyOut,
    TeamPlacementPolicyUpdate,
)
from ..rbac import Permission
from ..secret_codec import encrypt_secret
from ..services.multi_cluster import (
    cluster_has_kubeconfig,
    cluster_kubeconfig_source,
    cluster_runtime_namespace,
    csv_tokens,
    explain_cluster_selection,
    ensure_local_cluster,
    kube_service_for_cluster,
    local_cluster_id,
    process_artifact_replication_queue,
    runtime_client_probe,
    split_csv_tokens,
)
from ..services.tenant_context import (
    GLOBAL_TENANT,
    actor_tenant,
    is_platform_admin,
    normalize_tenant,
)
from ..tables import (
    AdminAuditEvent,
    ArtifactReplication,
    Cluster,
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    Instance,
    TeamPlacementPolicy,
    Template,
    User,
)
from ..time_utils import utc_now
from ..db import get_session

router = APIRouter(dependencies=[Depends(require_permission(Permission.ADMIN_ACCESS))])
logger = logging.getLogger(__name__)


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
    session.add(
        AdminAuditEvent(
            id=str(uuid4()),
            actor=actor,
            tenant=normalize_tenant(tenant, default=GLOBAL_TENANT),
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            created_at=utc_now(),
        )
    )


def _require_platform_admin(user: User) -> None:
    if is_platform_admin(user):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required")


def _cluster_out(record: Cluster) -> ClusterConfigOut:
    secret_name = str(getattr(record, "kubeconfig_secret_name", "") or "").strip() or None
    secret_namespace = str(getattr(record, "kubeconfig_secret_namespace", "") or "").strip() or None
    secret_key = str(getattr(record, "kubeconfig_secret_key", "") or "").strip() or None
    return ClusterConfigOut(
        id=record.id,
        name=record.name,
        region=record.region,
        compliance_tags=split_csv_tokens(record.compliance_tags_csv),
        capacity_weight=max(1, int(record.capacity_weight or 100)),
        enabled=bool(record.enabled),
        schedule_enabled=bool(record.schedule_enabled),
        runtime_enabled=bool(record.runtime_enabled),
        is_local=bool(record.is_local),
        runtime_namespace=cluster_runtime_namespace(record),
        kubeconfig_configured=cluster_has_kubeconfig(record),
        kubeconfig_source=cluster_kubeconfig_source(record),
        kubeconfig_secret_name=secret_name,
        kubeconfig_secret_namespace=secret_namespace,
        kubeconfig_secret_key=(secret_key or "kubeconfig") if secret_name else None,
        notes=str(record.notes or ""),
        health_status=str(record.health_status or "unknown"),
        health_message=str(record.health_message or ""),
        last_heartbeat_at=getattr(record, "last_heartbeat_at", None),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _policy_out(record: TeamPlacementPolicy) -> TeamPlacementPolicyOut:
    return TeamPlacementPolicyOut(
        id=record.id,
        team=record.team,
        preferred_cluster_id=(record.preferred_cluster_id or None),
        hard_pin_cluster=bool(record.hard_pin_cluster),
        required_regions=split_csv_tokens(record.required_regions_csv),
        required_compliance_tags=split_csv_tokens(record.required_compliance_tags_csv),
        allowed_cluster_ids=split_csv_tokens(record.allowed_cluster_ids_csv),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _replication_out(record: ArtifactReplication) -> ArtifactReplicationOut:
    return ArtifactReplicationOut(
        id=record.id,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        artifact_type=record.artifact_type,
        artifact_id=record.artifact_id,
        source_cluster_id=record.source_cluster_id,
        target_cluster_id=record.target_cluster_id,
        status=record.status,
        detail=record.detail,
        requested_by=record.requested_by,
        last_attempt_at=record.last_attempt_at,
        last_synced_at=record.last_synced_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _cluster_exists(session: Session, cluster_id: str) -> bool:
    return session.get(Cluster, cluster_id) is not None


def _normalize_cluster_id(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip("-")
    return normalized[:64]


def _probe_cluster(session: Session, record: Cluster) -> tuple[str, str]:
    try:
        kube_service = kube_service_for_cluster(session=session, cluster_id=record.id, require_runtime_enabled=False)
        kube_service._client().list_namespace(limit=1)
        source = cluster_kubeconfig_source(record)
        if source == "local":
            return "healthy", "local cluster api reachable"
        return "healthy", f"remote cluster api reachable via {source}"
    except Exception as exc:
        if cluster_kubeconfig_source(record) == "none":
            return "unknown", "no kubeconfig configured"
        return "unhealthy", f"remote probe failed: {exc}"


def _validate_artifact_exists(session: Session, artifact_type: str, artifact_id: str) -> str:
    if artifact_type == "vm_image":
        row = session.get(Image, artifact_id)
    elif artifact_type == "vm_template":
        row = session.get(Template, artifact_id)
    elif artifact_type == "container_image":
        row = session.get(ContainerImage, artifact_id)
    elif artifact_type == "container_template":
        row = session.get(ContainerTemplate, artifact_id)
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported artifact_type")
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    return normalize_tenant(getattr(row, "tenant", None), default=GLOBAL_TENANT)


def _default_runtime_namespace() -> str:
    return str(cluster_runtime_namespace(None) or "labs").strip() or "labs"


def _count_active_cluster_workloads(session: Session, cluster_id: str) -> tuple[int, int]:
    vm_active = int(
        session.exec(
            select(func.count())
            .select_from(Instance)
            .where(Instance.cluster_id == cluster_id)
            .where(Instance.status.in_(["pending", "running"]))
        ).one()
        or 0
    )
    container_active = int(
        session.exec(
            select(func.count())
            .select_from(ContainerInstance)
            .where(ContainerInstance.cluster_id == cluster_id)
            .where(ContainerInstance.status.in_(["queued", "pending", "running"]))
        ).one()
        or 0
    )
    return vm_active, container_active


def _policies_referencing_cluster(session: Session, cluster_id: str) -> list[TeamPlacementPolicy]:
    refs: list[TeamPlacementPolicy] = []
    rows = session.exec(select(TeamPlacementPolicy)).all()
    for row in rows:
        preferred = _normalize_cluster_id(getattr(row, "preferred_cluster_id", ""))
        allowed = set(split_csv_tokens(getattr(row, "allowed_cluster_ids_csv", "")))
        if preferred == cluster_id or cluster_id in allowed:
            refs.append(row)
    return refs


def _validate_policy_cluster_ids(session: Session, cluster_ids: list[str], *, field: str) -> list[str]:
    normalized_ids: list[str] = []
    for raw_id in cluster_ids:
        normalized = _normalize_cluster_id(raw_id)
        if not normalized:
            continue
        if not _cluster_exists(session, normalized):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cluster not found: {raw_id}",
            )
        if normalized not in normalized_ids:
            normalized_ids.append(normalized)
    return normalized_ids


@router.get(
    "/settings/clusters",
    response_model=list[ClusterConfigOut],
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def list_clusters(
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[ClusterConfigOut]:
    _require_platform_admin(user)
    ensure_local_cluster(session)
    rows = session.exec(select(Cluster).order_by(Cluster.is_local.desc(), Cluster.id.asc())).all()
    return [_cluster_out(row) for row in rows]


@router.get(
    "/settings/clusters/telemetry",
    response_model=list[ClusterTelemetryOut],
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def cluster_telemetry(
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[ClusterTelemetryOut]:
    _require_platform_admin(user)
    ensure_local_cluster(session)
    clusters = session.exec(select(Cluster).order_by(Cluster.id.asc())).all()

    vm_rows = session.exec(
        select(Instance.cluster_id, func.count())
        .where(Instance.status.in_(["pending", "running"]))
        .group_by(Instance.cluster_id)
    ).all()
    vm_counts = {str(cluster_id or ""): int(count or 0) for cluster_id, count in vm_rows}

    container_rows = session.exec(
        select(ContainerInstance.cluster_id, func.count())
        .where(ContainerInstance.status.in_(["queued", "pending", "running"]))
        .group_by(ContainerInstance.cluster_id)
    ).all()
    container_counts = {str(cluster_id or ""): int(count or 0) for cluster_id, count in container_rows}

    replication_rows = session.exec(
        select(ArtifactReplication.target_cluster_id, ArtifactReplication.status, func.count()).group_by(
            ArtifactReplication.target_cluster_id, ArtifactReplication.status
        )
    ).all()
    replication_counts: dict[str, dict[str, int]] = {}
    for target_cluster_id, status_value, count in replication_rows:
        cluster_key = str(target_cluster_id or "")
        status_key = str(status_value or "").strip().lower()
        bucket = replication_counts.setdefault(cluster_key, {})
        bucket[status_key] = int(count or 0)

    telemetry: list[ClusterTelemetryOut] = []
    for cluster in clusters:
        ready, ready_message = runtime_client_probe(session, cluster.id)
        cluster_replication = replication_counts.get(cluster.id, {})
        telemetry.append(
            ClusterTelemetryOut(
                cluster_id=cluster.id,
                health_status=str(getattr(cluster, "health_status", "unknown") or "unknown"),
                enabled=bool(getattr(cluster, "enabled", False)),
                schedule_enabled=bool(getattr(cluster, "schedule_enabled", False)),
                runtime_enabled=bool(getattr(cluster, "runtime_enabled", False)),
                active_vm_instances=vm_counts.get(cluster.id, 0),
                active_container_instances=container_counts.get(cluster.id, 0),
                queued_replications=cluster_replication.get("queued", 0),
                syncing_replications=cluster_replication.get("syncing", 0),
                error_replications=cluster_replication.get("error", 0),
                runtime_client_ready=bool(ready),
                runtime_client_message=str(ready_message or ""),
            )
        )
    return telemetry


@router.post(
    "/settings/clusters",
    response_model=ClusterConfigOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def create_cluster(
    payload: ClusterConfigCreate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ClusterConfigOut:
    _require_platform_admin(user)
    ensure_local_cluster(session)
    cluster_id = _normalize_cluster_id(payload.id)
    if not cluster_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cluster id is required")
    if session.get(Cluster, cluster_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cluster already exists")
    kubeconfig_secret_name = str(payload.kubeconfig_secret_name or "").strip()
    kubeconfig_secret_namespace = str(payload.kubeconfig_secret_namespace or "").strip()
    kubeconfig_secret_key = str(payload.kubeconfig_secret_key or "").strip() or "kubeconfig"
    runtime_namespace = str(payload.runtime_namespace or "").strip() or _default_runtime_namespace()
    legacy_kubeconfig = str(payload.kubeconfig or "").strip()
    record = Cluster(
        id=cluster_id,
        name=str(payload.name or "").strip(),
        region=str(payload.region or "local").strip().lower() or "local",
        compliance_tags_csv=csv_tokens(payload.compliance_tags),
        capacity_weight=max(1, int(payload.capacity_weight or 100)),
        enabled=bool(payload.enabled),
        schedule_enabled=bool(payload.schedule_enabled),
        runtime_enabled=bool(payload.runtime_enabled),
        is_local=(cluster_id == local_cluster_id()),
        runtime_namespace=runtime_namespace,
        kubeconfig_secret_name=kubeconfig_secret_name,
        kubeconfig_secret_namespace=kubeconfig_secret_namespace,
        kubeconfig_secret_key=kubeconfig_secret_key,
        kubeconfig=(encrypt_secret(legacy_kubeconfig) if legacy_kubeconfig else ""),
        notes=str(payload.notes or "").strip(),
        health_status="unknown",
        health_message="",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    if record.is_local:
        record.enabled = True
        record.schedule_enabled = True
        record.runtime_enabled = True
        record.runtime_namespace = _default_runtime_namespace()
    elif record.runtime_enabled and not cluster_has_kubeconfig(record):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="remote runtime-enabled clusters must configure kubeconfig secret reference or kubeconfig payload",
        )
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="create",
        target_type="cluster",
        target_id=record.id,
        detail=f"region={record.region} schedule_enabled={record.schedule_enabled} runtime_enabled={record.runtime_enabled}",
    )
    session.commit()
    session.refresh(record)
    return _cluster_out(record)


@router.patch(
    "/settings/clusters/{cluster_id}",
    response_model=ClusterConfigOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_cluster(
    cluster_id: str,
    payload: ClusterConfigUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ClusterConfigOut:
    _require_platform_admin(user)
    ensure_local_cluster(session)
    normalized_id = _normalize_cluster_id(cluster_id)
    record = session.get(Cluster, normalized_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cluster not found")
    if payload.name is not None:
        record.name = str(payload.name).strip()
    if payload.region is not None:
        record.region = str(payload.region).strip().lower() or record.region
    if payload.compliance_tags is not None:
        record.compliance_tags_csv = csv_tokens(payload.compliance_tags)
    if payload.capacity_weight is not None:
        record.capacity_weight = max(1, int(payload.capacity_weight))
    if payload.enabled is not None:
        record.enabled = bool(payload.enabled)
    if payload.schedule_enabled is not None:
        record.schedule_enabled = bool(payload.schedule_enabled)
    if payload.runtime_enabled is not None:
        record.runtime_enabled = bool(payload.runtime_enabled)
    if payload.runtime_namespace is not None:
        record.runtime_namespace = str(payload.runtime_namespace or "").strip()
    if payload.kubeconfig_secret_name is not None:
        record.kubeconfig_secret_name = str(payload.kubeconfig_secret_name or "").strip()
        if not record.kubeconfig_secret_name:
            record.kubeconfig_secret_namespace = ""
            record.kubeconfig_secret_key = "kubeconfig"
    if payload.kubeconfig_secret_namespace is not None:
        record.kubeconfig_secret_namespace = str(payload.kubeconfig_secret_namespace or "").strip()
    if payload.kubeconfig_secret_key is not None:
        record.kubeconfig_secret_key = str(payload.kubeconfig_secret_key or "").strip() or "kubeconfig"
    if payload.notes is not None:
        record.notes = str(payload.notes or "").strip()
    if payload.kubeconfig is not None:
        kubeconfig_text = str(payload.kubeconfig or "").strip()
        record.kubeconfig = encrypt_secret(kubeconfig_text) if kubeconfig_text else ""
    if bool(record.is_local):
        record.enabled = True
        record.schedule_enabled = True
        record.runtime_enabled = True
        record.runtime_namespace = _default_runtime_namespace()
    elif record.runtime_enabled and not cluster_has_kubeconfig(record):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="remote runtime-enabled clusters must configure kubeconfig secret reference or kubeconfig payload",
        )
    if not str(record.runtime_namespace or "").strip():
        record.runtime_namespace = _default_runtime_namespace()
    record.updated_at = utc_now()
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="update",
        target_type="cluster",
        target_id=record.id,
        detail="cluster settings updated",
    )
    session.commit()
    session.refresh(record)
    return _cluster_out(record)


@router.post(
    "/settings/clusters/{cluster_id}/probe",
    response_model=ClusterConfigOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def probe_cluster(
    cluster_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ClusterConfigOut:
    _require_platform_admin(user)
    ensure_local_cluster(session)
    normalized_id = _normalize_cluster_id(cluster_id)
    record = session.get(Cluster, normalized_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cluster not found")
    health_status, health_message = _probe_cluster(session, record)
    record.health_status = health_status
    record.health_message = health_message
    record.last_heartbeat_at = utc_now()
    record.updated_at = utc_now()
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="probe",
        target_type="cluster",
        target_id=record.id,
        detail=f"health_status={health_status}",
    )
    session.commit()
    session.refresh(record)
    return _cluster_out(record)


@router.delete(
    "/settings/clusters/{cluster_id}",
    response_model=ClusterConfigOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def disable_cluster(
    cluster_id: str,
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ClusterConfigOut:
    _require_platform_admin(user)
    normalized_id = _normalize_cluster_id(cluster_id)
    record = session.get(Cluster, normalized_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="cluster not found")
    if bool(record.is_local):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="local cluster cannot be disabled")
    vm_active, container_active = _count_active_cluster_workloads(session, normalized_id)
    if (vm_active or container_active) and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"cluster has active workloads (vm={vm_active}, container={container_active}); "
                "set force=true to disable anyway"
            ),
        )
    policy_refs = _policies_referencing_cluster(session, normalized_id)
    if policy_refs and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"cluster is referenced by {len(policy_refs)} placement policies; "
                "set force=true to disable and automatically sanitize policy references"
            ),
        )
    if force and policy_refs:
        for policy in policy_refs:
            if _normalize_cluster_id(getattr(policy, "preferred_cluster_id", "")) == normalized_id:
                policy.preferred_cluster_id = None
                policy.hard_pin_cluster = False
            allowed = split_csv_tokens(getattr(policy, "allowed_cluster_ids_csv", ""))
            policy.allowed_cluster_ids_csv = csv_tokens([cid for cid in allowed if cid != normalized_id])
            policy.updated_at = utc_now()
            session.add(policy)
    record.enabled = False
    record.schedule_enabled = False
    record.runtime_enabled = False
    record.updated_at = utc_now()
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="disable",
        target_type="cluster",
        target_id=record.id,
        detail=f"cluster disabled force={force} active_vm={vm_active} active_container={container_active}",
    )
    session.commit()
    session.refresh(record)
    return _cluster_out(record)


@router.get(
    "/settings/placement-policies",
    response_model=list[TeamPlacementPolicyOut],
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def list_team_placement_policies(
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[TeamPlacementPolicyOut]:
    _require_platform_admin(user)
    rows = session.exec(select(TeamPlacementPolicy).order_by(TeamPlacementPolicy.team.asc())).all()
    return [_policy_out(row) for row in rows]


@router.get(
    "/settings/placement-policies/explain",
    response_model=PlacementExplainOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def explain_team_placement_policy(
    team: str | None = Query(default="default"),
    workload_kind: str = Query(default="vm"),
    template_cluster_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> PlacementExplainOut:
    _require_platform_admin(user)
    explanation = explain_cluster_selection(
        session,
        team=team,
        workload_kind=workload_kind,
        template_cluster_id=template_cluster_id,
    )
    return PlacementExplainOut(
        team=explanation.team,
        workload_kind=explanation.workload_kind,
        template_cluster_id=explanation.template_cluster_id,
        selected_cluster_id=explanation.selected_cluster_id,
        selected_reason=explanation.selected_reason,
        error=explanation.error,
        candidates=[
            PlacementCandidateOut(cluster_id=row.cluster_id, allowed=row.allowed, reasons=row.reasons)
            for row in explanation.candidates
        ],
    )


@router.put(
    "/settings/placement-policies/{team}",
    response_model=TeamPlacementPolicyOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def upsert_team_placement_policy(
    team: str,
    payload: TeamPlacementPolicyUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> TeamPlacementPolicyOut:
    _require_platform_admin(user)
    normalized_team = str(team or "").strip().lower()
    if not normalized_team:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="team is required")
    preferred_cluster = _normalize_cluster_id(payload.preferred_cluster_id)
    if preferred_cluster and not _cluster_exists(session, preferred_cluster):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="preferred_cluster_id not found")
    allowed_cluster_ids = _validate_policy_cluster_ids(
        session, [str(item or "") for item in payload.allowed_cluster_ids], field="allowed_cluster_ids"
    )
    if bool(payload.hard_pin_cluster) and not preferred_cluster:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="hard_pin_cluster requires preferred_cluster_id",
        )
    if preferred_cluster and allowed_cluster_ids and preferred_cluster not in set(allowed_cluster_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preferred_cluster_id must be included in allowed_cluster_ids when allowlist is set",
        )

    row = session.exec(select(TeamPlacementPolicy).where(TeamPlacementPolicy.team == normalized_team)).first()
    now = utc_now()
    if not row:
        row = TeamPlacementPolicy(id=str(uuid4()), team=normalized_team, created_at=now, updated_at=now)
    row.preferred_cluster_id = preferred_cluster or None
    row.hard_pin_cluster = bool(payload.hard_pin_cluster)
    row.required_regions_csv = csv_tokens(payload.required_regions)
    row.required_compliance_tags_csv = csv_tokens(payload.required_compliance_tags)
    row.allowed_cluster_ids_csv = csv_tokens(allowed_cluster_ids)
    row.updated_at = now
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="upsert",
        target_type="team_placement_policy",
        target_id=normalized_team,
        detail=f"preferred_cluster_id={row.preferred_cluster_id or ''} hard_pin_cluster={row.hard_pin_cluster}",
    )
    session.commit()
    session.refresh(row)
    return _policy_out(row)


@router.delete(
    "/settings/placement-policies/{team}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def delete_team_placement_policy(
    team: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> None:
    _require_platform_admin(user)
    normalized_team = str(team or "").strip().lower()
    row = session.exec(select(TeamPlacementPolicy).where(TeamPlacementPolicy.team == normalized_team)).first()
    if not row:
        return
    session.delete(row)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="delete",
        target_type="team_placement_policy",
        target_id=normalized_team,
        detail="deleted",
    )
    session.commit()


@router.get(
    "/replication/artifacts",
    response_model=list[ArtifactReplicationOut],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_artifact_replications(
    artifact_type: str | None = Query(default=None),
    target_cluster_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[ArtifactReplicationOut]:
    stmt = select(ArtifactReplication)
    if artifact_type:
        stmt = stmt.where(ArtifactReplication.artifact_type == str(artifact_type).strip())
    if target_cluster_id:
        stmt = stmt.where(ArtifactReplication.target_cluster_id == _normalize_cluster_id(target_cluster_id))
    if status_filter:
        stmt = stmt.where(ArtifactReplication.status == str(status_filter).strip().lower())
    if not is_platform_admin(user):
        stmt = stmt.where(ArtifactReplication.tenant == actor_tenant(user))
    rows = session.exec(stmt.order_by(ArtifactReplication.updated_at.desc())).all()[:limit]
    return [_replication_out(row) for row in rows]


@router.post(
    "/replication/artifacts",
    response_model=list[ArtifactReplicationOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def enqueue_artifact_replication(
    payload: ArtifactReplicationCreate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[ArtifactReplicationOut]:
    ensure_local_cluster(session)
    source_cluster = _normalize_cluster_id(payload.source_cluster_id) or local_cluster_id()
    if not _cluster_exists(session, source_cluster):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="source_cluster_id not found")

    artifact_tenant = _validate_artifact_exists(session, payload.artifact_type, payload.artifact_id)
    requested_tenant = normalize_tenant(payload.tenant, default=artifact_tenant)
    if not is_platform_admin(user):
        actor_scope = actor_tenant(user)
        if requested_tenant != actor_scope:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant scope violation")

    target_ids = []
    for cluster_id in payload.target_cluster_ids:
        normalized = _normalize_cluster_id(cluster_id)
        if not normalized:
            continue
        if not _cluster_exists(session, normalized):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"target cluster not found: {cluster_id}",
            )
        if normalized == source_cluster:
            continue
        if normalized not in target_ids:
            target_ids.append(normalized)
    if not target_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no valid target_cluster_ids")

    created: list[ArtifactReplication] = []
    now = utc_now()
    for target_cluster in target_ids:
        row = session.exec(
            select(ArtifactReplication)
            .where(ArtifactReplication.artifact_type == payload.artifact_type)
            .where(ArtifactReplication.artifact_id == payload.artifact_id)
            .where(ArtifactReplication.target_cluster_id == target_cluster)
        ).first()
        if not row:
            row = ArtifactReplication(
                id=str(uuid4()),
                tenant=requested_tenant,
                artifact_type=payload.artifact_type,
                artifact_id=payload.artifact_id,
                source_cluster_id=source_cluster,
                target_cluster_id=target_cluster,
                status="queued",
                detail="Queued for replication.",
                requested_by=user.username,
                created_at=now,
                updated_at=now,
            )
        else:
            row.tenant = requested_tenant
            row.source_cluster_id = source_cluster
            row.status = "queued"
            row.detail = "Re-queued for replication."
            row.requested_by = user.username
            row.updated_at = now
        session.add(row)
        created.append(row)

    _record_admin_audit_event(
        session,
        actor=user.username,
        action="enqueue",
        target_type="artifact_replication",
        target_id=f"{payload.artifact_type}:{payload.artifact_id}",
        detail=f"source_cluster={source_cluster} targets={','.join(target_ids)}",
        tenant=requested_tenant,
    )
    session.commit()
    for row in created:
        session.refresh(row)
    return [_replication_out(row) for row in created]


@router.post(
    "/replication/artifacts/process",
    response_model=dict[str, int],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def process_replication_queue(
    limit: int = Query(default=25, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> dict[str, int]:
    _require_platform_admin(user)
    processed = process_artifact_replication_queue(session, limit=limit)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="process",
        target_type="artifact_replication",
        target_id="queue",
        detail=f"processed={processed}",
    )
    session.commit()
    return {"processed": int(processed)}


@router.patch(
    "/replication/artifacts/{replication_id}",
    response_model=ArtifactReplicationOut,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def update_artifact_replication(
    replication_id: str,
    payload: ArtifactReplicationUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ArtifactReplicationOut:
    row = session.get(ArtifactReplication, replication_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="replication record not found")
    if not is_platform_admin(user):
        if normalize_tenant(getattr(row, "tenant", None), default=GLOBAL_TENANT) != actor_tenant(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant scope violation")
    row.status = payload.status
    row.detail = str(payload.detail or "").strip()
    row.last_attempt_at = utc_now()
    if row.status == "ready":
        row.last_synced_at = utc_now()
    row.updated_at = utc_now()
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=user.username,
        action="update",
        target_type="artifact_replication",
        target_id=row.id,
        detail=f"status={row.status}",
        tenant=normalize_tenant(getattr(row, "tenant", None), default=GLOBAL_TENANT),
    )
    session.commit()
    session.refresh(row)
    return _replication_out(row)
