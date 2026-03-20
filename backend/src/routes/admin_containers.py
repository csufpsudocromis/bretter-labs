import json
import logging
import re
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlmodel import Session, select

from ..auth import require_permission, require_user
from ..config import settings
from ..db import get_session, session_scope
from ..models import (
    ContainerDependencyCheck,
    ContainerImageCreate,
    ContainerImageMeta,
    ContainerInstance as ContainerInstanceView,
    ContainerImageUpdate,
    ContainerTemplate,
    ContainerTemplateCreate,
    ContainerTemplateUpdate,
)
from ..tables import ContainerImage as ContainerImageTable
from ..tables import ContainerInstance as ContainerInstanceTable
from ..tables import ContainerTemplate as ContainerTemplateTable
from ..tables import User
from ..services.kubernetes import kube
from ..services.tenant_context import (
    GLOBAL_TENANT,
    actor_tenant,
    assert_actor_can_manage_tenant,
    is_platform_admin,
    normalize_tenant,
    resolve_resource_tenant,
)
from ..time_utils import utc_now
from ..rbac import Permission

router = APIRouter(dependencies=[Depends(require_permission(Permission.ADMIN_ACCESS))])
logger = logging.getLogger(__name__)

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_TEMPLATE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _tenant_scope_for_actor(actor: User, *, include_global: bool = True) -> set[str] | None:
    if is_platform_admin(actor):
        return None
    scoped = {actor_tenant(actor)}
    if include_global:
        scoped.add(GLOBAL_TENANT)
    return scoped


def _normalize_container_image_ref(value: str) -> str:
    ref = (value or "").strip()
    if not ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_ref is required")
    if " " in ref:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image_ref cannot contain spaces")
    if not _IMAGE_REF_RE.match(ref):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image_ref format looks invalid")
    if "@" in ref:
        return ref
    tail = ref.rsplit("/", 1)[-1]
    if ":" not in tail:
        return f"{ref}:latest"
    return ref


def _is_local_dev_image_ref(image_ref: str) -> bool:
    lowered = str(image_ref or "").strip().lower()
    return lowered.startswith("localhost/") or ":local" in lowered or "local-" in lowered


def _validate_env(env_map: dict[str, str]) -> dict[str, str]:
    validated: dict[str, str] = {}
    for raw_key, raw_value in (env_map or {}).items():
        key = str(raw_key).strip()
        if not key:
            continue
        if not _ENV_KEY_RE.match(key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid env var name: {key}",
            )
        validated[key] = str(raw_value)
    return validated


def _allowed_registries() -> set[str]:
    raw = (settings.container_allowed_registries or "").strip()
    if not raw:
        return {"docker.io"}
    out = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return out or {"docker.io"}


def _registry_from_ref(image_ref: str) -> str:
    ref = (image_ref or "").strip()
    if not ref:
        return ""
    first = ref.split("/", 1)[0].lower()
    if "/" not in ref:
        return "docker.io"
    if "." in first or ":" in first or first == "localhost":
        return first
    return "docker.io"


def _enforce_registry_policy(image_ref: str) -> None:
    allowed = _allowed_registries()
    if "*" in allowed:
        if bool(getattr(settings, "production_profile", False)) and _is_local_dev_image_ref(image_ref):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="local/dev image references are not allowed in production profile",
            )
        return
    registry = _registry_from_ref(image_ref)
    if registry not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"registry {registry or 'unknown'} is not allowed by policy",
        )
    if bool(getattr(settings, "production_profile", False)) and _is_local_dev_image_ref(image_ref):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="local/dev image references are not allowed in production profile",
        )


def _verify_image_signature(image_ref: str) -> str | None:
    if not settings.container_signature_verification_enabled:
        return None
    try:
        warning = kube.verify_container_image_signature(image_ref)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="image signature verification timed out"
        ) from exc
    except RuntimeError as exc:
        detail = str(exc or "").strip()
        if "no signatures found" in detail.lower():
            warning = "Image has no signatures; continuing with warning-only policy."
            logger.warning("Container image %s has no signatures; allowing registration by policy.", image_ref)
            return warning
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(detail or "signature verification failed")[:500],
        ) from exc
    return warning


