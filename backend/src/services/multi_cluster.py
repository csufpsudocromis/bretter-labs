from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from uuid import uuid4

import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..config import settings
from ..secret_codec import decrypt_secret, secret_is_configured
from ..tables import Cluster, TeamPlacementPolicy
from ..time_utils import utc_now
from .kubernetes import KubernetesService, kube

_TOKEN_RE = re.compile(r"[^a-z0-9._-]+")


class PlacementError(RuntimeError):
    pass


@dataclass
class PlacementDecision:
    cluster_id: str
    reason: str


@dataclass
class PlacementCandidate:
    cluster_id: str
    allowed: bool
    reasons: list[str]


@dataclass
class PlacementExplanation:
    team: str
    workload_kind: str
    template_cluster_id: str | None
    selected_cluster_id: str | None
    selected_reason: str | None
    error: str | None
    candidates: list[PlacementCandidate]


def _normalize_token(value: str | None, *, default: str = "") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    normalized = _TOKEN_RE.sub("-", raw).strip("-")
    return normalized or default


def split_csv_tokens(raw: str | None) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in str(raw or "").split(","):
        token = _normalize_token(item)
        if not token or token in seen:
            continue
        seen.add(token)
        values.append(token)
    return values


def csv_tokens(values: list[str] | tuple[str, ...] | set[str] | None) -> str:
    normalized = split_csv_tokens(",".join(values or []))
    return ",".join(normalized)


def local_cluster_id() -> str:
    configured = str(getattr(settings, "multi_cluster_local_cluster_id", "") or "").strip()
    return _normalize_token(configured, default="local")


def local_cluster_region() -> str:
    configured = str(getattr(settings, "multi_cluster_local_region", "") or "").strip()
    return _normalize_token(configured, default="local")


def _default_runtime_namespace() -> str:
    return str(getattr(settings, "kube_namespace", "labs") or "labs").strip() or "labs"


def cluster_runtime_namespace(cluster: Cluster | None) -> str:
    if not cluster:
        return _default_runtime_namespace()
    configured = str(getattr(cluster, "runtime_namespace", "") or "").strip()
    return configured or _default_runtime_namespace()


def cluster_kubeconfig_secret_ref(cluster: Cluster | None) -> tuple[str, str, str]:
    if not cluster:
        return "", "", _default_runtime_namespace()
    name = str(getattr(cluster, "kubeconfig_secret_name", "") or "").strip()
    key = str(getattr(cluster, "kubeconfig_secret_key", "") or "").strip() or "kubeconfig"
    namespace = str(getattr(cluster, "kubeconfig_secret_namespace", "") or "").strip() or _default_runtime_namespace()
    return name, key, namespace


def cluster_has_kubeconfig(cluster: Cluster | None) -> bool:
    if not cluster:
        return False
    if bool(getattr(cluster, "is_local", False)):
        return True
    secret_name, _, _ = cluster_kubeconfig_secret_ref(cluster)
    if secret_name:
        return True
    return secret_is_configured(str(getattr(cluster, "kubeconfig", "") or "").strip())


def cluster_kubeconfig_source(cluster: Cluster | None) -> str:
    if not cluster:
        return "none"
    if bool(getattr(cluster, "is_local", False)):
        return "local"
    secret_name, _, _ = cluster_kubeconfig_secret_ref(cluster)
    if secret_name:
        return "secret_ref"
    if secret_is_configured(str(getattr(cluster, "kubeconfig", "") or "").strip()):
        return "db_encrypted"
    return "none"


