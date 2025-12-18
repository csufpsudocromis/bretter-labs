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
from ..services.kubernetes import PodRequest, kube
from ..tables import Config, Image, Instance, Template, User

router = APIRouter()


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
            enabled=record.enabled,
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
    for record in instances:
        # Treat every poll from the user as activity so the idle reaper doesn't reclaim a live VM.
        if record.status in {"running", "pending"}:
            record.last_active_at = datetime.utcnow()
            session.add(record)
            changed = True
        try:
            pod_status = kube.get_status(record.id, record.owner)
            phase = (pod_status.phase or "").lower()
            mapped = {
                "pending": "pending",
                "running": "running",
                "succeeded": "completed",
                "failed": "failed",
                "unknown": "unknown",
            }.get(phase, "unknown")
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise
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
                    kube.delete_pod(record.id, record.owner)
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
        items.append(
            VMInstance(
                id=record.id,
                template_id=record.template_id,
                owner=record.owner,
                status=record.status,
                started_at=record.started_at,
                last_active_at=record.last_active_at,
                console_url=record.console_url,
            )
        )
    return items


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
    disk_path = Path(settings.storage_root) / Path(image.filename).name
    if not disk_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"image file not found on storage: {disk_path}")

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
    pod_request = PodRequest(
        instance_id=instance_id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
        network_mode=getattr(template, "network_mode", "default"),
    )
    pod_status = kube.create_pod(pod_request)
    # Create a NodePort service for browser-based SPICE (websockify on 6080).
    service_name = f"svc-{instance_id[:8]}"
    node_port = kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name)
    external_host = settings.kube_node_external_host or "127.0.0.1"
    embed_page = "spice-embed.html" if settings.kube_spice_embed_configmap else "spice_auto.html"
    # Use the slim embed page (if mounted) to auto-connect and hide chrome.
    console_title = quote(template.name, safe="")
    console_url = (
        f"http://{external_host}:{node_port}/{embed_page}"
        f"?host={external_host}&port={node_port}&secure=0&title={console_title}"
    )

    instance = Instance(
        id=instance_id,
        template_id=template.id,
        owner=user.username,
        status="pending",
        started_at=datetime.utcnow(),
        last_active_at=datetime.utcnow(),
        console_url=console_url,
    )
    session.add(instance)
    session.commit()
    session.refresh(instance)
    return VMInstance(
        id=instance.id,
        template_id=instance.template_id,
        owner=instance.owner,
        status=instance.status,
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
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
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
    disk_path = Path(settings.storage_root) / Path(image.filename).name
    if not disk_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"image file not found on storage: {disk_path}")

    # Ensure any old pod with the same name is removed before re-create.
    try:
        kube.delete_pod(instance_id, user.username)
    except ApiException as exc:
        if exc.status != 404:
            raise

    pod_request = PodRequest(
        instance_id=record.id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
    )
    kube.create_pod(pod_request)
    service_name = f"svc-{instance_id[:8]}"
    node_port = kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name)
    external_host = settings.kube_node_external_host or "127.0.0.1"
    embed_page = "spice-embed.html" if settings.kube_spice_embed_configmap else "spice_auto.html"
    console_title = quote(template.name, safe="")
    console_url = (
        f"http://{external_host}:{node_port}/{embed_page}"
        f"?host={external_host}&port={node_port}&secure=0&title={console_title}"
    )

    record.status = "pending"
    record.started_at = datetime.utcnow()
    record.last_active_at = datetime.utcnow()
    record.console_url = console_url
    session.add(record)
    session.commit()
    session.refresh(record)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.delete("/pods/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> None:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.delete_pod(instance_id, record.owner)
    session.delete(record)
    session.commit()