def _normalize_template_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower()).strip("-")
    if not key:
        key = f"ct-{uuid4().hex[:12]}"
    if len(key) > 64:
        key = key[:64].rstrip("-")
    if not _TEMPLATE_KEY_RE.match(key):
        key = f"ct-{uuid4().hex[:12]}"
    return key


def _normalize_container_network_mode(value: object) -> str:
    mode = str(value or "bridge").strip().lower()
    if mode in {"none", "isolated", "unrestricted"}:
        return mode
    return "bridge"


def _normalize_http_path(value: str | None, *, allow_blank: bool = False) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None if allow_blank else "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


def _normalize_optional_command(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    return raw or None


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


def _serialize_dependency_checks(items: list[ContainerDependencyCheck] | None) -> str:
    payload = [item.model_dump() for item in (items or [])]
    return json.dumps(payload, separators=(",", ":"))


def _trigger_image_scan(session: Session, record: ContainerImageTable) -> None:
    if not settings.container_scan_enabled:
        return
    try:
        status_text, summary_text = kube.scan_container_image(
            image_ref=record.image_ref,
            severity=(settings.container_scan_severity or "HIGH,CRITICAL"),
        )
    except Exception:
        logger.warning("Container image scan failed for %s", record.image_ref, exc_info=True)
        record.last_scan_at = utc_now()
        record.last_scan_status = "error"
        record.last_scan_summary = "scan failed"
        session.add(record)
        session.commit()
        session.refresh(record)
        return
    record.last_scan_at = utc_now()
    record.last_scan_status = status_text
    record.last_scan_summary = summary_text[:512]
    session.add(record)
    session.commit()
    session.refresh(record)


def _run_image_scan_for_id(image_id: str) -> None:
    try:
        with session_scope() as session:
            record = session.get(ContainerImageTable, image_id)
            if not record:
                return
            _trigger_image_scan(session, record)
    except Exception:
        logger.warning("Container image background scan failed for %s", image_id, exc_info=True)


def _run_image_prepull(image_ref: str) -> None:
    try:
        kube.prepull_container_image(image_ref)
    except Exception:
        logger.warning("Container image pre-pull failed for %s", image_ref, exc_info=True)


def _image_out(record: ContainerImageTable, signature_warning: str | None = None) -> ContainerImageMeta:
    return ContainerImageMeta(
        id=record.id,
        name=record.name,
        image_ref=record.image_ref,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        signature_warning=signature_warning,
        last_scan_at=getattr(record, "last_scan_at", None),
        last_scan_status=str(getattr(record, "last_scan_status", "never") or "never"),
        last_scan_summary=str(getattr(record, "last_scan_summary", "") or ""),
        created_at=record.created_at,
    )


def _parse_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _parse_json_map(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _template_out(record: ContainerTemplateTable) -> ContainerTemplate:
    return ContainerTemplate(
        id=record.id,
        template_key=str(getattr(record, "template_key", record.id) or record.id),
        version=max(1, int(getattr(record, "version", 1) or 1)),
        is_default=bool(getattr(record, "is_default", True)),
        name=record.name,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
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
        network_mode=str(getattr(record, "network_mode", "bridge") or "bridge"),
        run_as_non_root=bool(getattr(record, "run_as_non_root", False)),
        read_only_root_filesystem=bool(getattr(record, "read_only_root_filesystem", False)),
        command=record.command,
        args=_parse_json_list(record.args_json),
        env=_parse_json_map(record.env_json),
        auto_delete_minutes=record.auto_delete_minutes,
        idle_timeout_minutes=max(
            1,
            int(
                getattr(record, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes
            ),
        ),
        max_active_instances=max(0, int(getattr(record, "max_active_instances", 2) or 0)),
        enabled=record.enabled,
        created_at=record.created_at,
    )


def _instance_out(record: ContainerInstanceTable) -> ContainerInstanceView:
    return ContainerInstanceView(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        tenant=normalize_tenant(getattr(record, "tenant", None), default="default"),
        namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        status=str(record.status or "unknown"),
        status_stage=None,
        status_detail=None,
        pod_name=record.pod_name,
        access_url=None,
        container_port=None,
        queue_attempts=max(0, int(getattr(record, "queue_attempts", 0) or 0)),
        queue_not_before=getattr(record, "queue_not_before", None),
        queue_reason=getattr(record, "queue_reason", None),
        launch_diagnostics=[],
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


@router.get(
    "/containers",
    response_model=list[ContainerInstanceView],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_container_instances(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ContainerInstanceView]:
    stmt = select(ContainerInstanceTable)
    scope = _tenant_scope_for_actor(actor, include_global=False)
    if scope is not None:
        stmt = stmt.where(ContainerInstanceTable.tenant.in_(scope))
    rows = session.exec(stmt).all()
    rows.sort(key=lambda item: item.started_at, reverse=True)
    return [_instance_out(row) for row in rows]


@router.post(
    "/containers/{instance_id}/stop",
    response_model=ContainerInstanceView,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def stop_container_instance(
    instance_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))

    if record.status != "queued":
        kube.stop_container_pod(
            record.id,
            record.owner,
            namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        )
        try:
            kube.delete_container_service(
                record.id, namespace=str(getattr(record, "namespace", "") or settings.kube_namespace)
            )
        except Exception:
            pass
    record.status = "stopped"
    record.queue_not_before = None
    record.queue_reason = None
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    return _instance_out(record)


@router.delete(
    "/containers/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def delete_container_instance(
    instance_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))

    namespace = str(getattr(record, "namespace", "") or settings.kube_namespace)
    kube.delete_container_pod(record.id, record.owner, namespace=namespace)
    try:
        kube.delete_container_service(record.id, namespace=namespace)
    except Exception:
        pass
    session.delete(record)
    session.commit()


@router.post(
    "/container-images",
    response_model=ContainerImageMeta,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def create_container_image(
    payload: ContainerImageCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerImageMeta:
    resource_tenant = resolve_resource_tenant(actor, payload.tenant)
    image_ref = _normalize_container_image_ref(payload.image_ref)
    _enforce_registry_policy(image_ref)
    signature_warning = _verify_image_signature(image_ref)
    existing = session.exec(
        select(ContainerImageTable)
        .where(ContainerImageTable.image_ref == image_ref)
        .where(ContainerImageTable.tenant == resource_tenant)
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container image already exists")

    name = (payload.name or "").strip() or image_ref
    record = ContainerImageTable(
        id=str(uuid4()),
        name=name,
        image_ref=image_ref,
        tenant=resource_tenant,
        last_scan_at=utc_now() if settings.container_scan_enabled else None,
        last_scan_status="queued" if settings.container_scan_enabled else "never",
        last_scan_summary="scan queued" if settings.container_scan_enabled else "",
        created_at=utc_now(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    if settings.container_image_prepull_enabled:
        background_tasks.add_task(_run_image_prepull, image_ref)
    if settings.container_scan_enabled:
        background_tasks.add_task(_run_image_scan_for_id, record.id)
    return _image_out(record, signature_warning=signature_warning)


@router.get(
    "/container-images",
    response_model=list[ContainerImageMeta],
    dependencies=[Depends(require_permission(Permission.IMAGES_READ))],
)
def list_container_images(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ContainerImageMeta]:
    stmt = select(ContainerImageTable)
    scope = _tenant_scope_for_actor(actor, include_global=True)
    if scope is not None:
        stmt = stmt.where(ContainerImageTable.tenant.in_(scope))
    rows = session.exec(stmt).all()
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return [_image_out(row) for row in rows]


@router.patch(
    "/container-images/{image_id}",
    response_model=ContainerImageMeta,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def update_container_image(
    image_id: str,
    payload: ContainerImageUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerImageMeta:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    next_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    if payload.tenant is not None:
        next_tenant = assert_actor_can_manage_tenant(actor, payload.tenant)

    updates = payload.model_dump(exclude_unset=True)
    signature_warning = None
    if "name" in updates:
        name = (updates.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
        record.name = name
    if "image_ref" in updates:
        image_ref = _normalize_container_image_ref(str(updates.get("image_ref") or ""))
        _enforce_registry_policy(image_ref)
        signature_warning = _verify_image_signature(image_ref)
        existing = session.exec(
            select(ContainerImageTable)
            .where(ContainerImageTable.image_ref == image_ref)
            .where(ContainerImageTable.id != image_id)
            .where(ContainerImageTable.tenant == next_tenant)
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container image already exists")
        record.image_ref = image_ref
        if settings.container_scan_enabled:
            record.last_scan_at = utc_now()
            record.last_scan_status = "queued"
            record.last_scan_summary = "scan queued"
    record.tenant = next_tenant

    session.add(record)
    session.commit()
    session.refresh(record)
    if "image_ref" in updates:
        if settings.container_image_prepull_enabled:
            background_tasks.add_task(_run_image_prepull, record.image_ref)
        if settings.container_scan_enabled:
            background_tasks.add_task(_run_image_scan_for_id, image_id)
    return _image_out(record, signature_warning=signature_warning)


@router.delete(
    "/container-images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def delete_container_image(
    image_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))

    template_refs = session.exec(
        select(ContainerTemplateTable).where(ContainerTemplateTable.container_image_id == image_id)
    ).all()
    if template_refs:
        template_ids = [row.id for row in template_refs]
        active_instance = session.exec(
            select(ContainerInstanceTable)
            .where(ContainerInstanceTable.template_id.in_(template_ids))
            .where(ContainerInstanceTable.status.in_(["queued", "pending", "running"]))
        ).first()
        if active_instance:
            names = sorted({(row.name or row.id).strip() for row in template_refs})
            sample = ", ".join(names[:3])
            if len(names) > 3:
                sample = f"{sample}, +{len(names) - 3} more"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"container image is still used by active container templates: {sample}",
            )

        for instance in session.exec(
            select(ContainerInstanceTable).where(ContainerInstanceTable.template_id.in_(template_ids))
        ).all():
            session.delete(instance)
        for template in template_refs:
            session.delete(template)

    session.delete(record)
    session.commit()


@router.post(
    "/container-images/{image_id}/prepull",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def prepull_container_image(
    image_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> dict[str, str]:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    background_tasks.add_task(_run_image_prepull, record.image_ref)
    return {"detail": f"Pre-pull queued for {record.image_ref}"}


@router.post(
    "/container-images/{image_id}/scan",
    response_model=ContainerImageMeta,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def scan_container_image(
    image_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerImageMeta:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    record.last_scan_at = utc_now()
    record.last_scan_status = "queued"
    record.last_scan_summary = "scan queued"
    session.add(record)
    session.commit()
    session.refresh(record)
    background_tasks.add_task(_run_image_scan_for_id, image_id)
    return _image_out(record)


@router.post(
    "/container-templates",
    response_model=ContainerTemplate,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def create_container_template(
    payload: ContainerTemplateCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerTemplate:
    resource_tenant = resolve_resource_tenant(actor, payload.tenant)
    image = session.get(ContainerImageTable, payload.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {resource_tenant, GLOBAL_TENANT}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"container template tenant {resource_tenant} cannot use image tenant {image_tenant}",
        )

    args = [str(item).strip() for item in (payload.args or []) if str(item).strip()]
    env = _validate_env(payload.env or {})
    healthcheck_path = _normalize_http_path(payload.healthcheck_path) or "/"
    readiness_success_path = _normalize_http_path(payload.readiness_success_path, allow_blank=True)
    dependency_checks = [ContainerDependencyCheck.model_validate(item) for item in (payload.dependency_checks or [])]
    template_key = _normalize_template_key(f"ct-{uuid4().hex[:12]}")

    record = ContainerTemplateTable(
        id=str(uuid4()),
        template_key=template_key,
        version=1,
        is_default=True,
        name=(payload.name or "").strip(),
        tenant=resource_tenant,
        description=(payload.description or "").strip(),
        container_image_id=payload.container_image_id,
        cpu_millicores=payload.cpu_millicores,
        memory_mb=payload.memory_mb,
        container_port=payload.container_port,
        healthcheck_protocol=(payload.healthcheck_protocol or "tcp").lower(),
        healthcheck_path=healthcheck_path,
        readiness_http_status=max(100, min(599, int(payload.readiness_http_status or 200))),
        readiness_success_path=readiness_success_path,
        startup_timeout_seconds=max(10, int(payload.startup_timeout_seconds or 300)),
        dependency_checks_json=_serialize_dependency_checks(dependency_checks),
        expose_strategy=(payload.expose_strategy or "nodeport").lower(),
        network_mode=_normalize_container_network_mode(payload.network_mode),
        run_as_non_root=bool(payload.run_as_non_root),
        read_only_root_filesystem=bool(payload.read_only_root_filesystem),
        command=_normalize_optional_command(payload.command),
        args_json=json.dumps(args, separators=(",", ":")),
        env_json=json.dumps(env, separators=(",", ":")),
        auto_delete_minutes=payload.auto_delete_minutes,
        idle_timeout_minutes=max(1, int(payload.idle_timeout_minutes or settings.idle_timeout_minutes)),
        max_active_instances=max(0, int(payload.max_active_instances or 0)),
        enabled=payload.enabled,
        created_at=utc_now(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    if record.enabled:
        background_tasks.add_task(_run_image_prepull, image.image_ref)
    return _template_out(record)


@router.get(
    "/container-templates",
    response_model=list[ContainerTemplate],
    dependencies=[Depends(require_permission(Permission.TEMPLATES_READ))],
)
def list_container_templates(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ContainerTemplate]:
    stmt = select(ContainerTemplateTable).where(ContainerTemplateTable.is_default == True)  # noqa: E712
    scope = _tenant_scope_for_actor(actor, include_global=True)
    if scope is not None:
        stmt = stmt.where(ContainerTemplateTable.tenant.in_(scope))
    rows = session.exec(stmt).all()
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return [_template_out(row) for row in rows]


@router.patch(
    "/container-templates/{template_id}",
    response_model=ContainerTemplate,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def update_container_template(
    template_id: str,
    payload: ContainerTemplateUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ContainerTemplate:
    record = session.get(ContainerTemplateTable, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found")
    next_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _template_out(record)
    if "tenant" in updates:
        next_tenant = assert_actor_can_manage_tenant(actor, updates.get("tenant"))

    was_enabled = bool(record.enabled)
    previous_image_id = str(record.container_image_id or "").strip()
    image_id = str(updates.get("container_image_id") or previous_image_id).strip()
    if not image_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="container_image_id is required")
    image_changed = "container_image_id" in updates and image_id != previous_image_id
    image = None
    if image_changed:
        image = session.get(ContainerImageTable, image_id)
        if not image:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
        image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
        if image_tenant not in {next_tenant, GLOBAL_TENANT}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"container template tenant {next_tenant} cannot use image tenant {image_tenant}",
            )
        _enforce_registry_policy(image.image_ref)
        _verify_image_signature(image.image_ref)

    if "name" in updates:
        record.name = str(updates.get("name") or "").strip()
    if "description" in updates:
        record.description = str(updates.get("description") or "").strip()
    if "container_image_id" in updates:
        record.container_image_id = image_id
    if "cpu_millicores" in updates:
        record.cpu_millicores = max(50, int(updates.get("cpu_millicores") or 0))
    if "memory_mb" in updates:
        record.memory_mb = max(64, int(updates.get("memory_mb") or 0))
    if "container_port" in updates:
        record.container_port = max(1, int(updates.get("container_port") or 0))
    if "healthcheck_protocol" in updates:
        record.healthcheck_protocol = str(updates.get("healthcheck_protocol") or "tcp").strip().lower()
    if "healthcheck_path" in updates:
        record.healthcheck_path = _normalize_http_path(str(updates.get("healthcheck_path") or "/")) or "/"
    if "readiness_http_status" in updates:
        record.readiness_http_status = max(100, min(599, int(updates.get("readiness_http_status") or 200)))
    if "readiness_success_path" in updates:
        record.readiness_success_path = _normalize_http_path(updates.get("readiness_success_path"), allow_blank=True)
    if "startup_timeout_seconds" in updates:
        record.startup_timeout_seconds = max(10, int(updates.get("startup_timeout_seconds") or 300))
    if "dependency_checks" in updates:
        dependency_checks = [
            ContainerDependencyCheck.model_validate(item) for item in (updates.get("dependency_checks") or [])
        ]
        record.dependency_checks_json = _serialize_dependency_checks(dependency_checks)
    if "expose_strategy" in updates:
        record.expose_strategy = str(updates.get("expose_strategy") or "nodeport").strip().lower()
    if "network_mode" in updates:
        record.network_mode = _normalize_container_network_mode(updates.get("network_mode"))
    if "run_as_non_root" in updates:
        record.run_as_non_root = bool(updates.get("run_as_non_root"))
    if "read_only_root_filesystem" in updates:
        record.read_only_root_filesystem = bool(updates.get("read_only_root_filesystem"))
    if "command" in updates:
        record.command = _normalize_optional_command(updates.get("command"))
    if "args" in updates:
        args = [str(item).strip() for item in (updates.get("args") or []) if str(item).strip()]
        record.args_json = json.dumps(args, separators=(",", ":"))
    if "env" in updates:
        record.env_json = json.dumps(_validate_env(updates.get("env") or {}), separators=(",", ":"))
    if "auto_delete_minutes" in updates:
        record.auto_delete_minutes = max(1, int(updates.get("auto_delete_minutes") or 60))
    if "idle_timeout_minutes" in updates:
        record.idle_timeout_minutes = max(1, int(updates.get("idle_timeout_minutes") or settings.idle_timeout_minutes))
    if "max_active_instances" in updates:
        record.max_active_instances = max(0, int(updates.get("max_active_instances") or 0))
    if "enabled" in updates:
        record.enabled = bool(updates.get("enabled"))
    if "is_default" in updates:
        # Versioning is disabled; keep this as an accepted no-op for old clients.
        record.is_default = True
    if "tenant" in updates and not image_changed:
        image_for_tenant = session.get(ContainerImageTable, record.container_image_id)
        image_tenant = normalize_tenant(getattr(image_for_tenant, "tenant", None), default=GLOBAL_TENANT)
        if image_tenant not in {next_tenant, GLOBAL_TENANT}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"container template tenant {next_tenant} cannot use image tenant {image_tenant}",
            )
    record.tenant = next_tenant

    session.add(record)
    session.commit()
    session.refresh(record)
    should_prepull = bool(record.enabled) and (image_changed or not was_enabled)
    if should_prepull:
        if image is None:
            image = session.get(ContainerImageTable, record.container_image_id)
        if image:
            background_tasks.add_task(_run_image_prepull, image.image_ref)
    return _template_out(record)


@router.delete(
    "/container-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def delete_container_template(
    template_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(ContainerTemplateTable, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found")
    assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))

    template_key = str(getattr(record, "template_key", "") or "").strip()
    if template_key:
        template_rows = session.exec(
            select(ContainerTemplateTable).where(ContainerTemplateTable.template_key == template_key)
        ).all()
    else:
        template_rows = [record]
    template_ids = [row.id for row in template_rows]

    active_instance = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.template_id.in_(template_ids))
        .where(ContainerInstanceTable.status.in_(["queued", "pending", "running"]))
    ).first()
    if active_instance:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="container template has active container instances",
        )

    for instance in session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.template_id.in_(template_ids))
    ).all():
        session.delete(instance)
    for row in template_rows:
        session.delete(row)
    session.commit()
