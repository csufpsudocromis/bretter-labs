from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..auth import require_user
from ..config import settings
from ..db import get_session
from ..models import SiteSettings, SSOSettings, VMInstance, VMTemplate
from ..services.kubernetes import PodRequest, PodStatus, kube
from ..tables import Config, Image, Instance, Template, User

router = APIRouter()


def _public_scheme() -> str:
    scheme = (settings.public_scheme or "https").strip().lower()
    return scheme if scheme in {"http", "https"} else "https"


def _phase_to_instance_status(phase: str) -> str:
    return {
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _status_feedback(status: str, pod_status: PodStatus | None) -> tuple[str, str]:
    normalized = (status or "unknown").lower()
    if normalized == "running":
        if pod_status and not pod_status.ready:
            detail = (pod_status.waiting_message or pod_status.message or "").strip()
            return "starting", detail or "VM process started; waiting for readiness."
        return "running", "VM is running."
    if normalized == "pending":
        if not pod_status:
            return "pending", "Scheduling VM pod."
        reason_text = " ".join(
            [
                (pod_status.waiting_reason or "").lower(),
                (pod_status.reason or "").lower(),
            ]
        )
        detail = (pod_status.waiting_message or pod_status.message or "").strip()
        if "unschedulable" in reason_text or "failedscheduling" in reason_text:
            return "pending", detail or "Waiting for available node resources."
        build_reason_keywords = (
            "containercreating",
            "podinitializing",
            "createcontainer",
            "pulling",
            "errimagepull",
            "imagepullbackoff",
            "mountvolume",
            "attachvolume",
        )
        build_detail_keywords = (
            "persistentvolumeclaim",
            "volume",
            "mount",
            "attach",
            "pulling image",
            "creating container",
            "initializing",
        )
        detail_text = detail.lower()
        if any(token in reason_text for token in build_reason_keywords) or any(
            token in detail_text for token in build_detail_keywords
        ):
            return "building", detail or "Preparing VM disk and container."
        return "pending", detail or "Waiting in scheduler queue."
    if normalized == "completed":
        return "completed", "VM completed and stopped."
    if normalized == "stopped":
        return "stopped", "VM is stopped."
    if normalized == "failed":
        return "failed", "VM failed to start or run."
    return "unknown", "VM status is unknown."


def _require_clone_ready(image: Image) -> None:
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM launch is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    if not image.source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not prepared for clone-based storage; re-import the image from admin",
        )


@router.get("/templates", response_model=list[VMTemplate])
def list_available_templates(user: User = Depends(require_user), session: Session = Depends(get_session)) -> list[VMTemplate]:
    templates = session.exec(select(Template).where(Template.enabled == True)).all()  # noqa: E712
    return [
        VMTemplate(
            id=record.id,
            name=record.name,
            description=record.description,
            os_type=record.os_type,
            image_id=record.image_id,
            cpu_cores=record.cpu_cores,
            ram_mb=record.ram_mb,
            auto_delete_minutes=record.auto_delete_minutes,
            idle_timeout_minutes=getattr(record, "idle_timeout_minutes", settings.idle_timeout_minutes),
            preclone_pool_size=getattr(record, "preclone_pool_size", 0),
            preclone_pool_max=getattr(record, "preclone_pool_max", getattr(record, "preclone_pool_size", 0)),
            enabled=record.enabled,
            network_mode=getattr(record, "network_mode", "bridge"),
            created_at=record.created_at,
        )
        for record in templates
    ]


