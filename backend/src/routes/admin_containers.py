import json
import logging
import re
import subprocess
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..auth import require_admin
from ..config import settings
from ..db import get_session
from ..models import (
    ContainerDependencyCheck,
    ContainerImageCreate,
    ContainerImageMeta,
    ContainerImageUpdate,
    ContainerTemplate,
    ContainerTemplateCreate,
    ContainerTemplateUpdate,
)
from ..tables import ContainerImage as ContainerImageTable
from ..tables import ContainerInstance as ContainerInstanceTable
from ..tables import ContainerTemplate as ContainerTemplateTable
from ..services.kubernetes import kube

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_TEMPLATE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


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
        return
    registry = _registry_from_ref(image_ref)
    if registry not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"registry {registry or 'unknown'} is not allowed by policy",
        )


def _verify_image_signature(image_ref: str) -> None:
    if not settings.container_signature_verification_enabled:
        return
    cmd = ["cosign", "verify"]
    key_ref = (settings.container_signature_key_ref or "").strip()
    if key_ref:
        cmd.extend(["--key", key_ref])
    else:
        cmd.append("--keyless")
    cmd.append(image_ref)
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cosign is required for signature verification but is not installed",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="image signature verification timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "signature verification failed").strip()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail[:500])


def _normalize_template_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower()).strip("-")
    if not key:
        key = f"ct-{uuid4().hex[:12]}"
    if len(key) > 64:
        key = key[:64].rstrip("-")
    if not _TEMPLATE_KEY_RE.match(key):
        key = f"ct-{uuid4().hex[:12]}"
    return key


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
        record.last_scan_at = datetime.utcnow()
        record.last_scan_status = "error"
        record.last_scan_summary = "scan failed"
        session.add(record)
        session.commit()
        session.refresh(record)
        return
    record.last_scan_at = datetime.utcnow()
    record.last_scan_status = status_text
    record.last_scan_summary = summary_text[:512]
    session.add(record)
    session.commit()
    session.refresh(record)


def _image_out(record: ContainerImageTable) -> ContainerImageMeta:
    return ContainerImageMeta(
        id=record.id,
        name=record.name,
        image_ref=record.image_ref,
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
        args=_parse_json_list(record.args_json),
        env=_parse_json_map(record.env_json),
        auto_delete_minutes=record.auto_delete_minutes,
        enabled=record.enabled,
        created_at=record.created_at,
    )


