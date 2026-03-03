import json
import logging
import socket
from http.client import HTTPConnection
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..auth import require_user
from ..config import settings
from ..db import get_session
from ..models import ContainerInstance as ContainerInstanceView
from ..models import ContainerDependencyCheck
from ..models import ContainerTemplate as ContainerTemplateView
from ..services.kubernetes import ContainerPodRequest, PodStatus, kube
from ..tables import Config
from ..tables import ContainerImage as ContainerImageTable
from ..tables import ContainerInstance as ContainerInstanceTable
from ..tables import ContainerTemplate as ContainerTemplateTable
from ..tables import Instance
from ..tables import User

router = APIRouter()
logger = logging.getLogger(__name__)


def _phase_to_status(phase: str) -> str:
    return {
        "queued": "queued",
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _status_feedback(status_name: str, pod_status: PodStatus | None) -> tuple[str, str]:
    normalized = (status_name or "unknown").lower()
    if normalized == "queued":
        return "queued", "Queued for retry when resources become available."
    if normalized == "running":
        if pod_status and not pod_status.ready:
            detail = (pod_status.waiting_message or pod_status.message or "").strip()
            return "starting", detail or "Container is starting."
        return "running", "Container is running."
    if normalized == "pending":
        if not pod_status:
            return "pending", "Scheduling container pod."
        reason_text = " ".join(
            [
                (pod_status.waiting_reason or "").lower(),
                (pod_status.reason or "").lower(),
            ]
        )
        detail = (pod_status.waiting_message or pod_status.message or "").strip()
        if "unschedulable" in reason_text or "failedscheduling" in reason_text:
            return "pending", detail or "Waiting for available resources."
        return "building", detail or "Preparing container image and runtime."
    if normalized == "completed":
        return "completed", "Container completed."
    if normalized == "stopped":
        return "stopped", "Container is stopped."
    if normalized == "failed":
        return "failed", "Container failed."
    return "unknown", "Container status is unknown."


def _normalize_http_path(value: str | None, *, allow_blank: bool = False) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None if allow_blank else "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


def _parse_dependency_checks(raw: str) -> list[ContainerDependencyCheck]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[ContainerDependencyCheck] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ContainerDependencyCheck.model_validate(item))
        except Exception:
            continue
    return out


def _queue_backoff_seconds(attempts: int) -> int:
    base = max(5, int(settings.container_start_queue_base_delay_seconds or 20))
    max_delay = max(base, int(settings.container_start_queue_max_delay_seconds or 300))
    scale = base * (2 ** max(0, attempts))
    return min(max_delay, scale)


def _humanize_queue_reason(raw_reason: str | None) -> str:
    text = str(raw_reason or "").strip()
    lowered = text.lower()
    if "forbidden" in lowered or "quota" in lowered:
        return "Waiting for available resources or namespace quota."
    if "unschedulable" in lowered or "failedscheduling" in lowered:
        return "Waiting for available node CPU/memory."
    if len(text) > 180:
        text = text[:180].rstrip()
    return text or "Waiting for available resources."


def _container_access_url_for_target(node_port: int | None, ingress_host: str | None) -> str | None:
    if ingress_host:
        scheme = (settings.public_scheme or "https").strip() or "https"
        return f"{scheme}://{ingress_host}/"
    if not node_port:
        return None
    host = (settings.kube_node_external_host or "").strip() or "127.0.0.1"
    return f"http://{host}:{int(node_port)}/"


def _container_service_host(instance_id: str) -> str:
    return f"ctsvc-{instance_id[:8]}.{settings.kube_namespace}.svc.cluster.local"


def _container_service_ready(
    instance_id: str,
    container_port: int,
    *,
    protocol: str = "tcp",
    healthcheck_path: str = "/",
    expected_http_status: int = 200,
    success_path: str | None = None,
) -> bool:
    host = _container_service_host(instance_id)
    port = max(1, min(65535, int(container_port or 80)))
    normalized_protocol = str(protocol or "tcp").lower()
    path = _normalize_http_path(healthcheck_path) or "/"
    secondary_path = _normalize_http_path(success_path, allow_blank=True)
    try:
        if normalized_protocol == "http":
            expected = max(100, min(599, int(expected_http_status or 200)))
            conn = HTTPConnection(host, port, timeout=1.8)
            conn.request("GET", path)
            response = conn.getresponse()
            response.read(256)
            if response.status != expected:
                return False
            if secondary_path:
                conn = HTTPConnection(host, port, timeout=1.8)
                conn.request("GET", secondary_path)
                response = conn.getresponse()
                response.read(256)
                if response.status != expected:
                    return False
            return True
        with socket.create_connection((host, port), timeout=1.2):
            return True
    except OSError:
        return False