@router.get("/pods", response_model=list[VMInstance])
def list_user_pods(user: User = Depends(require_user), session: Session = Depends(get_session)) -> list[VMInstance]:
    instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    templates = {t.id: t for t in session.exec(select(Template)).all()}
    changed = False
    to_delete: list[Instance] = []
    feedback: dict[str, tuple[str, str]] = {}
    for record in instances:
        # Treat every poll from the user as activity so the idle reaper doesn't reclaim a live VM.
        if record.status in {"running", "pending"}:
            record.last_active_at = datetime.utcnow()
            session.add(record)
            changed = True
        pod_status: PodStatus | None = None
        try:
            pod_status = kube.get_status(record.id, record.owner)
            mapped = _phase_to_instance_status(pod_status.phase)
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise
        feedback[record.id] = _status_feedback(mapped, pod_status)
        if mapped != record.status:
            record.status = mapped
            record.last_active_at = datetime.utcnow()
            session.add(record)
            changed = True
        # Auto-delete stopped/completed instances based on template setting.
        tmpl = templates.get(record.template_id)
        if tmpl and record.status in {"stopped", "completed"}:
            cutoff = datetime.utcnow() - timedelta(minutes=tmpl.auto_delete_minutes)
            if record.last_active_at < cutoff:
                try:
                    kube.delete_pod(record.id, record.owner, disk_pvc=record.disk_pvc)
                except Exception:
                    pass
                to_delete.append(record)
    if changed:
        session.commit()
    if to_delete:
        for rec in to_delete:
            session.delete(rec)
        session.commit()
        # refresh instances list without deleted ones
        instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()

    items: list[VMInstance] = []
    for record in instances:
        stage, detail = feedback.get(record.id, _status_feedback(record.status, None))
        items.append(
            VMInstance(
                id=record.id,
                template_id=record.template_id,
                owner=record.owner,
                status=record.status,
                status_stage=stage,
                status_detail=detail,
                started_at=record.started_at,
                last_active_at=record.last_active_at,
                console_url=record.console_url,
            )
        )
    return items


@router.post("/pods/{instance_id}/activity", status_code=status.HTTP_204_NO_CONTENT)
def record_vm_activity(
    instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> None:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    record.last_active_at = datetime.utcnow()
    session.add(record)
    session.commit()


@router.get("/settings/site", response_model=SiteSettings)
def site_settings(session: Session = Depends(get_session)) -> SiteSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return SiteSettings(
        site_title=cfg.site_title,
        site_tagline=cfg.site_tagline,
        theme_bg_color=cfg.theme_bg_color,
        theme_text_color=cfg.theme_text_color,
        theme_button_color=cfg.theme_button_color,
        theme_button_text_color=cfg.theme_button_text_color,
        theme_bg_image=cfg.theme_bg_image,
        theme_bg_image_overlay_opacity=cfg.theme_bg_image_overlay_opacity,
        theme_contrast_body=cfg.theme_contrast_body,
        theme_contrast_button=cfg.theme_contrast_button,
        theme_contrast_tile=cfg.theme_contrast_tile,
        theme_contrast_tile_border=cfg.theme_contrast_tile_border,
        theme_font_family=cfg.theme_font_family,
        theme_font_size_base=cfg.theme_font_size_base,
        theme_font_size_h1=cfg.theme_font_size_h1,
        theme_font_size_h2=cfg.theme_font_size_h2,
        theme_tile_bg=cfg.theme_tile_bg,
        theme_tile_border=cfg.theme_tile_border,
        theme_tile_opacity=cfg.theme_tile_opacity,
        theme_tile_border_opacity=cfg.theme_tile_border_opacity,
    )


@router.get("/settings/sso", response_model=SSOSettings)
def sso_settings(session: Session = Depends(get_session)) -> SSOSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret="",
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
    )