@router.post("/container-images", response_model=ContainerImageMeta, status_code=status.HTTP_201_CREATED)
def create_container_image(payload: ContainerImageCreate, session: Session = Depends(get_session)) -> ContainerImageMeta:
    image_ref = _normalize_container_image_ref(payload.image_ref)
    _enforce_registry_policy(image_ref)
    _verify_image_signature(image_ref)
    existing = session.exec(select(ContainerImageTable).where(ContainerImageTable.image_ref == image_ref)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container image already exists")

    name = (payload.name or "").strip() or image_ref
    record = ContainerImageTable(
        id=str(uuid4()),
        name=name,
        image_ref=image_ref,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    try:
        kube.prepull_container_image(image_ref)
    except Exception:
        logger.warning("Container image pre-pull failed for %s", image_ref, exc_info=True)
    _trigger_image_scan(session, record)
    return _image_out(record)


@router.get("/container-images", response_model=list[ContainerImageMeta])
def list_container_images(session: Session = Depends(get_session)) -> list[ContainerImageMeta]:
    rows = session.exec(select(ContainerImageTable)).all()
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return [_image_out(row) for row in rows]


@router.patch("/container-images/{image_id}", response_model=ContainerImageMeta)
def update_container_image(
    image_id: str,
    payload: ContainerImageUpdate,
    session: Session = Depends(get_session),
) -> ContainerImageMeta:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")

    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        name = (updates.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
        record.name = name
    if "image_ref" in updates:
        image_ref = _normalize_container_image_ref(str(updates.get("image_ref") or ""))
        _enforce_registry_policy(image_ref)
        _verify_image_signature(image_ref)
        existing = session.exec(
            select(ContainerImageTable)
            .where(ContainerImageTable.image_ref == image_ref)
            .where(ContainerImageTable.id != image_id)
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container image already exists")
        record.image_ref = image_ref

    session.add(record)
    session.commit()
    session.refresh(record)
    if "image_ref" in updates:
        try:
            kube.prepull_container_image(record.image_ref)
        except Exception:
            logger.warning("Container image pre-pull failed for %s", record.image_ref, exc_info=True)
    _trigger_image_scan(session, record)
    return _image_out(record)


@router.delete("/container-images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_container_image(image_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")

    template_ref = session.exec(
        select(ContainerTemplateTable).where(ContainerTemplateTable.container_image_id == image_id)
    ).first()
    if template_ref:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="container image is still used by one or more container templates",
        )

    session.delete(record)
    session.commit()


@router.post("/container-images/{image_id}/prepull", status_code=status.HTTP_202_ACCEPTED)
def prepull_container_image(image_id: str, session: Session = Depends(get_session)) -> dict[str, str]:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    kube.prepull_container_image(record.image_ref)
    return {"detail": f"Pre-pull triggered for {record.image_ref}"}


@router.post("/container-images/{image_id}/scan", response_model=ContainerImageMeta)
def scan_container_image(image_id: str, session: Session = Depends(get_session)) -> ContainerImageMeta:
    record = session.get(ContainerImageTable, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
    _trigger_image_scan(session, record)
    return _image_out(record)


@router.post("/container-templates", response_model=ContainerTemplate, status_code=status.HTTP_201_CREATED)
def create_container_template(
    payload: ContainerTemplateCreate,
    session: Session = Depends(get_session),
) -> ContainerTemplate:
    image = session.get(ContainerImageTable, payload.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")

    args = [str(item).strip() for item in (payload.args or []) if str(item).strip()]
    env = _validate_env(payload.env or {})
    healthcheck_path = _normalize_http_path(payload.healthcheck_path) or "/"
    readiness_success_path = _normalize_http_path(payload.readiness_success_path, allow_blank=True)
    dependency_checks = [ContainerDependencyCheck.model_validate(item) for item in (payload.dependency_checks or [])]

    if payload.template_key:
        template_key = _normalize_template_key(payload.template_key)
    else:
        template_key = _normalize_template_key(f"ct-{uuid4().hex[:12]}")
    existing_versions = session.exec(
        select(ContainerTemplateTable).where(ContainerTemplateTable.template_key == template_key)
    ).all()
    next_version = max([max(1, int(getattr(row, "version", 1) or 1)) for row in existing_versions], default=0) + 1

    record = ContainerTemplateTable(
        id=str(uuid4()),
        template_key=template_key,
        version=next_version,
        is_default=True,
        name=(payload.name or "").strip(),
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
        run_as_non_root=bool(payload.run_as_non_root),
        read_only_root_filesystem=bool(payload.read_only_root_filesystem),
        command=(payload.command or "").strip() or None,
        args_json=json.dumps(args, separators=(",", ":")),
        env_json=json.dumps(env, separators=(",", ":")),
        auto_delete_minutes=payload.auto_delete_minutes,
        enabled=payload.enabled,
        created_at=datetime.utcnow(),
    )
    for row in existing_versions:
        if bool(getattr(row, "is_default", False)):
            row.is_default = False
            session.add(row)
    session.add(record)
    session.commit()
    session.refresh(record)
    if record.enabled:
        try:
            kube.prepull_container_image(image.image_ref)
        except Exception:
            logger.warning("Container image pre-pull failed for %s", image.image_ref, exc_info=True)
    return _template_out(record)


@router.get("/container-templates", response_model=list[ContainerTemplate])
def list_container_templates(session: Session = Depends(get_session)) -> list[ContainerTemplate]:
    rows = session.exec(select(ContainerTemplateTable)).all()
    rows.sort(
        key=lambda item: (
            str(getattr(item, "template_key", item.id)),
            -max(1, int(getattr(item, "version", 1) or 1)),
            item.created_at,
        ),
        reverse=False,
    )
    return [_template_out(row) for row in rows]


@router.patch("/container-templates/{template_id}", response_model=ContainerTemplate)
def update_container_template(
    template_id: str,
    payload: ContainerTemplateUpdate,
    session: Session = Depends(get_session),
) -> ContainerTemplate:
    record = session.get(ContainerTemplateTable, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _template_out(record)

    mutable_fields = {
        "template_key",
        "name",
        "description",
        "container_image_id",
        "cpu_millicores",
        "memory_mb",
        "container_port",
        "healthcheck_protocol",
        "healthcheck_path",
        "readiness_http_status",
        "readiness_success_path",
        "startup_timeout_seconds",
        "dependency_checks",
        "expose_strategy",
        "run_as_non_root",
        "read_only_root_filesystem",
        "command",
        "args",
        "env",
        "auto_delete_minutes",
    }
    metadata_only = set(updates).issubset({"enabled", "is_default"})
    if not metadata_only and set(updates).intersection(mutable_fields):
        image_id = str(updates.get("container_image_id") or record.container_image_id).strip()
        if not image_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="container_image_id is required")
        image = session.get(ContainerImageTable, image_id)
        if not image:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
        _enforce_registry_policy(image.image_ref)
        _verify_image_signature(image.image_ref)

        template_key = _normalize_template_key(str(updates.get("template_key") or record.template_key or record.id))
        same_key_rows = session.exec(
            select(ContainerTemplateTable).where(ContainerTemplateTable.template_key == template_key)
        ).all()
        next_version = max([max(1, int(getattr(row, "version", 1) or 1)) for row in same_key_rows], default=0) + 1

        args = (
            [str(item).strip() for item in (updates.get("args") or []) if str(item).strip()]
            if "args" in updates
            else _parse_json_list(record.args_json)
        )
        env = _validate_env(updates.get("env") or {}) if "env" in updates else _parse_json_map(record.env_json)
        dependency_checks = (
            [ContainerDependencyCheck.model_validate(item) for item in (updates.get("dependency_checks") or [])]
            if "dependency_checks" in updates
            else _parse_dependency_checks(getattr(record, "dependency_checks_json", "[]"))
        )
        healthcheck_path = (
            _normalize_http_path(str(updates.get("healthcheck_path") or "/")) if "healthcheck_path" in updates else record.healthcheck_path
        ) or "/"
        readiness_success_path = (
            _normalize_http_path(updates.get("readiness_success_path"), allow_blank=True)
            if "readiness_success_path" in updates
            else _normalize_http_path(getattr(record, "readiness_success_path", None), allow_blank=True)
        )
        new_is_default = bool(updates.get("is_default", True))
        new_record = ContainerTemplateTable(
            id=str(uuid4()),
            template_key=template_key,
            version=next_version,
            is_default=new_is_default,
            name=str(updates.get("name") or record.name).strip(),
            description=str(updates.get("description") if "description" in updates else record.description or "").strip(),
            container_image_id=image_id,
            cpu_millicores=max(50, int(updates.get("cpu_millicores") or record.cpu_millicores)),
            memory_mb=max(64, int(updates.get("memory_mb") or record.memory_mb)),
            container_port=max(1, int(updates.get("container_port") or record.container_port)),
            healthcheck_protocol=str(updates.get("healthcheck_protocol") or record.healthcheck_protocol or "tcp").strip().lower(),
            healthcheck_path=healthcheck_path,
            readiness_http_status=max(100, min(599, int(updates.get("readiness_http_status") or getattr(record, "readiness_http_status", 200) or 200))),
            readiness_success_path=readiness_success_path,
            startup_timeout_seconds=max(10, int(updates.get("startup_timeout_seconds") or record.startup_timeout_seconds or 300)),
            dependency_checks_json=_serialize_dependency_checks(dependency_checks),
            expose_strategy=str(updates.get("expose_strategy") or record.expose_strategy or "nodeport").strip().lower(),
            run_as_non_root=bool(updates.get("run_as_non_root", record.run_as_non_root)),
            read_only_root_filesystem=bool(updates.get("read_only_root_filesystem", record.read_only_root_filesystem)),
            command=(str(updates.get("command")) if "command" in updates else (record.command or "")).strip() or None,
            args_json=json.dumps(args, separators=(",", ":")),
            env_json=json.dumps(env, separators=(",", ":")),
            auto_delete_minutes=max(1, int(updates.get("auto_delete_minutes") or record.auto_delete_minutes)),
            enabled=bool(updates.get("enabled", record.enabled)),
            created_at=datetime.utcnow(),
        )
        if new_is_default:
            for row in same_key_rows:
                if bool(getattr(row, "is_default", False)):
                    row.is_default = False
                    session.add(row)
        session.add(new_record)
        session.commit()
        session.refresh(new_record)
        if new_record.enabled:
            try:
                kube.prepull_container_image(image.image_ref)
            except Exception:
                logger.warning("Container image pre-pull failed for %s", image.image_ref, exc_info=True)
        return _template_out(new_record)

    if "enabled" in updates:
        record.enabled = bool(updates.get("enabled"))
    if "is_default" in updates:
        desired_default = bool(updates.get("is_default"))
        if desired_default:
            siblings = session.exec(
                select(ContainerTemplateTable).where(ContainerTemplateTable.template_key == record.template_key)
            ).all()
            for row in siblings:
                row.is_default = (row.id == record.id)
                session.add(row)
        else:
            sibling_default = session.exec(
                select(ContainerTemplateTable)
                .where(ContainerTemplateTable.template_key == record.template_key)
                .where(ContainerTemplateTable.id != record.id)
                .where(ContainerTemplateTable.is_default == True)  # noqa: E712
            ).first()
            if not sibling_default:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="cannot unset default without promoting another version first",
                )
            record.is_default = False
    session.add(record)
    session.commit()
    session.refresh(record)
    if record.enabled:
        image = session.get(ContainerImageTable, record.container_image_id)
        if image:
            try:
                kube.prepull_container_image(image.image_ref)
            except Exception:
                logger.warning("Container image pre-pull failed for %s", image.image_ref, exc_info=True)
    return _template_out(record)


@router.delete("/container-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_container_template(template_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(ContainerTemplateTable, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found")

    active_instance = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.template_id == template_id)
        .where(ContainerInstanceTable.status.in_(["queued", "pending", "running"]))
    ).first()
    if active_instance:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="container template has active container instances",
        )

    template_key = str(getattr(record, "template_key", "") or "")
    was_default = bool(getattr(record, "is_default", False))
    for instance in session.exec(select(ContainerInstanceTable).where(ContainerInstanceTable.template_id == template_id)).all():
        session.delete(instance)
    session.delete(record)
    session.commit()
    if template_key and was_default:
        replacement = session.exec(
            select(ContainerTemplateTable)
            .where(ContainerTemplateTable.template_key == template_key)
            .order_by(ContainerTemplateTable.version.desc(), ContainerTemplateTable.created_at.desc())
        ).first()
        if replacement:
            replacement.is_default = True
            session.add(replacement)
            session.commit()