def _nodeport_ready(
    node_port: int | None,
    *,
    protocol: str = "tcp",
    healthcheck_path: str = "/",
    expected_http_status: int = 200,
    success_path: str | None = None,
) -> bool:
    if not node_port:
        return False
    host = (settings.kube_node_external_host or "").strip()
    if not host:
        return True
    normalized_protocol = str(protocol or "tcp").lower()
    path = _normalize_http_path(healthcheck_path) or "/"
    secondary_path = _normalize_http_path(success_path, allow_blank=True)
    try:
        if normalized_protocol == "http":
            expected = max(100, min(599, int(expected_http_status or 200)))
            conn = HTTPConnection(host, int(node_port), timeout=1.8)
            conn.request("GET", path)
            response = conn.getresponse()
            response.read(256)
            if response.status != expected:
                return False
            if secondary_path:
                conn = HTTPConnection(host, int(node_port), timeout=1.8)
                conn.request("GET", secondary_path)
                response = conn.getresponse()
                response.read(256)
                if response.status != expected:
                    return False
            return True
        with socket.create_connection((host, int(node_port)), timeout=1.2):
            return True
    except OSError:
        return False


def _parse_args(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _parse_env(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _template_out(record: ContainerTemplateTable) -> ContainerTemplateView:
    return ContainerTemplateView(
        id=record.id,
        template_key=str(getattr(record, "template_key", record.id) or record.id),
        version=max(1, int(getattr(record, "version", 1) or 1)),
        is_default=bool(getattr(record, "is_default", True)),
        name=record.name,
        description=record.description,
        container_image_id=record.container_image_id,
        cpu_millicores=record.cpu_millicores,
        memory_mb=record.memory_mb,
        container_port=max(1, int(getattr(record, "container_port", 80) or 80)),
        healthcheck_protocol=str(getattr(record, "healthcheck_protocol", "tcp") or "tcp"),
        healthcheck_path=str(getattr(record, "healthcheck_path", "/") or "/"),
        readiness_http_status=max(100, min(599, int(getattr(record, "readiness_http_status", 200) or 200))),
        readiness_success_path=_normalize_http_path(getattr(record, "readiness_success_path", None), allow_blank=True),
        startup_timeout_seconds=max(10, int(getattr(record, "startup_timeout_seconds", 300) or 300)),
        dependency_checks=_parse_dependency_checks(getattr(record, "dependency_checks_json", "[]")),
        expose_strategy=str(getattr(record, "expose_strategy", "nodeport") or "nodeport"),
        run_as_non_root=bool(getattr(record, "run_as_non_root", False)),
        read_only_root_filesystem=bool(getattr(record, "read_only_root_filesystem", False)),
        command=record.command,
        args=_parse_args(record.args_json),
        env=_parse_env(record.env_json),
        auto_delete_minutes=record.auto_delete_minutes,
        enabled=record.enabled,
        created_at=record.created_at,
    )


def _instance_out(
    record: ContainerInstanceTable,
    *,
    stage: str | None = None,
    detail: str | None = None,
    access_url: str | None = None,
    container_port: int | None = None,
    launch_diagnostics: list[str] | None = None,
) -> ContainerInstanceView:
    resolved_stage, resolved_detail = _status_feedback(record.status, None)
    return ContainerInstanceView(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        status_stage=stage or resolved_stage,
        status_detail=detail or resolved_detail,
        pod_name=record.pod_name,
        access_url=access_url,
        container_port=container_port,
        queue_attempts=max(0, int(getattr(record, "queue_attempts", 0) or 0)),
        queue_not_before=getattr(record, "queue_not_before", None),
        queue_reason=(getattr(record, "queue_reason", None) or None),
        launch_diagnostics=list(launch_diagnostics or []),
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


def _active_workload_count(session: Session) -> int:
    total_vm_active = session.exec(select(Instance).where(Instance.status.in_(["pending", "running"]))).all()
    total_container_active = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.status.in_(["pending", "running"]))
    ).all()
    return len(total_vm_active) + len(total_container_active)


def _mark_queued(record: ContainerInstanceTable, reason: str, *, increment_attempt: bool = True) -> None:
    attempts = max(0, int(getattr(record, "queue_attempts", 0) or 0))
    if increment_attempt:
        attempts += 1
    delay_seconds = _queue_backoff_seconds(attempts)
    queue_reason = _humanize_queue_reason(reason)
    record.status = "queued"
    record.queue_attempts = attempts
    record.queue_reason = queue_reason[:255]
    record.queue_not_before = datetime.utcnow() + timedelta(seconds=delay_seconds)
    record.last_active_at = datetime.utcnow()


def _container_launch_request(instance_id: str, owner: str, template: ContainerTemplateTable, image_ref: str) -> ContainerPodRequest:
    return ContainerPodRequest(
        instance_id=instance_id,
        owner=owner,
        image_ref=image_ref,
        cpu_millicores=template.cpu_millicores,
        memory_mb=template.memory_mb,
        container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
        healthcheck_protocol=str(getattr(template, "healthcheck_protocol", "tcp") or "tcp"),
        healthcheck_path=str(getattr(template, "healthcheck_path", "/") or "/"),
        readiness_http_status=max(100, min(599, int(getattr(template, "readiness_http_status", 200) or 200))),
        readiness_success_path=_normalize_http_path(getattr(template, "readiness_success_path", None), allow_blank=True),
        startup_timeout_seconds=max(10, int(getattr(template, "startup_timeout_seconds", 300) or 300)),
        dependency_checks=_parse_dependency_checks(getattr(template, "dependency_checks_json", "[]")),
        expose_strategy=str(getattr(template, "expose_strategy", "nodeport") or "nodeport"),
        run_as_non_root=bool(getattr(template, "run_as_non_root", False)),
        read_only_root_filesystem=bool(getattr(template, "read_only_root_filesystem", False)),
        command=template.command,
        args=_parse_args(template.args_json),
        env=_parse_env(template.env_json),
    )


def _create_container_runtime(
    *,
    instance_id: str,
    owner: str,
    template: ContainerTemplateTable,
    image_ref: str,
) -> tuple[PodStatus, str | None, int]:
    pod_status = kube.create_container_pod(_container_launch_request(instance_id, owner, template, image_ref))
    container_port = max(1, int(getattr(template, "container_port", 80) or 80))
    expose_strategy = str(getattr(template, "expose_strategy", "nodeport") or "nodeport")
    ingress_enabled = (
        expose_strategy == "ingress"
        and settings.container_ingress_enabled
        and bool((settings.container_ingress_base_domain or "").strip())
    )
    service_type = "ClusterIP" if ingress_enabled else "NodePort"
    try:
        node_port = kube.ensure_container_service(instance_id, owner, container_port, service_type=service_type)
        ingress_host = None
        if ingress_enabled:
            ingress_host = kube.ensure_container_ingress(instance_id, f"ctsvc-{instance_id[:8]}", container_port)
            if ingress_host is None:
                node_port = kube.ensure_container_service(instance_id, owner, container_port, service_type="NodePort")
        access_url = _container_access_url_for_target(node_port=node_port, ingress_host=ingress_host)
        return pod_status, access_url, container_port
    except Exception:
        try:
            kube.delete_container_pod(instance_id, owner)
            kube.delete_container_service(instance_id)
        except Exception:
            pass
        raise


@router.get("/container-templates", response_model=list[ContainerTemplateView])
def list_user_container_templates(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerTemplateView]:
    _ = user
    rows = session.exec(
        select(ContainerTemplateTable)
        .where(ContainerTemplateTable.enabled == True)  # noqa: E712
        .where(ContainerTemplateTable.is_default == True)  # noqa: E712
    ).all()
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return [_template_out(row) for row in rows]


@router.get("/containers", response_model=list[ContainerInstanceView])
def list_user_containers(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerInstanceView]:
    config = session.get(Config, 1) or Config()
    max_concurrency = int(config.max_concurrent_vms)
    active_count = _active_workload_count(session)
    instances = session.exec(select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)).all()
    templates = {row.id: row for row in session.exec(select(ContainerTemplateTable)).all()}
    images = {row.id: row for row in session.exec(select(ContainerImageTable)).all()}
    changed = False
    feedback: dict[str, tuple[str, str]] = {}
    access_map: dict[str, str | None] = {}
    port_map: dict[str, int | None] = {}
    diagnostics_map: dict[str, list[str]] = {}
    to_delete: list[ContainerInstanceTable] = []

    for record in instances:
        tmpl = templates.get(record.template_id)
        container_port = max(1, int(getattr(tmpl, "container_port", 80) or 80)) if tmpl else 80
        healthcheck_protocol = str(getattr(tmpl, "healthcheck_protocol", "tcp") or "tcp") if tmpl else "tcp"
        healthcheck_path = str(getattr(tmpl, "healthcheck_path", "/") or "/") if tmpl else "/"
        readiness_http_status = max(100, min(599, int(getattr(tmpl, "readiness_http_status", 200) or 200))) if tmpl else 200
        readiness_success_path = _normalize_http_path(getattr(tmpl, "readiness_success_path", None), allow_blank=True) if tmpl else None
        expose_strategy = str(getattr(tmpl, "expose_strategy", "nodeport") or "nodeport") if tmpl else "nodeport"
        ingress_enabled = (
            expose_strategy == "ingress"
            and settings.container_ingress_enabled
            and bool((settings.container_ingress_base_domain or "").strip())
        )
        service_type = "ClusterIP" if ingress_enabled else "NodePort"
        port_map[record.id] = container_port
        if record.status == "queued":
            if not settings.container_start_queue_enabled or not tmpl:
                feedback[record.id] = ("queued", record.queue_reason or "Queued for retry when resources are available.")
                access_map[record.id] = None
            else:
                now = datetime.utcnow()
                not_before = getattr(record, "queue_not_before", None)
                should_try = not_before is None or now >= not_before
                image = images.get(tmpl.container_image_id)
                if should_try and image and active_count < max_concurrency:
                    try:
                        pod_status, access_url, _ = _create_container_runtime(
                            instance_id=record.id,
                            owner=record.owner,
                            template=tmpl,
                            image_ref=image.image_ref,
                        )
                        record.status = "pending"
                        record.pod_name = kube.container_pod_name(instance_id=record.id, owner=record.owner)
                        record.started_at = now
                        record.last_active_at = now
                        record.queue_not_before = None
                        record.queue_reason = None
                        stage, detail = _status_feedback("pending", pod_status)
                        feedback[record.id] = (stage, detail)
                        access_map[record.id] = access_url
                        session.add(record)
                        changed = True
                        active_count += 1
                    except Exception as exc:
                        _mark_queued(record, str(exc) or "Waiting for available resources.")
                        feedback[record.id] = ("queued", record.queue_reason or "Queued for retry.")
                        access_map[record.id] = None
                        session.add(record)
                        changed = True
                else:
                    retry_in = 0
                    if not_before:
                        retry_in = max(0, int((not_before - now).total_seconds()))
                    reason = record.queue_reason or "Waiting for available resources."
                    if retry_in > 0:
                        reason = f"{reason} Retrying in {retry_in}s."
                    feedback[record.id] = ("queued", reason)
                    access_map[record.id] = None
            diagnostics_map[record.id] = []
            continue

        pod_status: PodStatus | None = None
        try:
            pod_status = kube.get_container_status(record.id, record.owner)
            mapped = _phase_to_status(pod_status.phase)
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise

        stage, detail = _status_feedback(mapped, pod_status)
        feedback[record.id] = (stage, detail)
        try:
            diagnostics_map[record.id] = kube.get_container_launch_diagnostics(record.id, record.owner)
        except Exception:
            diagnostics_map[record.id] = []
        if mapped in {"pending", "running"} and tmpl:
            try:
                node_port = kube.ensure_container_service(
                    record.id,
                    record.owner,
                    container_port,
                    service_type=service_type,
                )
                ingress_host = None
                if ingress_enabled:
                    ingress_host = kube.ensure_container_ingress(record.id, f"ctsvc-{record.id[:8]}", container_port)
                    if ingress_host is None:
                        node_port = kube.ensure_container_service(
                            record.id,
                            record.owner,
                            container_port,
                            service_type="NodePort",
                        )
                if mapped == "running" and _container_service_ready(
                    record.id,
                    container_port,
                    protocol=healthcheck_protocol,
                    healthcheck_path=healthcheck_path,
                    expected_http_status=readiness_http_status,
                    success_path=readiness_success_path,
                ) and (
                    ingress_host is not None
                    or _nodeport_ready(
                        node_port,
                        protocol=healthcheck_protocol,
                        healthcheck_path=healthcheck_path,
                        expected_http_status=readiness_http_status,
                        success_path=readiness_success_path,
                    )
                ):
                    access_map[record.id] = _container_access_url_for_target(node_port=node_port, ingress_host=ingress_host)
                elif mapped == "running":
                    feedback[record.id] = (
                        "starting",
                        "Container pod is running; waiting for application startup.",
                    )
                    access_map[record.id] = None
                else:
                    access_map[record.id] = None
            except ApiException as exc:
                if exc.status != 404:
                    raise
                access_map[record.id] = None
        else:
            access_map[record.id] = None
            try:
                kube.delete_container_service(record.id)
            except Exception:
                pass

        if mapped == "pending" and settings.container_start_queue_enabled:
            reason_text = " ".join(
                [
                    (pod_status.waiting_reason or "").lower() if pod_status else "",
                    (pod_status.reason or "").lower() if pod_status else "",
                    (pod_status.message or "").lower() if pod_status else "",
                ]
            )
            if "unschedulable" in reason_text or "failedscheduling" in reason_text:
                try:
                    kube.delete_container_pod(record.id, record.owner)
                    kube.delete_container_service(record.id)
                except Exception:
                    pass
                _mark_queued(record, detail or "Waiting for available resources.")
                mapped = "queued"
                feedback[record.id] = ("queued", record.queue_reason or "Queued for retry when resources are available.")
                access_map[record.id] = None
                session.add(record)
                changed = True
                active_count = max(0, active_count - 1)

        if mapped != record.status:
            record.status = mapped
            record.last_active_at = datetime.utcnow()
            session.add(record)
            changed = True

        if tmpl and record.status in {"stopped", "completed"}:
            cutoff = datetime.utcnow() - timedelta(minutes=max(1, int(tmpl.auto_delete_minutes or 60)))
            if record.last_active_at < cutoff:
                try:
                    kube.delete_container_pod(record.id, record.owner)
                    kube.delete_container_service(record.id)
                except Exception:
                    pass
                to_delete.append(record)

    if changed:
        session.commit()
    if to_delete:
        for row in to_delete:
            session.delete(row)
        session.commit()
        instances = session.exec(select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)).all()

    out: list[ContainerInstanceView] = []
    for row in instances:
        stage, detail = feedback.get(row.id, _status_feedback(row.status, None))
        out.append(
            _instance_out(
                row,
                stage=stage,
                detail=detail,
                access_url=access_map.get(row.id),
                container_port=port_map.get(row.id),
                launch_diagnostics=diagnostics_map.get(row.id, []),
            )
        )
    return out


