import json
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..auth import require_user
from ..config import settings
from ..db import get_session
from ..models import ContainerInstance as ContainerInstanceView
from ..models import ContainerTemplate as ContainerTemplateView
from ..services.kubernetes import ContainerPodRequest, PodStatus, kube
from ..tables import Config
from ..tables import ContainerImage as ContainerImageTable
from ..tables import ContainerInstance as ContainerInstanceTable
from ..tables import ContainerTemplate as ContainerTemplateTable
from ..tables import Instance
from ..tables import User

router = APIRouter()


def _phase_to_status(phase: str) -> str:
    return {
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _status_feedback(status_name: str, pod_status: PodStatus | None) -> tuple[str, str]:
    normalized = (status_name or "unknown").lower()
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


def _container_access_url(node_port: int | None) -> str | None:
    if not node_port:
        return None
    host = (settings.kube_node_external_host or "").strip() or "127.0.0.1"
    return f"http://{host}:{int(node_port)}/"


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
        name=record.name,
        description=record.description,
        container_image_id=record.container_image_id,
        cpu_millicores=record.cpu_millicores,
        memory_mb=record.memory_mb,
        container_port=max(1, int(getattr(record, "container_port", 80) or 80)),
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
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


@router.get("/container-templates", response_model=list[ContainerTemplateView])
def list_user_container_templates(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerTemplateView]:
    _ = user
    rows = session.exec(select(ContainerTemplateTable).where(ContainerTemplateTable.enabled == True)).all()  # noqa: E712
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return [_template_out(row) for row in rows]


@router.get("/containers", response_model=list[ContainerInstanceView])
def list_user_containers(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerInstanceView]:
    instances = session.exec(select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)).all()
    templates = {row.id: row for row in session.exec(select(ContainerTemplateTable)).all()}
    changed = False
    feedback: dict[str, tuple[str, str]] = {}
    access_map: dict[str, str | None] = {}
    port_map: dict[str, int | None] = {}
    to_delete: list[ContainerInstanceTable] = []

    for record in instances:
        tmpl = templates.get(record.template_id)
        container_port = max(1, int(getattr(tmpl, "container_port", 80) or 80)) if tmpl else 80
        port_map[record.id] = container_port
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
        if mapped in {"pending", "running"} and tmpl:
            try:
                node_port = kube.ensure_container_service(record.id, record.owner, container_port)
                access_map[record.id] = _container_access_url(node_port)
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
    if len(total_vm_active) + len(total_container_active) >= int(config.max_concurrent_vms):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")

    instance_id = str(uuid4())
    pod_name = kube.container_pod_name(instance_id=instance_id, owner=user.username)
    pod_status = kube.create_container_pod(
        ContainerPodRequest(
            instance_id=instance_id,
            owner=user.username,
            image_ref=image.image_ref,
            cpu_millicores=template.cpu_millicores,
            memory_mb=template.memory_mb,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            command=template.command,
            args=_parse_args(template.args_json),
            env=_parse_env(template.env_json),
        )
    )
    container_port = max(1, int(getattr(template, "container_port", 80) or 80))
    try:
        node_port = kube.ensure_container_service(instance_id, user.username, container_port)
    except Exception:
        try:
            kube.delete_container_pod(instance_id, user.username)
        except Exception:
            pass
        raise
    access_url = _container_access_url(node_port)

    now = datetime.utcnow()
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
    return _instance_out(record, stage=stage, detail=detail, access_url=access_url, container_port=container_port)


@router.post("/containers/{instance_id}/stop", response_model=ContainerInstanceView)
def stop_container(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")

    kube.stop_container_pod(instance_id, user.username)
    try:
        kube.delete_container_service(instance_id)
    except Exception:
        pass
    record.status = "stopped"
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

    pod_status = kube.create_container_pod(
        ContainerPodRequest(
            instance_id=record.id,
            owner=user.username,
            image_ref=image.image_ref,
            cpu_millicores=template.cpu_millicores,
            memory_mb=template.memory_mb,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            command=template.command,
            args=_parse_args(template.args_json),
            env=_parse_env(template.env_json),
        )
    )
    container_port = max(1, int(getattr(template, "container_port", 80) or 80))
    node_port = kube.ensure_container_service(record.id, user.username, container_port)
    access_url = _container_access_url(node_port)

    record.status = "pending"
    record.pod_name = kube.container_pod_name(instance_id=record.id, owner=user.username)
    record.started_at = datetime.utcnow()
    record.last_active_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, pod_status)
    return _instance_out(record, stage=stage, detail=detail, access_url=access_url, container_port=container_port)


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
