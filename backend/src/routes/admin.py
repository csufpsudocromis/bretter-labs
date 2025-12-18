import hashlib
import logging
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session, select
from kubernetes.utils import parse_quantity

from ..auth import hash_password, require_admin, revoke_tokens
from ..config import settings
from ..db import get_session
from ..models import (
    ConcurrencySettings,
    IdleTimeoutSettings,
    ImageCreateResponse,
    ImageMeta,
    RuntimeSettingsRead,
    SiteSettings,
    SSOSettings,
    TemplateToggle,
    UserCreate,
    UserOut,
    UserPasswordUpdate,
    UserUpdate,
    VMInstance,
    VMTemplate,
    VMTemplateCreate,
    VMTemplateUpdate,
)
from ..services.kubernetes import kube
from ..tables import Config, Image, Instance, Template, User

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)

IMAGE_DIR = Path(settings.storage_root)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024 * 1024  # 60 GB
ALLOWED_SUFFIXES = {".vhd", ".qcow", ".qcow2", ".vdi"}

PVC_HELPER_IMAGE = "alpine:3.19"
POD_READY_WAIT_SECONDS = 120
POD_READY_SLEEP = 2


def _ensure_config_columns() -> None:
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(config)")}
        to_add = []
        if "site_title" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN site_title TEXT DEFAULT 'Bretter Labs'")
        if "site_tagline" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN site_tagline TEXT DEFAULT 'Run Virtual Labs and Software'")
        if "theme_bg_color" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_bg_color TEXT DEFAULT '#f5f5f5'")
        if "theme_text_color" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_text_color TEXT DEFAULT '#111111'")
        if "theme_button_color" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_button_color TEXT DEFAULT '#2563eb'")
        if "theme_button_text_color" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_button_text_color TEXT DEFAULT '#ffffff'")
        if "theme_bg_image" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_bg_image TEXT DEFAULT ''")
        if "theme_tile_bg" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_tile_bg TEXT DEFAULT '#f8fafc'")
        if "theme_tile_border" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_tile_border TEXT DEFAULT '#e2e8f0'")
        if "theme_tile_opacity" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_tile_opacity REAL DEFAULT 1.0")
        if "theme_tile_border_opacity" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_tile_border_opacity REAL DEFAULT 1.0")
        if "sso_enabled" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_enabled BOOLEAN DEFAULT 0")
        if "sso_provider" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_provider TEXT DEFAULT ''")
        if "sso_client_id" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_client_id TEXT DEFAULT ''")
        if "sso_client_secret" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_client_secret TEXT DEFAULT ''")
        if "sso_authorize_url" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_authorize_url TEXT DEFAULT ''")
        if "sso_token_url" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_token_url TEXT DEFAULT ''")
        if "sso_userinfo_url" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_userinfo_url TEXT DEFAULT ''")
        if "sso_redirect_url" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_redirect_url TEXT DEFAULT ''")
        for stmt in to_add:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass
        if to_add:
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure config columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


_ensure_config_columns()


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(msg)
    return result