@router.post("/container-templates/{template_id}/start", response_model=ContainerInstanceView, status_code=status.HTTP_201_CREATED)
def start_container_template(
    template_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    template = session.get(ContainerTemplateTable, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    image = session.get(ContainerImageTable, template.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image missing for template")

    config = session.get(Config, 1) or Config()
    user_vm_instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    user_container_instances = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)
    ).all()
    for row in [*user_vm_instances, *user_container_instances]:
        if row.status not in {"stopped", "completed", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You already have a workload running. Delete the current workload before starting a new one.",
            )

    total_vm_active = session.exec(select(Instance).where(Instance.status.in_(["pending", "running"]))).all()
    total_container_active = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.status.in_(["pending", "running"]))
    ).all()
    cluster_full = len(total_vm_active) + len(total_container_active) >= int(config.max_concurrent_vms)

    instance_id = str(uuid4())
    now = datetime.utcnow()
    if cluster_full and settings.container_start_queue_enabled:
        record = ContainerInstanceTable(
            id=instance_id,
            template_id=template.id,
            owner=user.username,
            status="queued",
            pod_name=kube.container_pod_name(instance_id=instance_id, owner=user.username),
            queue_attempts=0,
            queue_reason="Cluster concurrency limit reached. Waiting for available resources.",
            queue_not_before=now + timedelta(seconds=_queue_backoff_seconds(0)),
            started_at=now,
            last_active_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return _instance_out(
            record,
            stage="queued",
            detail=record.queue_reason,
            access_url=None,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            launch_diagnostics=[],
        )
    if cluster_full:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")

    pod_name = kube.container_pod_name(instance_id=instance_id, owner=user.username)
    try:
        pod_status, access_url, container_port = _create_container_runtime(
            instance_id=instance_id,
            owner=user.username,
            template=template,
            image_ref=image.image_ref,
        )
        record = ContainerInstanceTable(
            id=instance_id,
            template_id=template.id,
            owner=user.username,
            status="pending",
            pod_name=pod_name,
            started_at=now,
            last_active_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        stage, detail = _status_feedback(record.status, pod_status)
        diagnostics = kube.get_container_launch_diagnostics(record.id, record.owner)
        return _instance_out(
            record,
            stage=stage,
            detail=detail,
            access_url=access_url,
            container_port=container_port,
            launch_diagnostics=diagnostics,
        )
    except Exception as exc:
        if settings.container_start_queue_enabled:
            queue_reason = _humanize_queue_reason(str(exc))
            record = ContainerInstanceTable(
                id=instance_id,
                template_id=template.id,
                owner=user.username,
                status="queued",
                pod_name=pod_name,
                queue_attempts=1,
                queue_reason=queue_reason[:255],
                queue_not_before=now + timedelta(seconds=_queue_backoff_seconds(1)),
                started_at=now,
                last_active_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _instance_out(
                record,
                stage="queued",
                detail=record.queue_reason,
                access_url=None,
                container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
                launch_diagnostics=[],
            )
        raise


@router.post("/containers/{instance_id}/stop", response_model=ContainerInstanceView)
def stop_container(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")

    if record.status != "queued":
        kube.stop_container_pod(instance_id, user.username)
        try:
            kube.delete_container_service(instance_id)
        except Exception:
            pass
    record.status = "stopped"
    record.queue_not_before = None
    record.queue_reason = None
    record.last_active_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    template = session.get(ContainerTemplateTable, record.template_id)
    container_port = max(1, int(getattr(template, "container_port", 80) or 80)) if template else None
    return _instance_out(record, container_port=container_port)


@router.post("/containers/{instance_id}/start", response_model=ContainerInstanceView, status_code=status.HTTP_200_OK)
def restart_container(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")

    template = session.get(ContainerTemplateTable, record.template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    image = session.get(ContainerImageTable, template.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image missing for template")

    try:
        kube.delete_container_pod(instance_id, user.username)
    except ApiException as exc:
        if exc.status != 404:
            raise

    pod_status, access_url, container_port = _create_container_runtime(
        instance_id=record.id,
        owner=user.username,
        template=template,
        image_ref=image.image_ref,
    )

    record.status = "pending"
    record.pod_name = kube.container_pod_name(instance_id=record.id, owner=user.username)
    record.queue_attempts = 0
    record.queue_not_before = None
    record.queue_reason = None
    record.started_at = datetime.utcnow()
    record.last_active_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, pod_status)
    diagnostics = kube.get_container_launch_diagnostics(record.id, record.owner)
    return _instance_out(
        record,
        stage=stage,
        detail=detail,
        access_url=access_url,
        container_port=container_port,
        launch_diagnostics=diagnostics,
    )


@router.delete("/containers/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_container(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")

    kube.delete_container_pod(instance_id, user.username)
    try:
        kube.delete_container_service(instance_id)
    except Exception:
        pass
    session.delete(record)
    session.commit()
