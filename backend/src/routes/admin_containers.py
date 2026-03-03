import json
import re
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..auth import require_admin
from ..db import get_session
from ..models import (
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

router = APIRouter(dependencies=[Depends(require_admin)])

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _normalize_container_image_ref(value: str) -> str:
    ref = (value or "").strip()
    if not ref:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_ref is required")
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


def _image_out(record: ContainerImageTable) -> ContainerImageMeta:
    return ContainerImageMeta(
        id=record.id,
        name=record.name,
        image_ref=record.image_ref,
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
        name=record.name,
        description=record.description,
        container_image_id=record.container_image_id,
        cpu_millicores=record.cpu_millicores,
        memory_mb=record.memory_mb,
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
    record = ContainerTemplateTable(
        id=str(uuid4()),
        name=(payload.name or "").strip(),
        description=(payload.description or "").strip(),
        container_image_id=payload.container_image_id,
        cpu_millicores=payload.cpu_millicores,
        memory_mb=payload.memory_mb,
        command=(payload.command or "").strip() or None,
        args_json=json.dumps(args, separators=(",", ":")),
        env_json=json.dumps(env, separators=(",", ":")),
        auto_delete_minutes=payload.auto_delete_minutes,
        enabled=payload.enabled,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return _template_out(record)


@router.get("/container-templates", response_model=list[ContainerTemplate])
def list_container_templates(session: Session = Depends(get_session)) -> list[ContainerTemplate]:
    rows = session.exec(select(ContainerTemplateTable)).all()
    rows.sort(key=lambda item: item.created_at, reverse=True)
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
    if "container_image_id" in updates:
        image_id = str(updates.get("container_image_id") or "").strip()
        if not image_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="container_image_id is required")
        image = session.get(ContainerImageTable, image_id)
        if not image:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image not found")
        record.container_image_id = image_id

    if "name" in updates:
        name = str(updates.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
        record.name = name
    if "description" in updates:
        record.description = str(updates.get("description") or "").strip()
    if "cpu_millicores" in updates:
        record.cpu_millicores = int(updates.get("cpu_millicores") or record.cpu_millicores)
    if "memory_mb" in updates:
        record.memory_mb = int(updates.get("memory_mb") or record.memory_mb)
    if "command" in updates:
        record.command = (str(updates.get("command") or "").strip() or None)
    if "args" in updates:
        args = [str(item).strip() for item in (updates.get("args") or []) if str(item).strip()]
        record.args_json = json.dumps(args, separators=(",", ":"))
    if "env" in updates:
        env = _validate_env(updates.get("env") or {})
        record.env_json = json.dumps(env, separators=(",", ":"))
    if "auto_delete_minutes" in updates:
        record.auto_delete_minutes = int(updates.get("auto_delete_minutes") or record.auto_delete_minutes)
    if "enabled" in updates:
        record.enabled = bool(updates.get("enabled"))

    session.add(record)
    session.commit()
    session.refresh(record)
    return _template_out(record)


@router.delete("/container-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_container_template(template_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(ContainerTemplateTable, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found")

    active_instance = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.template_id == template_id)
        .where(ContainerInstanceTable.status.in_(["pending", "running"]))
    ).first()
    if active_instance:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="container template has active container instances",
        )

    for instance in session.exec(select(ContainerInstanceTable).where(ContainerInstanceTable.template_id == template_id)).all():
        session.delete(instance)
    session.delete(record)
    session.commit()