def _with_pvc_helper(command: list[str], *, image: str | None = None, capture_output: bool = True) -> subprocess.CompletedProcess:
    helper = f"image-sync-{uuid4().hex[:8]}"
    helper_image = image or PVC_HELPER_IMAGE
    pod_spec = (
        '{"spec":{"volumes":[{"name":"images","persistentVolumeClaim":{"claimName":"'
        f'{settings.kube_image_pvc}'
        '"}}],"containers":[{"name":"worker","image":"'
        f'{helper_image}'
        '","command":["/bin/sh","-c","sleep 3600"],'
        '"volumeMounts":[{"name":"images","mountPath":"/images"}]}],'
        '"restartPolicy":"Never"}}'
    )
    try:
        _run(
            [
                "kubectl",
                "run",
                helper,
                "-n",
                settings.kube_namespace,
                "--restart=Never",
                "--image",
                PVC_HELPER_IMAGE,
                "--overrides",
                pod_spec,
                "--command",
                "--",
                "sleep",
                "3600",
            ]
        )
        deadline = time.time() + POD_READY_WAIT_SECONDS
        while time.time() < deadline:
            phase = (
                _run(
                    [
                        "kubectl",
                        "get",
                        "pod",
                        helper,
                        "-n",
                        settings.kube_namespace,
                        "-o",
                        "jsonpath={.status.phase}",
                    ],
                    check=False,
                ).stdout.strip()
            )
            if phase.lower() in {"running", "succeeded"}:
                break
            if phase.lower() in {"failed", "unknown"}:
                raise RuntimeError(f"helper pod failed to start (phase={phase})")
            time.sleep(POD_READY_SLEEP)
        else:
            raise RuntimeError("timed out waiting for helper pod")
        return _run(
            ["kubectl", "exec", "-n", settings.kube_namespace, helper, "--request-timeout=0", "--"] + command,
            capture=capture_output,
        )
    finally:
        _run(["kubectl", "delete", "pod", helper, "-n", settings.kube_namespace, "--ignore-not-found=true"], check=False)