def ensure_local_cluster(session: Session) -> Cluster:
    cluster_id = local_cluster_id()
    record = session.get(Cluster, cluster_id)
    if record:
        changed = False
        if not record.is_local:
            record.is_local = True
            changed = True
        if not record.runtime_enabled:
            record.runtime_enabled = True
            changed = True
        if not record.enabled:
            record.enabled = True
            changed = True
        if not record.schedule_enabled:
            record.schedule_enabled = True
            changed = True
        if not str(record.region or "").strip():
            record.region = local_cluster_region()
            changed = True
        if not str(getattr(record, "runtime_namespace", "") or "").strip():
            record.runtime_namespace = _default_runtime_namespace()
            changed = True
        if not str(getattr(record, "kubeconfig_secret_key", "") or "").strip():
            record.kubeconfig_secret_key = "kubeconfig"
            changed = True
        if changed:
            record.updated_at = utc_now()
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    now = utc_now()
    record = Cluster(
        id=cluster_id,
        name="Local Cluster",
        region=local_cluster_region(),
        compliance_tags_csv="",
        capacity_weight=100,
        enabled=True,
        schedule_enabled=True,
        runtime_enabled=True,
        is_local=True,
        runtime_namespace=_default_runtime_namespace(),
        kubeconfig_secret_name="",
        kubeconfig_secret_namespace="",
        kubeconfig_secret_key="kubeconfig",
        kubeconfig="",
        notes="Auto-managed local runtime cluster.",
        health_status="unknown",
        health_message="",
        created_at=now,
        updated_at=now,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _load_kubeconfig_from_secret(cluster: Cluster) -> str:
    secret_name, secret_key, secret_namespace = cluster_kubeconfig_secret_ref(cluster)
    if not secret_name:
        return ""
    try:
        secret = kube._client().read_namespaced_secret(name=secret_name, namespace=secret_namespace)
    except ApiException as exc:
        raise PlacementError(
            f"cluster '{cluster.id}' kubeconfig secret lookup failed ({secret_namespace}/{secret_name}): {exc.reason}"
        ) from exc
    data = secret.data or {}
    encoded = str(data.get(secret_key) or "").strip()
    if not encoded:
        raise PlacementError(
            f"cluster '{cluster.id}' kubeconfig secret key missing ({secret_namespace}/{secret_name}:{secret_key})"
        )
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise PlacementError(
            f"cluster '{cluster.id}' kubeconfig secret is not valid base64 utf-8 ({secret_namespace}/{secret_name}:{secret_key})"
        ) from exc
    return decoded.strip()


def resolve_cluster_kubeconfig_text(cluster: Cluster) -> str:
    if bool(getattr(cluster, "is_local", False)):
        return ""
    raw = _load_kubeconfig_from_secret(cluster)
    if raw:
        return raw
    kubeconfig_raw = str(getattr(cluster, "kubeconfig", "") or "").strip()
    if not kubeconfig_raw:
        return ""
    if secret_is_configured(kubeconfig_raw):
        return str(decrypt_secret(kubeconfig_raw) or "").strip()
    return kubeconfig_raw


def resolve_cluster_kubeconfig_dict(cluster: Cluster) -> dict | None:
    kubeconfig_text = resolve_cluster_kubeconfig_text(cluster)
    if not kubeconfig_text:
        return None
    try:
        parsed = yaml.safe_load(kubeconfig_text)
    except yaml.YAMLError as exc:
        raise PlacementError(f"cluster '{cluster.id}' kubeconfig is invalid yaml") from exc
    if not isinstance(parsed, dict):
        raise PlacementError(f"cluster '{cluster.id}' kubeconfig must parse to an object")
    return parsed


def kube_service_for_cluster(
    session: Session,
    cluster_id: str | None,
    *,
    require_runtime_enabled: bool = True,
) -> KubernetesService:
    ensure_local_cluster(session)
    normalized_cluster_id = _normalize_token(cluster_id, default=local_cluster_id())
    cluster = session.get(Cluster, normalized_cluster_id)
    if not cluster:
        raise PlacementError(f"cluster '{normalized_cluster_id}' not found")
    if require_runtime_enabled:
        if not bool(getattr(cluster, "enabled", False)):
            raise PlacementError(f"cluster '{cluster.id}' is disabled")
        if not bool(getattr(cluster, "runtime_enabled", False)):
            raise PlacementError(f"cluster '{cluster.id}' runtime is disabled")

    if bool(getattr(cluster, "is_local", False)) or _normalize_token(cluster.id) == local_cluster_id():
        return kube

    kubeconfig_data = resolve_cluster_kubeconfig_dict(cluster)
    if not kubeconfig_data:
        raise PlacementError(
            f"cluster '{cluster.id}' has no runtime client credentials configured (set kubeconfig secret reference)."
        )
    try:
        api_client = k8s_config.new_client_from_config_dict(config_dict=kubeconfig_data)
    except Exception as exc:
        raise PlacementError(f"cluster '{cluster.id}' kubeconfig client initialization failed: {exc}") from exc

    return KubernetesService(
        core_api=k8s_client.CoreV1Api(api_client),
        networking_api=k8s_client.NetworkingV1Api(api_client),
        namespace_override=cluster_runtime_namespace(cluster),
    )


def runtime_client_probe(session: Session, cluster_id: str | None) -> tuple[bool, str]:
    try:
        kube_service = kube_service_for_cluster(session, cluster_id, require_runtime_enabled=False)
        kube_service._client().list_namespace(limit=1)
    except Exception as exc:
        return False, str(exc)
    return True, "runtime client reachable"


def list_clusters_for_scheduling(session: Session) -> list[Cluster]:
    ensure_local_cluster(session)
    rows = session.exec(
        select(Cluster)
        .where(Cluster.enabled == True)  # noqa: E712
        .where(Cluster.schedule_enabled == True)  # noqa: E712
        .where(Cluster.runtime_enabled == True)  # noqa: E712
    ).all()
    if rows:
        return rows
    local = session.get(Cluster, local_cluster_id())
    return [local] if local else []


def _team_policy(session: Session, team: str | None) -> TeamPlacementPolicy | None:
    normalized_team = _normalize_token(team, default="default")
    return session.exec(select(TeamPlacementPolicy).where(TeamPlacementPolicy.team == normalized_team)).first()


def _cluster_compliance_tags(cluster: Cluster) -> set[str]:
    return set(split_csv_tokens(getattr(cluster, "compliance_tags_csv", "")))


def _policy_rejection_reasons(cluster: Cluster, policy: TeamPlacementPolicy | None) -> list[str]:
    if not policy:
        return []
    reasons: list[str] = []
    required_regions = set(split_csv_tokens(getattr(policy, "required_regions_csv", "")))
    required_tags = set(split_csv_tokens(getattr(policy, "required_compliance_tags_csv", "")))
    allowed_cluster_ids = set(split_csv_tokens(getattr(policy, "allowed_cluster_ids_csv", "")))
    if required_regions and _normalize_token(cluster.region) not in required_regions:
        reasons.append("region mismatch")
    if required_tags and not required_tags.issubset(_cluster_compliance_tags(cluster)):
        reasons.append("missing required compliance tags")
    if allowed_cluster_ids and _normalize_token(cluster.id) not in allowed_cluster_ids:
        reasons.append("cluster not in tenant allowlist")
    return reasons


def _policy_allows_cluster(cluster: Cluster, policy: TeamPlacementPolicy | None) -> bool:
    return not _policy_rejection_reasons(cluster, policy)


def _cluster_sort_key(cluster: Cluster) -> tuple[int, int, str]:
    weight = max(1, int(getattr(cluster, "capacity_weight", 100) or 100))
    local_bias = 1 if bool(getattr(cluster, "is_local", False)) else 0
    return (weight, local_bias, str(cluster.id or ""))


def select_cluster_for_launch(
    session: Session,
    *,
    team: str | None,
    workload_kind: str,
    template_cluster_id: str | None = None,
) -> PlacementDecision:
    del workload_kind  # Reserved for future workload-kind-specific policies.
    candidates = list_clusters_for_scheduling(session)
    if not candidates:
        raise PlacementError("No runtime-enabled clusters are available for scheduling.")

    policy = _team_policy(session, team)
    filtered = [cluster for cluster in candidates if _policy_allows_cluster(cluster, policy)]
    if not filtered:
        raise PlacementError("No cluster satisfies tenant placement policy constraints.")
    filtered = [
        cluster for cluster in filtered if bool(getattr(cluster, "is_local", False)) or cluster_has_kubeconfig(cluster)
    ]
    if not filtered:
        raise PlacementError("No schedulable cluster has runtime kubeconfig configured.")

    template_cluster = _normalize_token(template_cluster_id)
    if template_cluster:
        narrowed = [cluster for cluster in filtered if _normalize_token(cluster.id) == template_cluster]
        if not narrowed:
            raise PlacementError(
                f"Template requires cluster '{template_cluster}', but it is not currently schedulable for this tenant."
            )
        filtered = narrowed
        return PlacementDecision(cluster_id=filtered[0].id, reason="template-cluster-pin")

    preferred_cluster = _normalize_token(getattr(policy, "preferred_cluster_id", "")) if policy else ""
    hard_pin = bool(getattr(policy, "hard_pin_cluster", False)) if policy else False
    if preferred_cluster:
        preferred = [cluster for cluster in filtered if _normalize_token(cluster.id) == preferred_cluster]
        if preferred:
            return PlacementDecision(cluster_id=preferred[0].id, reason="tenant-preferred-cluster")
        if hard_pin:
            raise PlacementError(
                f"Tenant policy hard-pins cluster '{preferred_cluster}', but it is not currently schedulable."
            )

    chosen = sorted(filtered, key=_cluster_sort_key, reverse=True)[0]
    return PlacementDecision(cluster_id=chosen.id, reason="capacity-weighted")


def explain_cluster_selection(
    session: Session,
    *,
    team: str | None,
    workload_kind: str,
    template_cluster_id: str | None = None,
) -> PlacementExplanation:
    ensure_local_cluster(session)
    normalized_team = _normalize_token(team, default="default")
    normalized_workload = _normalize_token(workload_kind, default="vm") or "vm"
    normalized_template_cluster = _normalize_token(template_cluster_id) or None
    policy = _team_policy(session, normalized_team)
    preferred_cluster = _normalize_token(getattr(policy, "preferred_cluster_id", "")) if policy else ""
    hard_pin = bool(getattr(policy, "hard_pin_cluster", False)) if policy else False

    rows = session.exec(select(Cluster).order_by(Cluster.id.asc())).all()
    candidates: list[PlacementCandidate] = []
    for cluster in rows:
        reasons: list[str] = []
        if not bool(getattr(cluster, "enabled", False)):
            reasons.append("cluster disabled")
        if not bool(getattr(cluster, "schedule_enabled", False)):
            reasons.append("schedule disabled")
        if not bool(getattr(cluster, "runtime_enabled", False)):
            reasons.append("runtime disabled")
        reasons.extend(_policy_rejection_reasons(cluster, policy))
        if normalized_template_cluster and _normalize_token(cluster.id) != normalized_template_cluster:
            reasons.append("template pins a different cluster")
        if hard_pin and preferred_cluster and _normalize_token(cluster.id) != preferred_cluster:
            reasons.append("tenant hard-pins a different cluster")
        if cluster_kubeconfig_source(cluster) == "none" and not bool(getattr(cluster, "is_local", False)):
            reasons.append("runtime kubeconfig not configured")
        candidates.append(PlacementCandidate(cluster_id=cluster.id, allowed=(len(reasons) == 0), reasons=reasons))

    selected_cluster_id: str | None = None
    selected_reason: str | None = None
    error: str | None = None
    try:
        decision = select_cluster_for_launch(
            session,
            team=normalized_team,
            workload_kind=normalized_workload,
            template_cluster_id=normalized_template_cluster,
        )
        selected_cluster_id = decision.cluster_id
        selected_reason = decision.reason
    except PlacementError as exc:
        error = str(exc)

    return PlacementExplanation(
        team=normalized_team,
        workload_kind=normalized_workload,
        template_cluster_id=normalized_template_cluster,
        selected_cluster_id=selected_cluster_id,
        selected_reason=selected_reason,
        error=error,
        candidates=candidates,
    )


def _replicate_vm_image(session: Session, source_id: str, target_cluster_id: str):
    from ..tables import Image

    source = session.get(Image, source_id)
    if not source:
        raise PlacementError(f"vm_image source artifact not found: {source_id}")
    existing = session.exec(
        select(Image)
        .where(Image.cluster_id == target_cluster_id)
        .where(Image.tenant == source.tenant)
        .where(Image.name == source.name)
    ).first()
    if existing:
        return existing, "existing replica found"
    replica = Image(
        id=str(uuid4()),
        name=source.name,
        filename=source.filename,
        tenant=source.tenant,
        cluster_id=target_cluster_id,
        source_pvc=source.source_pvc,
        checksum=source.checksum,
        size_bytes=int(source.size_bytes or 0),
        created_at=utc_now(),
    )
    session.add(replica)
    session.flush()
    return replica, "created vm_image metadata replica"


def _replicate_vm_template(session: Session, source_id: str, target_cluster_id: str):
    from ..tables import Template

    source = session.get(Template, source_id)
    if not source:
        raise PlacementError(f"vm_template source artifact not found: {source_id}")
    image_replica, _ = _replicate_vm_image(session, source.image_id, target_cluster_id)
    existing = session.exec(
        select(Template)
        .where(Template.cluster_id == target_cluster_id)
        .where(Template.tenant == source.tenant)
        .where(Template.name == source.name)
    ).first()
    if existing:
        existing.image_id = image_replica.id
        session.add(existing)
        session.flush()
        return existing, "updated existing vm_template replica"
    replica = Template(
        id=str(uuid4()),
        name=source.name,
        tenant=source.tenant,
        cluster_id=target_cluster_id,
        description=source.description,
        os_type=source.os_type,
        image_id=image_replica.id,
        cpu_cores=int(source.cpu_cores or 1),
        ram_mb=int(source.ram_mb or 512),
        auto_delete_minutes=int(source.auto_delete_minutes or 30),
        idle_timeout_minutes=int(source.idle_timeout_minutes or 30),
        preclone_pool_size=int(source.preclone_pool_size or 0),
        preclone_pool_max=int(source.preclone_pool_max or 0),
        max_active_instances=int(source.max_active_instances or 2),
        enabled=bool(source.enabled),
        network_mode=source.network_mode,
        console_provider=source.console_provider,
        rdp_default_username=source.rdp_default_username,
        rdp_default_password=source.rdp_default_password,
        created_at=utc_now(),
    )
    session.add(replica)
    session.flush()
    return replica, "created vm_template metadata replica"


def _replicate_container_image(session: Session, source_id: str, target_cluster_id: str):
    from ..tables import ContainerImage

    source = session.get(ContainerImage, source_id)
    if not source:
        raise PlacementError(f"container_image source artifact not found: {source_id}")
    existing = session.exec(
        select(ContainerImage)
        .where(ContainerImage.cluster_id == target_cluster_id)
        .where(ContainerImage.tenant == source.tenant)
        .where(ContainerImage.image_ref == source.image_ref)
    ).first()
    if existing:
        return existing, "existing replica found"
    replica = ContainerImage(
        id=str(uuid4()),
        name=source.name,
        image_ref=source.image_ref,
        tenant=source.tenant,
        cluster_id=target_cluster_id,
        last_scan_at=source.last_scan_at,
        last_scan_status=source.last_scan_status,
        last_scan_summary=source.last_scan_summary,
        created_at=utc_now(),
    )
    session.add(replica)
    session.flush()
    return replica, "created container_image metadata replica"


def _replicate_container_template(session: Session, source_id: str, target_cluster_id: str):
    from ..tables import ContainerTemplate

    source = session.get(ContainerTemplate, source_id)
    if not source:
        raise PlacementError(f"container_template source artifact not found: {source_id}")
    image_replica, _ = _replicate_container_image(session, source.container_image_id, target_cluster_id)
    existing = session.exec(
        select(ContainerTemplate)
        .where(ContainerTemplate.cluster_id == target_cluster_id)
        .where(ContainerTemplate.tenant == source.tenant)
        .where(ContainerTemplate.name == source.name)
    ).first()
    if existing:
        existing.container_image_id = image_replica.id
        session.add(existing)
        session.flush()
        return existing, "updated existing container_template replica"
    replica = ContainerTemplate(
        id=str(uuid4()),
        template_key=f"{source.template_key}-{target_cluster_id}",
        version=1,
        is_default=True,
        name=source.name,
        tenant=source.tenant,
        cluster_id=target_cluster_id,
        description=source.description,
        container_image_id=image_replica.id,
        cpu_millicores=int(source.cpu_millicores or 500),
        memory_mb=int(source.memory_mb or 512),
        container_port=int(source.container_port or 80),
        healthcheck_protocol=source.healthcheck_protocol,
        healthcheck_path=source.healthcheck_path,
        readiness_http_status=int(source.readiness_http_status or 200),
        readiness_success_path=source.readiness_success_path,
        startup_timeout_seconds=int(source.startup_timeout_seconds or 300),
        dependency_checks_json=source.dependency_checks_json,
        expose_strategy=source.expose_strategy,
        network_mode=source.network_mode,
        run_as_non_root=bool(source.run_as_non_root),
        read_only_root_filesystem=bool(source.read_only_root_filesystem),
        command=source.command,
        args_json=source.args_json,
        env_json=source.env_json,
        auto_delete_minutes=int(source.auto_delete_minutes or 60),
        idle_timeout_minutes=int(source.idle_timeout_minutes or 30),
        max_active_instances=int(source.max_active_instances or 2),
        enabled=bool(source.enabled),
        created_at=utc_now(),
    )
    session.add(replica)
    session.flush()
    return replica, "created container_template metadata replica"


def process_artifact_replication_queue(session: Session, *, limit: int = 20) -> int:
    from ..tables import ArtifactReplication

    max_items = max(1, min(200, int(limit or 20)))
    rows = session.exec(
        select(ArtifactReplication)
        .where(ArtifactReplication.status == "queued")
        .order_by(ArtifactReplication.updated_at.asc())
    ).all()[:max_items]
    processed = 0
    for row in rows:
        row.status = "syncing"
        row.last_attempt_at = utc_now()
        row.updated_at = utc_now()
        session.add(row)
        session.commit()
        try:
            if row.artifact_type == "vm_image":
                _replicate_vm_image(session, row.artifact_id, row.target_cluster_id)
            elif row.artifact_type == "vm_template":
                _replicate_vm_template(session, row.artifact_id, row.target_cluster_id)
            elif row.artifact_type == "container_image":
                _replicate_container_image(session, row.artifact_id, row.target_cluster_id)
            elif row.artifact_type == "container_template":
                _replicate_container_template(session, row.artifact_id, row.target_cluster_id)
            else:
                raise PlacementError(f"unsupported artifact_type: {row.artifact_type}")

            row.status = "ready"
            row.detail = "Replication completed."
            row.last_synced_at = utc_now()
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            processed += 1
        except Exception as exc:
            row.status = "error"
            row.detail = str(exc)[:2000]
            row.updated_at = utc_now()
            session.add(row)
            session.commit()
            processed += 1
    return processed