@router.post("/templates/{template_id}/start", response_model=VMInstance, status_code=status.HTTP_201_CREATED)
def start_vm(
    template_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> VMInstance:
    template = session.get(Template, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    _require_clone_ready(image)

    config = session.get(Config, 1) or Config()
    total_running = session.exec(select(Instance).where(Instance.status == "running")).all()
    user_instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    # Block if any of the user's labs are not stopped/completed/failed.
    for inst in user_instances:
        if inst.status not in {"stopped", "completed", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You already have a virtual lab running. Delete the current lab before starting a new one.",
            )
    if len(total_running) >= config.max_concurrent_vms:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")
    # Enforce per-user limit against any non-stopped labs.
    active_count = sum(1 for inst in user_instances if inst.status not in {"stopped", "completed", "failed"})
    if active_count >= config.per_user_vm_limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="per-user concurrency limit reached")

    instance_id = str(uuid4())
    try:
        warm_pool_pvc = kube.reserve_warm_pool_pvc(template.id, instance_id, user.username)
    except Exception:
        warm_pool_pvc = None
    pod_request = PodRequest(
        instance_id=instance_id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        image_source_pvc=image.source_pvc,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
        network_mode=getattr(template, "network_mode", "bridge"),
        instance_disk_pvc=warm_pool_pvc,
    )
    try:
        pod_status = kube.create_pod(pod_request)
    except Exception:
        if warm_pool_pvc:
            try:
                kube._client().delete_namespaced_persistent_volume_claim(
                    name=warm_pool_pvc,
                    namespace=settings.kube_namespace,
                )
            except Exception:
                pass
        raise
    # Create a NodePort service for browser-based SPICE (websockify on 6080).
    service_name = f"svc-{instance_id[:8]}"
    node_port = kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name)
    external_host = settings.kube_node_external_host or "127.0.0.1"
    embed_page = "spice-embed.html" if settings.kube_spice_embed_configmap else "spice_auto.html"
    public_scheme = _public_scheme()
    secure_param = 1 if public_scheme == "https" else 0
    # Use the slim embed page (if mounted) to auto-connect and hide chrome.
    console_title = quote(template.name, safe="")
    idle_minutes = template.idle_timeout_minutes or settings.idle_timeout_minutes
    console_url = (
        f"{public_scheme}://{external_host}:{node_port}/{embed_page}"
        f"?host={external_host}&port={node_port}&secure={secure_param}&title={console_title}"
        f"&instance_id={instance_id}&idle_minutes={idle_minutes}"
    )

    instance = Instance(
        id=instance_id,
        template_id=template.id,
        owner=user.username,
        status="pending",
        disk_pvc=pod_status.disk_pvc,
        started_at=datetime.utcnow(),
        last_active_at=datetime.utcnow(),
        console_url=console_url,
    )
    session.add(instance)
    session.commit()
    session.refresh(instance)
    stage, detail = _status_feedback(instance.status, pod_status)
    return VMInstance(
        id=instance.id,
        template_id=instance.template_id,
        owner=instance.owner,
        status=instance.status,
        status_stage=stage,
        status_detail=detail,
        started_at=instance.started_at,
        last_active_at=instance.last_active_at,
        console_url=instance.console_url,
    )


@router.post("/pods/{instance_id}/stop", response_model=VMInstance)
def stop_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.stop_pod(instance_id, record.owner)
    record.status = "stopped"
    record.last_active_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, None)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        status_stage=stage,
        status_detail=detail,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.post("/pods/{instance_id}/start", response_model=VMInstance, status_code=status.HTTP_200_OK)
def restart_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    template = session.get(Template, record.template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    _require_clone_ready(image)

    # Ensure any old pod with the same name is removed before re-create.
    try:
        kube.delete_pod(instance_id, user.username, disk_pvc=record.disk_pvc)
    except ApiException as exc:
        if exc.status != 404:
            raise

    try:
        warm_pool_pvc = kube.reserve_warm_pool_pvc(template.id, record.id, user.username)
    except Exception:
        warm_pool_pvc = None
    pod_request = PodRequest(
        instance_id=record.id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        image_source_pvc=image.source_pvc,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
        network_mode=getattr(template, "network_mode", "bridge"),
        instance_disk_pvc=warm_pool_pvc,
    )
    try:
        pod_status = kube.create_pod(pod_request)
    except Exception:
        if warm_pool_pvc:
            try:
                kube._client().delete_namespaced_persistent_volume_claim(
                    name=warm_pool_pvc,
                    namespace=settings.kube_namespace,
                )
            except Exception:
                pass
        raise
    service_name = f"svc-{instance_id[:8]}"
    node_port = kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name)
    external_host = settings.kube_node_external_host or "127.0.0.1"
    embed_page = "spice-embed.html" if settings.kube_spice_embed_configmap else "spice_auto.html"
    public_scheme = _public_scheme()
    secure_param = 1 if public_scheme == "https" else 0
    console_title = quote(template.name, safe="")
    idle_minutes = template.idle_timeout_minutes or settings.idle_timeout_minutes
    console_url = (
        f"{public_scheme}://{external_host}:{node_port}/{embed_page}"
        f"?host={external_host}&port={node_port}&secure={secure_param}&title={console_title}"
        f"&instance_id={record.id}&idle_minutes={idle_minutes}"
    )

    record.status = "pending"
    record.disk_pvc = pod_status.disk_pvc
    record.started_at = datetime.utcnow()
    record.last_active_at = datetime.utcnow()
    record.console_url = console_url
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, pod_status)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        status_stage=stage,
        status_detail=detail,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.delete("/pods/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> None:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.delete_pod(instance_id, record.owner, disk_pvc=record.disk_pvc)
    session.delete(record)
    session.commit()