def _copy_file_to_pvc(source_path: Path, filename: str) -> None:
    """
    Copy the uploaded file into the PVC by spinning a short-lived helper pod and streaming via kubectl exec.
    Retries on broken pipes by resuming from the last written offset.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"source file not found: {source_path}")
    helper = f"image-sync-{uuid4().hex[:8]}"
    pod_spec = (
        '{"spec":{"volumes":[{"name":"images","persistentVolumeClaim":{"claimName":"'
        f'{settings.kube_image_pvc}'
        '"}}],"containers":[{"name":"worker","image":"'
        f'{PVC_HELPER_IMAGE}'
        '","command":["/bin/sh","-c","sleep 3600"],'
        '"volumeMounts":[{"name":"images","mountPath":"/images"}]}],'
        '"restartPolicy":"Never"}}'
    )
    try:
        _run(
            [
                "kubectl",
                "run",
                helper,
                "-n",
                settings.kube_namespace,
                "--restart=Never",
                "--image",
                PVC_HELPER_IMAGE,
                "--overrides",
                pod_spec,
                "--command",
                "--",
                "sleep",
                "3600",
            ]
        )
        deadline = time.time() + POD_READY_WAIT_SECONDS
        while time.time() < deadline:
            phase = (
                _run(
                    [
                        "kubectl",
                        "get",
                        "pod",
                        helper,
                        "-n",
                        settings.kube_namespace,
                        "-o",
                        "jsonpath={.status.phase}",
                    ],
                    check=False,
                ).stdout.strip()
            )
            if phase.lower() in {"running", "succeeded"}:
                break
            if phase.lower() in {"failed", "unknown"}:
                raise RuntimeError(f"helper pod failed to start (phase={phase})")
            time.sleep(POD_READY_SLEEP)
        else:
            raise RuntimeError("timed out waiting for helper pod")

        _run(
            [
                "kubectl",
                "exec",
                "-n",
                settings.kube_namespace,
                helper,
                "--",
                "/bin/sh",
                "-c",
                f":> /images/{filename}",
            ]
        )

        max_retries = 4
        chunk_bytes = 8 * 1024 * 1024  # 8MB per exec call
        offset_bytes = 0
        with source_path.open("rb") as infile:
            while True:
                chunk = infile.read(chunk_bytes)
                if not chunk:
                    break
                seek_blocks = offset_bytes // (1024 * 1024)
                for attempt in range(max_retries):
                    cmd = [
                        "kubectl",
                        "exec",
                        "-i",
                        "-n",
                        settings.kube_namespace,
                        helper,
                        "--request-timeout=0",
                        "--",
                        "/bin/sh",
                        "-c",
                        f"dd of=/images/{filename} bs=1M seek={seek_blocks} conv=notrunc status=none",
                    ]
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    error = None
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write(chunk)
                    except BrokenPipeError as exc:
                        error = exc
                    finally:
                        try:
                            if proc.stdin and not proc.stdin.closed:
                                proc.stdin.close()
                        except Exception:
                            pass
                    stdout_data = b""
                    stderr_data = b""
                    try:
                        stdout_data = (proc.stdout.read() if proc.stdout else b"") or b""
                        stderr_data = (proc.stderr.read() if proc.stderr else b"") or b""
                    finally:
                        try:
                            proc.wait()
                        except Exception:
                            pass
                    if proc.returncode == 0 and error is None:
                        break
                    if attempt == max_retries - 1:
                        msg = (stderr_data or stdout_data or b"unknown error").decode().strip()
                        if error:
                            raise RuntimeError("stream interrupted and retries exhausted") from error
                        raise RuntimeError(f"kubectl exec copy failed: {msg}")
                    logger.warning(
                        "Chunk copy interrupted (attempt %d/%d), retrying from offset %d MB",
                        attempt + 1,
                        max_retries,
                        seek_blocks,
                    )
                offset_bytes += len(chunk)
    finally:
        _run(["kubectl", "delete", "pod", helper, "-n", settings.kube_namespace, "--ignore-not-found=true"], check=False)


def _validate_file_on_pvc(filename: str) -> None:
    """
    Validate the image on the PVC using qemu-img check. Raises if invalid.
    """
    result = _with_pvc_helper(
        ["/bin/sh", "-c", f"qemu-img check /images/{filename}"],
        image=settings.runner_image,
    )
    if result and result.returncode != 0:
        msg = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        if "does not support checks" in msg:
            return
        raise RuntimeError(f"qemu-img check failed: {msg or 'invalid image'}")


def _exists_on_pvc(filename: str) -> bool:
    try:
        _with_pvc_helper(["/bin/sh", "-c", f"test -f /images/{filename}"], capture_output=False)
        return True
    except Exception:
        return False


def _convert_qcow_to_raw_on_pvc(filename: str) -> str:
    """
    Convert a qcow/qcow2 image on the PVC to raw. Returns new filename.
    """
    stem = Path(filename).stem
    raw_name = f"{stem}.raw"
    cmd = f"qemu-img convert -O raw /images/{filename} /images/{raw_name} && sync"
    _with_pvc_helper(
        ["/bin/sh", "-c", cmd],
        image=settings.runner_image,
    )
    # Remove original to save space.
    try:
        _with_pvc_helper(["/bin/sh", "-c", f"rm -f /images/{filename}"])
    except Exception:
        logger.warning("Failed to delete source qcow after conversion: %s", filename)
    return raw_name


def _ensure_on_pvc(source_path: Path) -> None:
    if not _exists_on_pvc(source_path.name):
        _copy_file_to_pvc(source_path, source_path.name)


def _list_pvc_files() -> list[dict]:
    items = []
    root = Path(settings.storage_root)
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        st = path.stat()
        items.append({"name": path.name, "size": st.st_size, "mtime": st.st_mtime})
    return items


class ImageImport(BaseModel):
    filename: str
    name: str | None = None
    skip_validation: bool = False


class ImageRename(BaseModel):
    name: str | None = None
    filename: str | None = None
    skip_validation: bool = False

def _user_out(user: User) -> UserOut:
    return UserOut(username=user.username, is_admin=user.is_admin, force_password_change=user.force_password_change)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_user(payload: UserCreate, session: Session = Depends(get_session)) -> UserOut:
    existing = session.get(User, payload.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user exists")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        force_password_change=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return _user_out(user)


@router.get("/users", response_model=list[UserOut])
def list_users(session: Session = Depends(get_session)) -> list[UserOut]:
    users = session.exec(select(User)).all()
    return [_user_out(u) for u in users]


@router.patch("/users/{username}", response_model=UserOut)
def update_user(username: str, payload: UserUpdate, session: Session = Depends(get_session)) -> UserOut:
    user = session.get(User, username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    new_username = payload.username or username
    if payload.username is not None and (len(payload.username) < 3 or len(payload.username) > 64):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid username length")
    if new_username != username:
        existing = session.get(User, new_username)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already exists")
        # migrate instances to new owner
        instances = session.exec(select(Instance).where(Instance.owner == username)).all()
        for inst in instances:
            inst.owner = new_username
            session.add(inst)
    if payload.password:
        user.password_hash = hash_password(payload.password)
        user.force_password_change = False
        revoke_tokens(session, username)
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    user.username = new_username
    session.add(user)
    session.commit()
    session.refresh(user)
    return _user_out(user)


@router.delete("/users/{username}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user(username: str, session: Session = Depends(get_session)) -> None:
    user = session.get(User, username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if user.is_admin and username == settings.admin_default_username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot delete default admin")
    revoke_tokens(session, username)
    session.delete(user)
    session.commit()


@router.post("/images", response_model=ImageCreateResponse, status_code=status.HTTP_201_CREATED)
def upload_image(file: UploadFile = File(...), session: Session = Depends(get_session)) -> ImageCreateResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    sha256 = hashlib.sha256()
    size_bytes = 0
    filename = Path(file.filename).name
    allow_skip_validation = suffix in {".vhd", ".vhdx"}

    try:
        dest_path = IMAGE_DIR / filename
        with dest_path.open("wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="image too large (max 60GB)",
                    )
                buffer.write(chunk)
                sha256.update(chunk)
        if size_bytes == 0:
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
        # Auto-convert qcow/qcow2 to raw for better compatibility.
        if suffix in {".qcow", ".qcow2"}:
            try:
                raw_name = _convert_qcow_to_raw_on_pvc(dest_path.name)
                filename = raw_name
                dest_path = IMAGE_DIR / raw_name
                # Recompute checksum/size from converted raw.
                sha256 = hashlib.sha256()
                size_bytes = 0
                with dest_path.open("rb") as infile:
                    while chunk := infile.read(1024 * 1024):
                        size_bytes += len(chunk)
                        sha256.update(chunk)
            except Exception as exc:
                logger.error("Failed to convert qcow to raw: %s", exc, exc_info=True)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to convert qcow to raw") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to upload %s: %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"upload failed: {exc}") from exc

    if size_bytes == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")

    record = Image(
        id=str(uuid4()),
        name=filename,
        filename=filename,
        checksum=sha256.hexdigest(),
        size_bytes=size_bytes,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    return ImageCreateResponse(
        id=record.id,
        name=record.name,
        filename=record.filename,
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.post("/images/import", response_model=ImageCreateResponse, status_code=status.HTTP_201_CREATED)
def import_image(payload: ImageImport, session: Session = Depends(get_session)) -> ImageCreateResponse:
    dest_path = IMAGE_DIR / Path(payload.filename).name
    if not dest_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found on storage")
    existing = session.exec(select(Image).where(Image.filename == dest_path.name)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="image already registered")
    if dest_path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")

    sha256 = hashlib.sha256()
    size_bytes = 0
    with dest_path.open("rb") as infile:
        while chunk := infile.read(8192):
            sha256.update(chunk)
            size_bytes += len(chunk)

    if not payload.skip_validation:
        try:
            _validate_file_on_pvc(dest_path.name)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"validation failed: {exc}") from exc

    record = Image(
        id=str(uuid4()),
        name=payload.name or dest_path.name,
        filename=dest_path.name,
        checksum=sha256.hexdigest(),
        size_bytes=size_bytes,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    return ImageCreateResponse(
        id=record.id,
        name=record.name,
        filename=record.filename,
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.get("/images", response_model=list[ImageMeta])
def list_images(session: Session = Depends(get_session)) -> list[ImageMeta]:
    pvc_files = {item["name"]: item for item in _list_pvc_files()}
    existing_records = session.exec(select(Image)).all()
    for fname, info in pvc_files.items():
        if any(r.filename == fname for r in existing_records):
            continue
        record = Image(
            id=str(uuid4()),
            name=fname,
            filename=fname,
            checksum="",
            size_bytes=info.get("size", 0),
            created_at=datetime.utcnow(),
        )
        session.add(record)
        existing_records.append(record)
    session.commit()
    images = existing_records
    return [
        ImageMeta(
            id=record.id,
            name=record.name,
            checksum=record.checksum,
            size_bytes=record.size_bytes,
            created_at=record.created_at,
        )
        for record in images
    ]


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(image_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(Image, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    dest_path = IMAGE_DIR / Path(record.filename).name
    if dest_path.exists():
        try:
            dest_path.unlink()
        except OSError as exc:  # pragma: no cover
            raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail="failed to delete image") from exc
    session.delete(record)
    session.commit()


@router.patch("/images/{image_id}", response_model=ImageMeta)
def rename_image(image_id: str, payload: ImageRename, session: Session = Depends(get_session)) -> ImageMeta:
    record = session.get(Image, image_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    new_name = payload.name or record.name
    new_filename = payload.filename or record.filename
    if Path(new_filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    # Ensure no conflict
    existing = session.exec(select(Image).where(Image.filename == new_filename).where(Image.id != image_id)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="filename already exists")

    src_path = IMAGE_DIR / record.filename
    dst_path = IMAGE_DIR / new_filename
    try:
        if src_path.exists():
            src_path.replace(dst_path)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"rename failed: {exc}") from exc

    record.name = new_name
    record.filename = new_filename
    session.add(record)
    session.commit()
    session.refresh(record)
    return ImageMeta(
        id=record.id,
        name=record.name,
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.post("/templates", response_model=VMTemplate, status_code=status.HTTP_201_CREATED)
def create_template(payload: VMTemplateCreate, session: Session = Depends(get_session)) -> VMTemplate:
    image = session.get(Image, payload.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    src_path = IMAGE_DIR / image.filename
    if not src_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image file missing on storage")
    record = Template(
        id=str(uuid4()),
        name=payload.name,
        description=payload.description or "",
        os_type=payload.os_type or "windows",
        image_id=payload.image_id,
        cpu_cores=payload.cpu_cores,
        ram_mb=payload.ram_mb,
        auto_delete_minutes=payload.auto_delete_minutes,
        enabled=payload.enabled,
        network_mode=payload.network_mode,
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return VMTemplate(
        id=record.id,
        name=record.name,
        description=record.description,
        os_type=record.os_type,
        image_id=record.image_id,
        cpu_cores=record.cpu_cores,
        ram_mb=record.ram_mb,
        auto_delete_minutes=record.auto_delete_minutes,
        enabled=record.enabled,
        network_mode=record.network_mode,
        created_at=record.created_at,
    )


@router.get("/templates", response_model=list[VMTemplate])
def list_templates(session: Session = Depends(get_session)) -> list[VMTemplate]:
    templates = session.exec(select(Template)).all()
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
            network_mode=record.network_mode,
            created_at=record.created_at,
        )
        for record in templates
    ]


@router.patch("/templates/{template_id}", response_model=VMTemplate)
def update_template(template_id: str, payload: VMTemplateUpdate, session: Session = Depends(get_session)) -> VMTemplate:
    record = session.get(Template, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    if payload.name is not None:
        record.name = payload.name
    if payload.description is not None:
        record.description = payload.description
    if payload.os_type is not None:
        record.os_type = payload.os_type
    if payload.image_id is not None:
        image = session.get(Image, payload.image_id)
        if not image:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
        record.image_id = payload.image_id
    if payload.cpu_cores is not None:
        record.cpu_cores = payload.cpu_cores
    if payload.ram_mb is not None:
        record.ram_mb = payload.ram_mb
    if payload.auto_delete_minutes is not None:
        record.auto_delete_minutes = payload.auto_delete_minutes
    if payload.enabled is not None:
        record.enabled = payload.enabled
    if payload.network_mode is not None:
        record.network_mode = payload.network_mode
    session.add(record)
    session.commit()
    session.refresh(record)
    return VMTemplate(
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


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(Template, template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    session.delete(record)
    session.commit()


@router.get("/resources")
def cluster_resources() -> dict:
    core = kube._client()
    nodes = core.list_node().items
    total_capacity_cpu = 0
    total_capacity_mem = 0
    total_capacity_disk = 0
    total_allocatable_cpu = 0
    total_allocatable_mem = 0
    total_allocatable_disk = 0
    for node in nodes:
        cap = node.status.capacity or {}
        alloc = node.status.allocatable or {}
        total_capacity_cpu += int(parse_quantity(cap.get("cpu", "0")) * 1000)  # cores -> millicores
        total_capacity_mem += int(parse_quantity(cap.get("memory", "0")))  # bytes
        total_capacity_disk += int(parse_quantity(cap.get("ephemeral-storage", "0")))
        total_allocatable_cpu += int(parse_quantity(alloc.get("cpu", "0")) * 1000)
        total_allocatable_mem += int(parse_quantity(alloc.get("memory", "0")))
        total_allocatable_disk += int(parse_quantity(alloc.get("ephemeral-storage", "0")))

    requested_cpu = 0
    requested_mem = 0
    requested_disk = 0
    pods = core.list_pod_for_all_namespaces().items
    for pod in pods:
        for container in pod.spec.containers:
            req = (container.resources and container.resources.requests) or {}
            if "cpu" in req:
                requested_cpu += int(parse_quantity(req["cpu"]) * 1000)
            if "memory" in req:
                requested_mem += int(parse_quantity(req["memory"]))
            if "ephemeral-storage" in req:
                requested_disk += int(parse_quantity(req["ephemeral-storage"]))

    node_list = []
    for node in nodes:
        name = node.metadata.name
        internal_ip = ""
        for addr in node.status.addresses or []:
            if addr.type == "InternalIP":
                internal_ip = addr.address
        taints = [f"{t.key}={t.value}:{t.effect}" for t in (node.spec.taints or [])]
        node_list.append({"name": name, "ip": internal_ip, "taints": taints})

    return {
        "capacity": {"cpu_m": total_capacity_cpu, "memory_bytes": total_capacity_mem, "disk_bytes": total_capacity_disk},
        "allocatable": {
            "cpu_m": total_allocatable_cpu,
            "memory_bytes": total_allocatable_mem,
            "disk_bytes": total_allocatable_disk,
        },
        "requested": {"cpu_m": requested_cpu, "memory_bytes": requested_mem, "disk_bytes": requested_disk},
        "nodes": node_list,
    }


@router.post("/settings/concurrency", response_model=ConcurrencySettings)
def update_concurrency(settings_payload: ConcurrencySettings, session: Session = Depends(get_session)) -> ConcurrencySettings:
    config = session.get(Config, 1) or Config(id=1)
    config.max_concurrent_vms = settings_payload.max_concurrent_vms
    config.per_user_vm_limit = settings_payload.per_user_vm_limit
    session.add(config)
    session.commit()
    return settings_payload


@router.post("/settings/idle-timeout", response_model=IdleTimeoutSettings)
def update_idle_timeout(settings_payload: IdleTimeoutSettings, session: Session = Depends(get_session)) -> IdleTimeoutSettings:
    config = session.get(Config, 1) or Config(id=1)
    config.idle_timeout_minutes = settings_payload.idle_timeout_minutes
    session.add(config)
    session.commit()
    return settings_payload


@router.get("/settings/runtime", response_model=RuntimeSettingsRead)
def get_runtime_settings() -> RuntimeSettingsRead:
    return RuntimeSettingsRead(
        storage_root=settings.storage_root,
        kube_namespace=settings.kube_namespace,
        kube_image_pvc=settings.kube_image_pvc,
        kube_runtime_class=settings.kube_runtime_class,
        runner_image=settings.runner_image,
        image_pull_secret=settings.image_pull_secret,
        kube_node_selector_key=settings.kube_node_selector_key,
        kube_node_selector_value=settings.kube_node_selector_value,
        kube_use_kvm=settings.kube_use_kvm,
        kube_spice_embed_configmap=settings.kube_spice_embed_configmap,
        kube_node_external_host=settings.kube_node_external_host,
    )


@router.get("/settings/site", response_model=SiteSettings)
def get_site_settings(session: Session = Depends(get_session)) -> SiteSettings:
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


@router.patch("/settings/site", response_model=SiteSettings)
def update_site_settings(payload: SiteSettings, session: Session = Depends(get_session)) -> SiteSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    cfg.site_title = payload.site_title
    cfg.site_tagline = payload.site_tagline
    cfg.theme_bg_color = payload.theme_bg_color
    cfg.theme_text_color = payload.theme_text_color
    cfg.theme_button_color = payload.theme_button_color
    cfg.theme_button_text_color = payload.theme_button_text_color
    cfg.theme_bg_image = payload.theme_bg_image
    cfg.theme_tile_bg = payload.theme_tile_bg
    cfg.theme_tile_border = payload.theme_tile_border
    cfg.theme_tile_opacity = payload.theme_tile_opacity
    cfg.theme_tile_border_opacity = payload.theme_tile_border_opacity
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
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
def get_sso_settings(session: Session = Depends(get_session)) -> SSOSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret=cfg.sso_client_secret,
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
    )


@router.patch("/settings/sso", response_model=SSOSettings)
def update_sso_settings(payload: SSOSettings, session: Session = Depends(get_session)) -> SSOSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    cfg.sso_enabled = payload.sso_enabled
    cfg.sso_provider = payload.sso_provider
    cfg.sso_client_id = payload.sso_client_id
    cfg.sso_client_secret = payload.sso_client_secret
    cfg.sso_authorize_url = payload.sso_authorize_url
    cfg.sso_token_url = payload.sso_token_url
    cfg.sso_userinfo_url = payload.sso_userinfo_url
    cfg.sso_redirect_url = payload.sso_redirect_url
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret=cfg.sso_client_secret,
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
    )


@router.get("/settings/runtime", response_model=RuntimeSettingsRead)
def get_runtime_settings() -> RuntimeSettingsRead:
    return RuntimeSettingsRead(
        storage_root=settings.storage_root,
        kube_namespace=settings.kube_namespace,
        kube_image_pvc=settings.kube_image_pvc,
        kube_runtime_class=settings.kube_runtime_class,
        runner_image=settings.runner_image,
        image_pull_secret=settings.image_pull_secret,
        kube_node_selector_key=settings.kube_node_selector_key,
        kube_node_selector_value=settings.kube_node_selector_value,
        kube_use_kvm=settings.kube_use_kvm,
        kube_spice_embed_configmap=settings.kube_spice_embed_configmap,
        kube_node_external_host=settings.kube_node_external_host,
    )


@router.get("/pods", response_model=list[VMInstance])
def list_running_pods(session: Session = Depends(get_session)) -> list[VMInstance]:
    instances = session.exec(select(Instance)).all()
    return [
        VMInstance(
            id=record.id,
            template_id=record.template_id,
            owner=record.owner,
            status=record.status,
            started_at=record.started_at,
            last_active_at=record.last_active_at,
            console_url=record.console_url,
        )
        for record in instances
    ]


@router.post("/pods/{instance_id}/stop", response_model=VMInstance)
def stop_pod(instance_id: str, session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record:
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


@router.delete("/pods/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pod(instance_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(Instance, instance_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.delete_pod(instance_id, record.owner)
    session.delete(record)
    session.commit()
