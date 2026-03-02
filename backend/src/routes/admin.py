import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
import requests
from sqlmodel import Session, select
from kubernetes import client
from kubernetes.client import ApiException
from kubernetes.utils import parse_quantity

from ..auth import hash_password, require_admin, revoke_tokens
from ..config import settings
from ..db import SQLITE_DB, get_session, session_scope
from ..models import (
    AlertManagerAlert,
    AlertsAndErrorsView,
    ConcurrencySettings,
    ErrorLogView,
    IdleTimeoutSettings,
    ImageCreateResponse,
    ImageMeta,
    ImageUploadTaskStatus,
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
from ..tables import Config, Image, ImageUploadTask, Instance, Template, User

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)

IMAGE_DIR = Path(settings.storage_root)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024 * 1024  # 60 GB
ALLOWED_SUFFIXES = {".vhd", ".vhdx", ".qcow", ".qcow2", ".vdi"}
RAW_CONVERSION_SUFFIXES = {".qcow", ".qcow2"}
QCOW2_CONVERSION_SUFFIXES = {".vhd", ".vhdx", ".vdi"}
MIN_FREE_UPLOAD_BYTES = 18 * 1024 * 1024 * 1024  # keep nodefs above kubelet disk-pressure headroom
SOURCE_PVC_OVERHEAD_BYTES = 1024 * 1024 * 1024  # account for filesystem metadata/lost+found overhead

# Reuse the runner image for helper pods so fresh/private clusters do not depend on Docker Hub pulls.
PVC_HELPER_IMAGE = settings.runner_image or "alpine:3.19"
POD_READY_WAIT_SECONDS = 120
POD_READY_SLEEP = 2
FINALIZE_JOB_TIMEOUT_SECONDS = 3 * 60 * 60
COPY_JOB_TIMEOUT_SECONDS = 3 * 60 * 60
TASK_RETENTION_HOURS = 24

_CDI_AVAILABLE: bool | None = None
ALERTS_ERRORS_MAX_LOG_BYTES = 10 * 1024 * 1024
ERROR_LOG_LINE_RE = re.compile(r"(error|exception|traceback|critical|failed)", re.IGNORECASE)


def _to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _trim_log_bytes(content: str, max_bytes: int) -> tuple[str, bool]:
    raw = content.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return content, False
    clipped = raw[-max_bytes:]
    # Keep whole lines in the clipped view when possible.
    newline_idx = clipped.find(b"\n")
    if newline_idx != -1 and newline_idx + 1 < len(clipped):
        clipped = clipped[newline_idx + 1 :]
    return clipped.decode("utf-8", errors="replace"), True


def _extract_error_lines(content: str) -> str:
    lines = [line for line in content.splitlines() if ERROR_LOG_LINE_RE.search(line)]
    return "\n".join(lines)


def _read_error_log_file(path: Path, max_bytes: int) -> ErrorLogView:
    source = f"file:{path}"
    if not path.exists():
        return ErrorLogView(source=source, bytes=0, truncated=False, content="Log file not found.")
    try:
        file_size = path.stat().st_size
        with path.open("rb") as fh:
            if file_size > max_bytes:
                fh.seek(-max_bytes, os.SEEK_END)
            raw = fh.read(max_bytes if file_size > max_bytes else file_size)
    except Exception as exc:
        logger.warning("Failed reading error log file %s: %s", path, exc)
        return ErrorLogView(source=source, bytes=0, truncated=False, content=f"Failed to read log file: {exc}")

    text = raw.decode("utf-8", errors="replace")
    filtered = _extract_error_lines(text)
    if not filtered:
        filtered = "No error lines found in the selected log file."
    clipped, clipped_flag = _trim_log_bytes(filtered, max_bytes)
    return ErrorLogView(
        source=source,
        bytes=len(clipped.encode("utf-8", errors="replace")),
        truncated=(file_size > max_bytes) or clipped_flag,
        content=clipped,
    )


def _collect_k8s_error_logs(max_bytes: int) -> ErrorLogView:
    source = f"kubernetes:{settings.kube_namespace}"
    core = kube._client()
    try:
        pods = core.list_namespaced_pod(namespace=settings.kube_namespace).items
    except ApiException as exc:
        return ErrorLogView(source=source, bytes=0, truncated=False, content=f"Failed to list pods: {exc}")

    # Most recent pods first so operators see the latest failures first.
    pods_sorted = sorted(
        pods,
        key=lambda pod: (pod.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    sections: list[str] = []
    max_per_pod = min(max_bytes, 1024 * 1024)
    for pod in pods_sorted:
        name = _to_str(pod.metadata.name)
        if not name:
            continue
        try:
            log_text = core.read_namespaced_pod_log(
                name=name,
                namespace=settings.kube_namespace,
                timestamps=True,
                tail_lines=4000,
                limit_bytes=max_per_pod,
            )
        except ApiException:
            continue
        filtered = _extract_error_lines(log_text or "")
        if not filtered:
            continue
        sections.append(f"===== {name} =====\n{filtered}\n")
        if len("".join(sections).encode("utf-8", errors="replace")) >= max_bytes * 2:
            break

    content = "".join(sections).strip()
    if not content:
        content = "No error lines found in current Kubernetes pod logs."
    clipped, clipped_flag = _trim_log_bytes(content, max_bytes)
    return ErrorLogView(
        source=source,
        bytes=len(clipped.encode("utf-8", errors="replace")),
        truncated=clipped_flag,
        content=clipped,
    )


def _fetch_alertmanager_alerts() -> tuple[list[AlertManagerAlert], str]:
    url = _to_str(settings.alertmanager_api_url)
    if not url:
        return [], "Alertmanager URL is not configured."
    timeout_seconds = max(1, int(settings.alertmanager_timeout_seconds))
    try:
        resp = requests.get(url, timeout=timeout_seconds)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        return [], f"Failed to query Alertmanager: {exc}"
    except ValueError as exc:
        return [], f"Alertmanager returned invalid JSON: {exc}"

    if not isinstance(payload, list):
        return [], "Alertmanager response format is unexpected."

    alerts: list[AlertManagerAlert] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        status_obj = item.get("status") if isinstance(item.get("status"), dict) else {}
        alerts.append(
            AlertManagerAlert(
                name=_to_str(labels.get("alertname")) or "unnamed-alert",
                state=_to_str(status_obj.get("state")) or "unknown",
                severity=_to_str(labels.get("severity")),
                summary=_to_str(annotations.get("summary")),
                description=_to_str(annotations.get("description")),
                starts_at=item.get("startsAt"),
                ends_at=item.get("endsAt"),
                source=_to_str(item.get("generatorURL")),
                labels={str(k): _to_str(v) for k, v in labels.items()},
            )
        )
    alerts.sort(key=lambda alert: (alert.state.lower() != "active", alert.name))
    return alerts, ""


def _helper_overrides(worker_image: str, claim_name: str) -> str:
    spec: dict = {
        "spec": {
            "volumes": [{"name": "images", "persistentVolumeClaim": {"claimName": claim_name}}],
            "containers": [
                {
                    "name": "worker",
                    "image": worker_image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/sh", "-c", "sleep 3600"],
                    "volumeMounts": [{"name": "images", "mountPath": "/images"}],
                }
            ],
            "restartPolicy": "Never",
        }
    }
    if settings.image_pull_secret:
        spec["spec"]["imagePullSecrets"] = [{"name": settings.image_pull_secret}]
    return json.dumps(spec, separators=(",", ":"))


def _ensure_config_columns() -> None:
    if not SQLITE_DB:
        return
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


def _ensure_template_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(template)")}
        to_add = []
        if "idle_timeout_minutes" not in cols:
            to_add.append(
                f"ALTER TABLE template ADD COLUMN idle_timeout_minutes INTEGER DEFAULT {settings.idle_timeout_minutes}"
            )
        if "preclone_pool_size" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN preclone_pool_size INTEGER DEFAULT 0")
        if "preclone_pool_max" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN preclone_pool_max INTEGER DEFAULT 0")
        for stmt in to_add:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass
        if to_add:
            conn.commit()
            cur.execute(
                "UPDATE template SET idle_timeout_minutes = ? WHERE idle_timeout_minutes IS NULL",
                (settings.idle_timeout_minutes,),
            )
            cur.execute("UPDATE template SET preclone_pool_size = 0 WHERE preclone_pool_size IS NULL")
            # Keep existing behavior for upgraded rows: max defaults to min.
            cur.execute("UPDATE template SET preclone_pool_max = preclone_pool_size WHERE preclone_pool_max IS NULL")
            cur.execute("UPDATE template SET preclone_pool_max = preclone_pool_size WHERE preclone_pool_max < preclone_pool_size")
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure template columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_image_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(image)")}
        if "source_pvc" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN source_pvc TEXT")
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure image columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_instance_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "instance" not in tables:
            return
        cols = {row[1] for row in cur.execute("PRAGMA table_info(instance)")}
        if "disk_pvc" not in cols:
            cur.execute("ALTER TABLE instance ADD COLUMN disk_pvc TEXT")
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure instance columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_upload_task_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "imageuploadtask" not in tables:
            return
        cols = {row[1] for row in cur.execute("PRAGMA table_info(imageuploadtask)")}
        to_add = []
        if "checksum" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN checksum TEXT")
        if "source_pvc" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN source_pvc TEXT")
        if "upload_pvc" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN upload_pvc TEXT")
        if "finalize_job" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN finalize_job TEXT")
        if "copy_job" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN copy_job TEXT")
        for stmt in to_add:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass
        if to_add:
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure image upload task columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


_ensure_config_columns()
_ensure_template_columns()
_ensure_image_columns()
_ensure_instance_columns()
_ensure_upload_task_columns()


def _upload_task_out(task: ImageUploadTask) -> ImageUploadTaskStatus:
    return ImageUploadTaskStatus(
        task_id=task.id,
        status=task.status,
        original_filename=task.original_filename,
        filename=task.filename,
        size_bytes=task.size_bytes,
        detail=task.detail or "",
        error=task.error_message,
        image_id=task.image_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _update_upload_task(
    task_id: str,
    *,
    status: str | None = None,
    detail: str | None = None,
    error_message: str | None = None,
    image_id: str | None = None,
    filename: str | None = None,
    size_bytes: int | None = None,
    checksum: str | None = None,
    source_pvc: str | None = None,
    upload_pvc: str | None = None,
    finalize_job: str | None = None,
    copy_job: str | None = None,
) -> None:
    with session_scope() as session:
        task = session.get(ImageUploadTask, task_id)
        if not task:
            return
        if status is not None:
            task.status = status
        if detail is not None:
            task.detail = detail
        if error_message is not None:
            task.error_message = error_message
        if image_id is not None:
            task.image_id = image_id
        if filename is not None:
            task.filename = filename
        if size_bytes is not None:
            task.size_bytes = size_bytes
        if checksum is not None:
            task.checksum = checksum
        if source_pvc is not None:
            task.source_pvc = source_pvc
        if upload_pvc is not None:
            task.upload_pvc = upload_pvc
        if finalize_job is not None:
            task.finalize_job = finalize_job
        if copy_job is not None:
            task.copy_job = copy_job
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()


def _job_phase(job: client.V1Job | None) -> str:
    if not job or not job.status:
        return "unknown"
    if (job.status.failed or 0) > 0:
        return "failed"
    if (job.status.succeeded or 0) > 0:
        return "succeeded"
    if (job.status.active or 0) > 0:
        return "running"
    return "pending"


def _cleanup_task_jobs(task: ImageUploadTask) -> None:
    batch = client.BatchV1Api()
    custom = client.CustomObjectsApi()
    core = kube._client()
    for name in [task.finalize_job, task.copy_job]:
        if not name:
            continue
        if name.startswith("dv:"):
            # Keep successful DataVolumes so source PVCs remain attached and reusable.
            continue
        try:
            batch.delete_namespaced_job(
                name=name,
                namespace=settings.kube_namespace,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to cleanup job %s for task %s", name, task.id, exc_info=True)
    if task.upload_pvc:
        try:
            custom.delete_namespaced_custom_object(
                group="cdi.kubevirt.io",
                version="v1beta1",
                namespace=settings.kube_namespace,
                plural="datavolumes",
                name=task.upload_pvc,
            )
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to cleanup direct-upload DataVolume %s", task.upload_pvc, exc_info=True)
        try:
            core.delete_namespaced_persistent_volume_claim(name=task.upload_pvc, namespace=settings.kube_namespace)
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to cleanup direct-upload PVC %s", task.upload_pvc, exc_info=True)
    _cleanup_fileserver(task.id)


def _finalize_job_name(task_id: str) -> str:
    return f"img-finalize-{task_id[:8]}"


def _copy_job_name(task_id: str) -> str:
    return f"img-copy-{task_id[:8]}"


def _fileserver_name(task_id: str) -> str:
    return f"img-srcsrv-{task_id[:8]}"


def _has_cdi_datavolume() -> bool:
    global _CDI_AVAILABLE
    if _CDI_AVAILABLE is not None:
        return _CDI_AVAILABLE
    try:
        ext = client.ApiextensionsV1Api()
        ext.read_custom_resource_definition("datavolumes.cdi.kubevirt.io")
        _CDI_AVAILABLE = True
    except Exception:
        _CDI_AVAILABLE = False
    return _CDI_AVAILABLE


def _cleanup_fileserver(task_id: str) -> None:
    name = _fileserver_name(task_id)
    core = kube._client()
    try:
        core.delete_namespaced_service(name=name, namespace=settings.kube_namespace)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete fileserver service %s", name, exc_info=True)
    try:
        core.delete_namespaced_pod(
            name=name,
            namespace=settings.kube_namespace,
            grace_period_seconds=0,
            propagation_policy="Background",
        )
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Failed to delete fileserver pod %s", name, exc_info=True)


def _ensure_fileserver(task: ImageUploadTask) -> str:
    name = _fileserver_name(task.id)
    core = kube._client()
    labels = {"upload-task": task.id, "job-type": "image-fileserver"}
    try:
        core.read_namespaced_pod(name=name, namespace=settings.kube_namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=name, namespace=settings.kube_namespace, labels=labels),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="fileserver",
                        image=settings.runner_image,
                        image_pull_policy="IfNotPresent",
                        command=["python3", "-m", "http.server", "8080", "--directory", "/images"],
                        ports=[client.V1ContainerPort(container_port=8080)],
                        volume_mounts=[client.V1VolumeMount(name="images", mount_path="/images", read_only=True)],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="images",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=settings.kube_image_pvc),
                    )
                ],
            ),
        )
        if settings.image_pull_secret:
            pod.spec.image_pull_secrets = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        core.create_namespaced_pod(namespace=settings.kube_namespace, body=pod)

    try:
        core.read_namespaced_service(name=name, namespace=settings.kube_namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        service = client.V1Service(
            metadata=client.V1ObjectMeta(name=name, namespace=settings.kube_namespace, labels=labels),
            spec=client.V1ServiceSpec(
                selector=labels,
                ports=[client.V1ServicePort(port=8080, target_port=8080, protocol="TCP")],
            ),
        )
        core.create_namespaced_service(namespace=settings.kube_namespace, body=service)

    return f"http://{name}.{settings.kube_namespace}.svc.cluster.local:8080/{urlquote(task.filename)}"


def _start_datavolume_import(task: ImageUploadTask, claim_name: str) -> str:
    if not _has_cdi_datavolume():
        raise RuntimeError("CDI DataVolume CRD is not installed")
    custom = client.CustomObjectsApi()
    core = kube._client()
    required_bytes = int(task.size_bytes) + SOURCE_PVC_OVERHEAD_BYTES
    requested_gi = max(1, math.ceil(required_bytes / (1024 ** 3)))

    # Remove existing PVC/DataVolume so the import can be recreated with the expected size.
    try:
        core.delete_namespaced_persistent_volume_claim(name=claim_name, namespace=settings.kube_namespace)
        _wait_for_pvc_deleted(core, claim_name)
    except ApiException as exc:
        if exc.status != 404:
            raise
    try:
        custom.delete_namespaced_custom_object(
            group="cdi.kubevirt.io",
            version="v1beta1",
            namespace=settings.kube_namespace,
            plural="datavolumes",
            name=claim_name,
        )
    except ApiException as exc:
        if exc.status != 404:
            raise

    url = _ensure_fileserver(task)
    body = {
        "apiVersion": "cdi.kubevirt.io/v1beta1",
        "kind": "DataVolume",
        "metadata": {
            "name": claim_name,
            "namespace": settings.kube_namespace,
            "labels": {
                "app.kubernetes.io/part-of": "bretter-labs",
                "image-id": task.image_id or "",
                "upload-task": task.id,
            },
        },
        "spec": {
            "source": {"http": {"url": url}},
            "pvc": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": settings.kube_vm_storage_class,
                "resources": {"requests": {"storage": f"{requested_gi}Gi"}},
            },
        },
    }
    custom.create_namespaced_custom_object(
        group="cdi.kubevirt.io",
        version="v1beta1",
        namespace=settings.kube_namespace,
        plural="datavolumes",
        body=body,
    )
    return f"dv:{claim_name}"


def _datavolume_phase(name: str) -> tuple[str, str]:
    custom = client.CustomObjectsApi()
    obj = custom.get_namespaced_custom_object(
        group="cdi.kubevirt.io",
        version="v1beta1",
        namespace=settings.kube_namespace,
        plural="datavolumes",
        name=name,
    )
    status_obj = obj.get("status", {}) if isinstance(obj, dict) else {}
    phase = str(status_obj.get("phase") or "").strip() or "Unknown"
    msg = str(status_obj.get("message") or "").strip()
    return phase, msg


def _direct_upload_pvc_name(task_id: str) -> str:
    return f"img-upload-{task_id[:8]}"


def _direct_upload_url() -> str:
    base = (settings.cdi_upload_proxy_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("BLABS_CDI_UPLOAD_PROXY_URL is not configured")
    return f"{base}/v1beta1/upload"


def _create_direct_upload_datavolume(task: ImageUploadTask) -> str:
    if not _has_cdi_datavolume():
        raise RuntimeError("CDI DataVolume CRD is not installed")
    if not settings.kube_vm_storage_class:
        raise RuntimeError("BLABS_KUBE_VM_STORAGE_CLASS is required for direct CDI upload")
    custom = client.CustomObjectsApi()
    required_bytes = int(task.size_bytes) + SOURCE_PVC_OVERHEAD_BYTES
    requested_gi = max(1, math.ceil(required_bytes / (1024 ** 3)))
    name = _direct_upload_pvc_name(task.id)

    body = {
        "apiVersion": "cdi.kubevirt.io/v1beta1",
        "kind": "DataVolume",
        "metadata": {
            "name": name,
            "namespace": settings.kube_namespace,
            "labels": {
                "app.kubernetes.io/part-of": "bretter-labs",
                "upload-task": task.id,
                "job-type": "image-direct-upload",
            },
        },
        "spec": {
            "source": {"upload": {}},
            "pvc": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": settings.kube_vm_storage_class,
                "resources": {"requests": {"storage": f"{requested_gi}Gi"}},
            },
        },
    }

    try:
        custom.create_namespaced_custom_object(
            group="cdi.kubevirt.io",
            version="v1beta1",
            namespace=settings.kube_namespace,
            plural="datavolumes",
            body=body,
        )
    except ApiException as exc:
        if exc.status != 409:
            raise
    return name


def _request_direct_upload_token(claim_name: str) -> str:
    custom = client.CustomObjectsApi()
    req_name = f"upload-token-{uuid4().hex[:10]}"
    body = {
        "apiVersion": "upload.cdi.kubevirt.io/v1beta1",
        "kind": "UploadTokenRequest",
        "metadata": {"name": req_name, "namespace": settings.kube_namespace},
        "spec": {"pvcName": claim_name},
    }
    response = custom.create_namespaced_custom_object(
        group="upload.cdi.kubevirt.io",
        version="v1beta1",
        namespace=settings.kube_namespace,
        plural="uploadtokenrequests",
        body=body,
    )
    token = str((response or {}).get("status", {}).get("token") or "").strip()
    if not token:
        raise RuntimeError("failed to acquire CDI upload token")
    return token


def _finalize_conversion_spec(suffix: str) -> tuple[str, str]:
    if suffix in RAW_CONVERSION_SUFFIXES:
        return ("raw", "raw")
    if suffix in QCOW2_CONVERSION_SUFFIXES:
        return ("qcow2", "qcow2")
    return ("", "")


def _create_finalize_job(task: ImageUploadTask) -> str:
    batch = client.BatchV1Api()
    suffix = Path(task.filename).suffix.lower()
    convert_fmt, output_suffix = _finalize_conversion_spec(suffix)
    short_id = task.id[:8]
    job_name = _finalize_job_name(task.id)

    container = client.V1Container(
        name="finalize",
        image=settings.runner_image,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="INPUT_FILENAME", value=task.filename),
            client.V1EnvVar(name="CONVERT_FORMAT", value=convert_fmt),
            client.V1EnvVar(name="OUTPUT_SUFFIX", value=output_suffix),
            client.V1EnvVar(name="TASK_SHORT_ID", value=short_id),
        ],
        command=["/bin/sh", "-c"],
        args=[
            r"""
set -euo pipefail
in="/images/${INPUT_FILENAME}"
if [ ! -f "${in}" ]; then
  echo "BLABS_ERROR=input missing: ${INPUT_FILENAME}"
  exit 20
fi
out="${in}"
if [ -n "${CONVERT_FORMAT}" ] && [ -n "${OUTPUT_SUFFIX}" ]; then
  stem="${INPUT_FILENAME%.*}"
  out="/images/${stem}.${OUTPUT_SUFFIX}"
  if [ -f "${out}" ]; then
    out="/images/${stem}-${TASK_SHORT_ID}.${OUTPUT_SUFFIX}"
  fi
  qemu-img convert -O "${CONVERT_FORMAT}" "${in}" "${out}"
  rm -f "${in}"
fi
sync
size="$(wc -c < "${out}")"
sha="$(sha256sum "${out}" | awk '{print $1}')"
echo "BLABS_OUTPUT_FILENAME=$(basename "${out}")"
echo "BLABS_OUTPUT_SIZE=${size}"
echo "BLABS_OUTPUT_SHA256=${sha}"
"""
        ],
        volume_mounts=[
            client.V1VolumeMount(name="images", mount_path="/images", read_only=False),
        ],
    )
    spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            client.V1Volume(
                name="images",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=settings.kube_image_pvc),
            )
        ],
        tolerations=[
            client.V1Toleration(
                key="node-role.kubernetes.io/control-plane",
                operator="Exists",
                effect="NoSchedule",
            ),
            client.V1Toleration(
                key="node-role.kubernetes.io/master",
                operator="Exists",
                effect="NoSchedule",
            ),
        ],
    )
    if settings.image_pull_secret:
        spec.image_pull_secrets = [client.V1LocalObjectReference(name=settings.image_pull_secret)]

    job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=settings.kube_namespace,
            labels={"app.kubernetes.io/part-of": "bretter-labs", "upload-task": task.id, "job-type": "image-finalize"},
        ),
        spec=client.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=TASK_RETENTION_HOURS * 3600,
            active_deadline_seconds=FINALIZE_JOB_TIMEOUT_SECONDS,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"upload-task": task.id, "job-type": "image-finalize"}),
                spec=spec,
            ),
        ),
    )

    try:
        batch.create_namespaced_job(namespace=settings.kube_namespace, body=job)
    except ApiException as exc:
        if exc.status != 409:
            raise
        # Reuse existing job if the API already has one for this task.
        existing = batch.read_namespaced_job(name=job_name, namespace=settings.kube_namespace)
        if _job_phase(existing) == "failed":
            batch.delete_namespaced_job(
                name=job_name,
                namespace=settings.kube_namespace,
                propagation_policy="Background",
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    batch.read_namespaced_job(name=job_name, namespace=settings.kube_namespace)
                except ApiException as check_exc:
                    if check_exc.status == 404:
                        break
                    raise
                time.sleep(1)
            batch.create_namespaced_job(namespace=settings.kube_namespace, body=job)

    # Best-effort stale pod cleanup in case an old helper is lingering.
    try:
        _cleanup_stale_helper_pods()
    except Exception:
        pass
    return job_name


def _create_finalize_from_upload_job(task: ImageUploadTask) -> str:
    if not task.upload_pvc:
        raise RuntimeError("upload PVC missing for direct upload finalize")
    batch = client.BatchV1Api()
    suffix = Path(task.filename).suffix.lower()
    convert_fmt, output_suffix = _finalize_conversion_spec(suffix)
    short_id = task.id[:8]
    job_name = _finalize_job_name(task.id)

    container = client.V1Container(
        name="finalize",
        image=settings.runner_image,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="INPUT_FILENAME", value=task.filename),
            client.V1EnvVar(name="CONVERT_FORMAT", value=convert_fmt),
            client.V1EnvVar(name="OUTPUT_SUFFIX", value=output_suffix),
            client.V1EnvVar(name="TASK_SHORT_ID", value=short_id),
            client.V1EnvVar(name="UPLOAD_SOURCE_FILENAME", value=settings.cdi_upload_source_filename or "disk.img"),
        ],
        command=["/bin/sh", "-c"],
        args=[
            r"""
set -euo pipefail
src="/upload/${UPLOAD_SOURCE_FILENAME}"
if [ ! -f "${src}" ]; then
  fallback="$(find /upload -maxdepth 2 -type f | head -n 1 || true)"
  if [ -z "${fallback}" ]; then
    echo "BLABS_ERROR=upload source image missing"
    exit 22
  fi
  src="${fallback}"
fi
stage="/images/${INPUT_FILENAME}"
cp -f "${src}" "${stage}"
sync
out="${stage}"
if [ -n "${CONVERT_FORMAT}" ] && [ -n "${OUTPUT_SUFFIX}" ]; then
  stem="${INPUT_FILENAME%.*}"
  out="/images/${stem}.${OUTPUT_SUFFIX}"
  if [ -f "${out}" ]; then
    out="/images/${stem}-${TASK_SHORT_ID}.${OUTPUT_SUFFIX}"
  fi
  qemu-img convert -O "${CONVERT_FORMAT}" "${stage}" "${out}"
  rm -f "${stage}"
fi
sync
size="$(wc -c < "${out}")"
sha="$(sha256sum "${out}" | awk '{print $1}')"
echo "BLABS_OUTPUT_FILENAME=$(basename "${out}")"
echo "BLABS_OUTPUT_SIZE=${size}"
echo "BLABS_OUTPUT_SHA256=${sha}"
"""
        ],
        volume_mounts=[
            client.V1VolumeMount(name="upload", mount_path="/upload", read_only=True),
            client.V1VolumeMount(name="images", mount_path="/images", read_only=False),
        ],
    )
    spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            client.V1Volume(
                name="upload",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=task.upload_pvc),
            ),
            client.V1Volume(
                name="images",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=settings.kube_image_pvc),
            ),
        ],
        tolerations=[
            client.V1Toleration(
                key="node-role.kubernetes.io/control-plane",
                operator="Exists",
                effect="NoSchedule",
            ),
            client.V1Toleration(
                key="node-role.kubernetes.io/master",
                operator="Exists",
                effect="NoSchedule",
            ),
        ],
    )
    if settings.image_pull_secret:
        spec.image_pull_secrets = [client.V1LocalObjectReference(name=settings.image_pull_secret)]

    job = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=settings.kube_namespace,
            labels={"app.kubernetes.io/part-of": "bretter-labs", "upload-task": task.id, "job-type": "image-finalize"},
        ),
        spec=client.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=TASK_RETENTION_HOURS * 3600,
            active_deadline_seconds=FINALIZE_JOB_TIMEOUT_SECONDS,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"upload-task": task.id, "job-type": "image-finalize"}),
                spec=spec,
            ),
        ),
    )
    try:
        batch.create_namespaced_job(namespace=settings.kube_namespace, body=job)
    except ApiException as exc:
        if exc.status != 409:
            raise
    return job_name


def _parse_finalize_log(log_data: str) -> tuple[str, int, str]:
    name_match = re.search(r"BLABS_OUTPUT_FILENAME=([^\n]+)", log_data)
    size_match = re.search(r"BLABS_OUTPUT_SIZE=([0-9]+)", log_data)
    sha_match = re.search(r"BLABS_OUTPUT_SHA256=([0-9a-fA-F]{64})", log_data)
    if not name_match or not size_match or not sha_match:
        raise RuntimeError("missing finalize output markers")
    return (name_match.group(1).strip(), int(size_match.group(1)), sha_match.group(1).lower())


def _read_job_log(job_name: str, *, tail_lines: int = 200) -> str:
    core = kube._client()
    pods = core.list_namespaced_pod(
        namespace=settings.kube_namespace,
        label_selector=f"job-name={job_name}",
    ).items
    if not pods:
        return ""
    pods.sort(key=lambda p: p.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    pod_name = pods[0].metadata.name
    try:
        return core.read_namespaced_pod_log(
            name=pod_name,
            namespace=settings.kube_namespace,
            tail_lines=tail_lines,
        )
    except Exception:
        return ""


def _ensure_upload_task_finalize_job(task: ImageUploadTask) -> None:
    if task.finalize_job:
        return
    task.finalize_job = _create_finalize_from_upload_job(task) if task.upload_pvc else _create_finalize_job(task)
    task.status = "finalizing"
    task.detail = "Finalizing image format/checksum on cluster"
    task.updated_at = datetime.utcnow()


def _create_task_copy_job(task: ImageUploadTask) -> tuple[str, str]:
    if not task.image_id:
        raise RuntimeError("upload task image_id is missing")
    claim_name = _ensure_image_source_pvc_claim(task.image_id, task.size_bytes)
    if settings.kube_upload_use_cdi and _has_cdi_datavolume():
        try:
            copy_ref = _start_datavolume_import(task, claim_name)
            return claim_name, copy_ref
        except Exception:
            logger.warning("CDI DataVolume import setup failed; falling back to copy job", exc_info=True)
            _cleanup_fileserver(task.id)
            claim_name = _ensure_image_source_pvc_claim(task.image_id, task.size_bytes)

    job_name = _copy_job_name(task.id)

    batch = client.BatchV1Api()
    container = client.V1Container(
        name="copy",
        image=PVC_HELPER_IMAGE,
        image_pull_policy="IfNotPresent",
        env=[client.V1EnvVar(name="FILENAME", value=task.filename)],
        command=["/bin/sh", "-c"],
        args=[
            r"""
set -euo pipefail
src="/source/${FILENAME}"
dst="/target/${FILENAME}"
if [ ! -f "${src}" ]; then
  echo "BLABS_ERROR=source missing: ${src}"
  exit 21
fi
cp -f "${src}" "${dst}"
sync
echo "BLABS_COPY_SIZE=$(wc -c < "${dst}")"
"""
        ],
        volume_mounts=[
            client.V1VolumeMount(name="source", mount_path="/source", read_only=True),
            client.V1VolumeMount(name="target", mount_path="/target", read_only=False),
        ],
    )
    spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            client.V1Volume(
                name="source",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=settings.kube_image_pvc),
            ),
            client.V1Volume(
                name="target",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=claim_name),
            ),
        ],
        tolerations=[
            client.V1Toleration(
                key="node-role.kubernetes.io/control-plane",
                operator="Exists",
                effect="NoSchedule",
            ),
            client.V1Toleration(
                key="node-role.kubernetes.io/master",
                operator="Exists",
                effect="NoSchedule",
            ),
        ],
    )
    if settings.image_pull_secret:
        spec.image_pull_secrets = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
    body = client.V1Job(
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=settings.kube_namespace,
            labels={"app.kubernetes.io/part-of": "bretter-labs", "upload-task": task.id, "job-type": "image-copy"},
        ),
        spec=client.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=TASK_RETENTION_HOURS * 3600,
            active_deadline_seconds=COPY_JOB_TIMEOUT_SECONDS,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"upload-task": task.id, "job-type": "image-copy"}),
                spec=spec,
            ),
        ),
    )
    try:
        batch.create_namespaced_job(namespace=settings.kube_namespace, body=body)
    except ApiException as exc:
        if exc.status != 409:
            raise
    return claim_name, job_name


def _upsert_image_from_task(task: ImageUploadTask, session: Session) -> None:
    if not task.image_id:
        raise RuntimeError("upload task image_id is missing")
    if not task.source_pvc:
        raise RuntimeError("upload task source_pvc is missing")
    if not task.checksum:
        raise RuntimeError("upload task checksum is missing")

    existing = session.get(Image, task.image_id)
    if existing:
        existing.name = task.filename
        existing.filename = task.filename
        existing.source_pvc = task.source_pvc
        existing.checksum = task.checksum
        existing.size_bytes = task.size_bytes
        session.add(existing)
        return

    record = Image(
        id=task.image_id,
        name=task.filename,
        filename=task.filename,
        source_pvc=task.source_pvc,
        checksum=task.checksum,
        size_bytes=task.size_bytes,
        created_at=datetime.utcnow(),
    )
    session.add(record)


def _refresh_upload_task(task: ImageUploadTask, session: Session) -> ImageUploadTask:
    if task.status in {"completed", "failed"}:
        return task

    batch = client.BatchV1Api()

    if task.upload_pvc and task.status == "uploading":
        try:
            phase, msg = _datavolume_phase(task.upload_pvc)
        except ApiException as exc:
            if exc.status == 404:
                task.status = "failed"
                task.detail = "Direct upload DataVolume not found"
                task.error_message = "direct upload datavolume disappeared before completion"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                _cleanup_task_jobs(task)
                return task
            raise
        phase_lower = phase.lower()
        if phase_lower == "failed":
            task.status = "failed"
            task.detail = "Direct CDI upload failed"
            task.error_message = msg or "direct upload failed"
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            _cleanup_task_jobs(task)
            return task
        if phase_lower != "succeeded":
            task.detail = "Uploading image directly to CDI DataVolume"
            task.error_message = None
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
        task.status = "finalizing"
        task.detail = "Direct upload complete; starting finalize job"
        task.error_message = None

    if task.status != "uploading":
        try:
            _ensure_upload_task_finalize_job(task)
        except Exception as exc:
            task.status = "failed"
            task.detail = "Failed to submit finalize job"
            task.error_message = str(exc)
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    if task.status == "finalizing":
        try:
            job = batch.read_namespaced_job(name=task.finalize_job, namespace=settings.kube_namespace)
            phase = _job_phase(job)
        except ApiException as exc:
            if exc.status == 404:
                task.status = "failed"
                task.detail = "Finalize job not found"
                task.error_message = "finalize job disappeared before completion"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                return task
            raise

        if phase in {"running", "pending"}:
            task.detail = "Finalizing image format/checksum on cluster"
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

        if phase == "failed":
            task.status = "failed"
            task.detail = "Finalize job failed"
            task.error_message = _read_job_log(task.finalize_job, tail_lines=120) or "finalize job failed"
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

        try:
            out_name, out_size, out_sha = _parse_finalize_log(_read_job_log(task.finalize_job, tail_lines=200))
            task.filename = out_name
            task.size_bytes = out_size
            task.checksum = out_sha
            task.detail = "Preparing source PVC copy job"
            task.error_message = None
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
        except Exception as exc:
            task.status = "failed"
            task.detail = "Failed to parse finalize output"
            task.error_message = str(exc)
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

        try:
            claim_name, copy_job = _create_task_copy_job(task)
            task.source_pvc = claim_name
            task.copy_job = copy_job
            task.status = "importing"
            task.detail = (
                "Importing image into clone source PVC via CDI DataVolume"
                if copy_job.startswith("dv:")
                else "Copying image into clone source PVC"
            )
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task
        except Exception as exc:
            task.status = "failed"
            task.detail = "Failed to start source PVC copy job"
            task.error_message = str(exc)
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    if task.status == "importing":
        if not task.copy_job:
            try:
                claim_name, copy_job = _create_task_copy_job(task)
                task.source_pvc = claim_name
                task.copy_job = copy_job
                task.detail = (
                    "Importing image into clone source PVC via CDI DataVolume"
                    if copy_job.startswith("dv:")
                    else "Copying image into clone source PVC"
                )
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
            except Exception as exc:
                task.status = "failed"
                task.detail = "Failed to start source PVC copy job"
                task.error_message = str(exc)
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

        if task.copy_job.startswith("dv:"):
            dv_name = task.copy_job.split(":", 1)[1]
            try:
                dv_phase, dv_msg = _datavolume_phase(dv_name)
            except ApiException as exc:
                if exc.status == 404:
                    task.status = "failed"
                    task.detail = "DataVolume import not found"
                    task.error_message = "datavolume disappeared before completion"
                    task.updated_at = datetime.utcnow()
                    session.add(task)
                    session.commit()
                    session.refresh(task)
                    return task
                raise

            phase_lower = dv_phase.lower()
            if phase_lower not in {"succeeded", "failed"}:
                task.detail = "Importing image into clone source PVC via CDI DataVolume"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                return task
            if phase_lower == "failed":
                task.status = "failed"
                task.detail = "CDI DataVolume import failed"
                task.error_message = dv_msg or "datavolume import failed"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                _cleanup_fileserver(task.id)
                return task
            _cleanup_fileserver(task.id)
        else:
            try:
                job = batch.read_namespaced_job(name=task.copy_job, namespace=settings.kube_namespace)
                phase = _job_phase(job)
            except ApiException as exc:
                if exc.status == 404:
                    task.status = "failed"
                    task.detail = "Source PVC copy job not found"
                    task.error_message = "copy job disappeared before completion"
                    task.updated_at = datetime.utcnow()
                    session.add(task)
                    session.commit()
                    session.refresh(task)
                    return task
                raise

            if phase in {"running", "pending"}:
                task.detail = "Copying image into clone source PVC"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

            if phase == "failed":
                task.status = "failed"
                task.detail = "Source PVC copy failed"
                task.error_message = _read_job_log(task.copy_job, tail_lines=120) or "copy job failed"
                task.updated_at = datetime.utcnow()
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

        try:
            _upsert_image_from_task(task, session)
            task.status = "completed"
            task.detail = "Image ready"
            task.error_message = None
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
            _cleanup_task_jobs(task)
        except Exception as exc:
            task.status = "failed"
            task.detail = "Failed to register image metadata"
            task.error_message = str(exc)
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
            session.refresh(task)
    return task


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


def _ensure_free_space(required_free_bytes: int, *, context: str) -> None:
    free_bytes = shutil.disk_usage(IMAGE_DIR).free
    if free_bytes >= required_free_bytes:
        return
    free_gib = free_bytes / (1024 ** 3)
    required_gib = required_free_bytes / (1024 ** 3)
    raise HTTPException(
        status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        detail=f"insufficient free storage for {context} (free={free_gib:.1f}Gi, required={required_gib:.1f}Gi)",
    )


def _cleanup_stale_helper_pods(max_age_minutes: int = 20) -> None:
    try:
        core = kube._client()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=max_age_minutes)
        for pod in core.list_namespaced_pod(namespace=settings.kube_namespace).items:
            name = pod.metadata.name or ""
            if not name.startswith("image-sync-"):
                continue
            phase = (pod.status.phase or "").lower()
            created = pod.metadata.creation_timestamp
            if phase in {"succeeded", "failed"} or (created and created < cutoff):
                core.delete_namespaced_pod(
                    name=name,
                    namespace=settings.kube_namespace,
                    grace_period_seconds=0,
                    propagation_policy="Background",
                )
    except Exception:
        logger.warning("Failed to cleanup stale image helper pods", exc_info=True)


def _with_pvc_helper(
    command: list[str],
    *,
    image: str | None = None,
    capture_output: bool = True,
    claim_name: str | None = None,
) -> subprocess.CompletedProcess:
    helper = f"image-sync-{uuid4().hex[:8]}"
    helper_image = image or PVC_HELPER_IMAGE
    claim = claim_name or settings.kube_image_pvc
    _cleanup_stale_helper_pods()
    pod_spec = _helper_overrides(helper_image, claim)
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
                helper_image,
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


def _copy_file_to_pvc(source_path: Path, filename: str, *, claim_name: str | None = None) -> None:
    """
    Copy an image file from golden-images PVC into a target source PVC using a Kubernetes Job.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"source file not found: {source_path}")
    core = kube._client()
    batch = client.BatchV1Api()
    source_claim = settings.kube_image_pvc
    target_claim = claim_name or settings.kube_image_pvc
    if source_claim == target_claim:
        return

    job_name = f"img-copy-{uuid4().hex[:8]}"
    container = client.V1Container(
        name="copy",
        image=PVC_HELPER_IMAGE,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="FILENAME", value=filename),
        ],
        command=["/bin/sh", "-c"],
        args=[
            r"""
set -euo pipefail
src="/source/${FILENAME}"
dst="/target/${FILENAME}"
if [ ! -f "${src}" ]; then
  echo "BLABS_ERROR=source missing: ${src}"
  exit 20
fi
cp -f "${src}" "${dst}"
sync
echo "BLABS_COPY_SIZE=$(wc -c < "${dst}")"
"""
        ],
        volume_mounts=[
            client.V1VolumeMount(name="source", mount_path="/source", read_only=True),
            client.V1VolumeMount(name="target", mount_path="/target", read_only=False),
        ],
    )
    spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            client.V1Volume(
                name="source",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=source_claim),
            ),
            client.V1Volume(
                name="target",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=target_claim),
            ),
        ],
    )
    if settings.image_pull_secret:
        spec.image_pull_secrets = [client.V1LocalObjectReference(name=settings.image_pull_secret)]

    body = client.V1Job(
        metadata=client.V1ObjectMeta(name=job_name, namespace=settings.kube_namespace),
        spec=client.V1JobSpec(
            backoff_limit=1,
            active_deadline_seconds=COPY_JOB_TIMEOUT_SECONDS,
            ttl_seconds_after_finished=TASK_RETENTION_HOURS * 3600,
            template=client.V1PodTemplateSpec(spec=spec),
        ),
    )
    batch.create_namespaced_job(namespace=settings.kube_namespace, body=body)

    deadline = time.time() + COPY_JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        job = batch.read_namespaced_job(name=job_name, namespace=settings.kube_namespace)
        phase = _job_phase(job)
        if phase == "succeeded":
            break
        if phase == "failed":
            break
        time.sleep(2)
    else:
        raise RuntimeError(f"timed out waiting for copy job {job_name}")

    job = batch.read_namespaced_job(name=job_name, namespace=settings.kube_namespace)
    if _job_phase(job) != "succeeded":
        pods = core.list_namespaced_pod(
            namespace=settings.kube_namespace,
            label_selector=f"job-name={job_name}",
        ).items
        pod_name = pods[0].metadata.name if pods else ""
        err = ""
        if pod_name:
            try:
                err = core.read_namespaced_pod_log(name=pod_name, namespace=settings.kube_namespace, tail_lines=120)
            except Exception:
                pass
        raise RuntimeError(f"copy job failed for {filename}: {err.strip() or 'job failed'}")

    expected_size = source_path.stat().st_size
    actual_size = _pvc_file_size(filename, claim_name=target_claim)
    if actual_size != expected_size:
        raise RuntimeError(
            f"copied file size mismatch for {filename}: expected {expected_size} bytes, got {actual_size} bytes"
        )


def _source_pvc_name(image_id: str) -> str:
    return f"img-src-{image_id[:8].lower()}"


def _wait_for_pvc_bound(core: client.CoreV1Api, claim_name: str, timeout_seconds: int = 300) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        pvc = core.read_namespaced_persistent_volume_claim(name=claim_name, namespace=settings.kube_namespace)
        phase = (pvc.status.phase or "").lower()
        if phase == "bound":
            return
        if phase == "lost":
            raise RuntimeError(f"PVC {claim_name} entered Lost phase")
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for PVC {claim_name} to bind")


def _wait_for_pvc_deleted(core: client.CoreV1Api, claim_name: str, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            core.read_namespaced_persistent_volume_claim(name=claim_name, namespace=settings.kube_namespace)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for PVC {claim_name} to delete")


def _ensure_image_source_pvc_claim(image_id: str, size_bytes: int) -> str:
    if not settings.kube_vm_storage_class:
        raise RuntimeError("BLABS_KUBE_VM_STORAGE_CLASS is required for clone-based disks")

    claim_name = _source_pvc_name(image_id)
    required_bytes = size_bytes + SOURCE_PVC_OVERHEAD_BYTES
    requested_gi = max(1, math.ceil(required_bytes / (1024 ** 3)))
    core = kube._client()
    existing_pvc = None
    try:
        existing_pvc = core.read_namespaced_persistent_volume_claim(name=claim_name, namespace=settings.kube_namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
    if existing_pvc:
        existing_request = None
        if existing_pvc.spec and existing_pvc.spec.resources and existing_pvc.spec.resources.requests:
            existing_request = existing_pvc.spec.resources.requests.get("storage")
        existing_bytes = int(parse_quantity(existing_request)) if existing_request else 0
        if existing_bytes < required_bytes:
            logger.warning(
                "Recreating source PVC %s with larger capacity (current=%s bytes, required=%s bytes)",
                claim_name,
                existing_bytes,
                required_bytes,
            )
            core.delete_namespaced_persistent_volume_claim(name=claim_name, namespace=settings.kube_namespace)
            _wait_for_pvc_deleted(core, claim_name)
            existing_pvc = None

    if not existing_pvc:
        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=claim_name,
                labels={"app.kubernetes.io/part-of": "bretter-labs", "image-id": image_id},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=settings.kube_vm_storage_class,
                resources=client.V1ResourceRequirements(requests={"storage": f"{requested_gi}Gi"}),
            ),
        )
        core.create_namespaced_persistent_volume_claim(namespace=settings.kube_namespace, body=body)
        _wait_for_pvc_bound(core, claim_name)
    return claim_name


def _ensure_image_source_pvc(image_id: str, image_path: Path, size_bytes: int) -> str:
    # Always size the source PVC from the on-disk file size as a floor. Some qcow2
    # uploads can have stale/under-reported metadata sizes, which causes short PVCs.
    source_size_bytes = max(size_bytes, image_path.stat().st_size)
    claim_name = _ensure_image_source_pvc_claim(image_id, source_size_bytes)

    expected_size = image_path.stat().st_size
    copy_needed = True
    if _exists_on_pvc(image_path.name, claim_name=claim_name):
        existing_size = _pvc_file_size(image_path.name, claim_name=claim_name)
        if existing_size == expected_size:
            copy_needed = False
        else:
            logger.warning(
                "Refreshing source image %s in PVC %s due size mismatch (pvc=%s, host=%s)",
                image_path.name,
                claim_name,
                existing_size,
                expected_size,
            )
    if copy_needed:
        _copy_file_to_pvc(image_path, image_path.name, claim_name=claim_name)
    return claim_name


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


def _exists_on_pvc(filename: str, *, claim_name: str | None = None) -> bool:
    try:
        _with_pvc_helper(
            ["/bin/sh", "-c", f"test -f /images/{filename}"],
            capture_output=False,
            claim_name=claim_name,
        )
        return True
    except Exception:
        return False


def _pvc_file_size(filename: str, *, claim_name: str | None = None) -> int:
    safe_filename = filename.replace("'", "'\"'\"'")
    result = _with_pvc_helper(
        ["/bin/sh", "-c", f"wc -c < '/images/{safe_filename}'"],
        claim_name=claim_name,
    )
    return int((result.stdout or "0").strip() or "0")


def _convert_image_on_pvc(filename: str, *, output_format: str, output_suffix: str) -> str:
    """
    Convert an image on the PVC to the requested output format.
    """
    stem = Path(filename).stem
    converted_name = f"{stem}.{output_suffix}"
    if _exists_on_pvc(converted_name):
        # Avoid clobbering an existing normalized image with the same stem.
        converted_name = f"{stem}-{uuid4().hex[:8]}.{output_suffix}"
    cmd = f"qemu-img convert -O {output_format} /images/{filename} /images/{converted_name} && sync"
    _with_pvc_helper(
        ["/bin/sh", "-c", cmd],
        image=settings.runner_image,
    )
    # Remove original after successful conversion to save space.
    try:
        _with_pvc_helper(["/bin/sh", "-c", f"rm -f /images/{filename}"])
    except Exception:
        logger.warning("Failed to delete source image after conversion: %s", filename)
    return converted_name


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


class DirectUploadStart(BaseModel):
    filename: str
    size_bytes: int


class DirectUploadSession(BaseModel):
    task: ImageUploadTaskStatus
    upload_url: str
    upload_token: str


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


@router.post("/images", response_model=ImageUploadTaskStatus, status_code=status.HTTP_202_ACCEPTED)
def upload_image(file: UploadFile = File(...), session: Session = Depends(get_session)) -> ImageUploadTaskStatus:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM storage is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    size_bytes = 0
    filename = Path(file.filename).name
    task_id = str(uuid4())
    image_id = str(uuid4())
    try:
        dest_path = IMAGE_DIR / filename
        with dest_path.open("wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                _ensure_free_space(MIN_FREE_UPLOAD_BYTES + len(chunk), context="upload")
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="image too large (max 60GB)",
                    )
                buffer.write(chunk)
        if size_bytes == 0:
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to upload %s: %s", filename, exc, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"upload failed: {exc}") from exc

    if size_bytes == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")

    task = ImageUploadTask(
        id=task_id,
        original_filename=Path(file.filename).name,
        filename=filename,
        size_bytes=size_bytes,
        status="finalizing",
        detail="Upload complete; submitting finalize job",
        error_message=None,
        image_id=image_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    try:
        _ensure_upload_task_finalize_job(task)
        session.add(task)
        session.commit()
        session.refresh(task)
    except Exception as exc:
        task.status = "failed"
        task.detail = "Failed to submit finalize job"
        task.error_message = str(exc)
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)

    return _upload_task_out(task)


@router.post("/images/direct-upload/start", response_model=DirectUploadSession, status_code=status.HTTP_202_ACCEPTED)
def start_direct_upload(payload: DirectUploadStart, session: Session = Depends(get_session)) -> DirectUploadSession:
    if not settings.cdi_direct_upload_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="direct CDI upload is disabled")
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM storage is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    if not _has_cdi_datavolume():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="CDI DataVolume CRD is not installed")
    try:
        upload_url = _direct_upload_url()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    filename = Path(payload.filename or "").name
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    if payload.size_bytes <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="size_bytes must be > 0")
    if payload.size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="image too large (max 60GB)",
        )

    task = ImageUploadTask(
        id=str(uuid4()),
        original_filename=filename,
        filename=filename,
        size_bytes=payload.size_bytes,
        status="uploading",
        detail="Ready for direct CDI upload",
        error_message=None,
        image_id=str(uuid4()),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    try:
        task.upload_pvc = _create_direct_upload_datavolume(task)
        token = _request_direct_upload_token(task.upload_pvc)
        task.detail = "Uploading image directly to CDI DataVolume"
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    except Exception as exc:
        task.status = "failed"
        task.detail = "Failed to initialize direct CDI upload"
        task.error_message = str(exc)
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return DirectUploadSession(task=_upload_task_out(task), upload_url=upload_url, upload_token=token)


@router.get("/images/upload-tasks/{task_id}", response_model=ImageUploadTaskStatus)
def get_upload_task(task_id: str, session: Session = Depends(get_session)) -> ImageUploadTaskStatus:
    task = session.get(ImageUploadTask, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")
    try:
        task = _refresh_upload_task(task, session)
    except Exception as exc:
        logger.error("Failed to refresh upload task %s: %s", task_id, exc, exc_info=True)
        task.status = "failed"
        task.detail = "Internal error while refreshing upload task"
        task.error_message = str(exc)
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    return _upload_task_out(task)


@router.post("/images/import", response_model=ImageCreateResponse, status_code=status.HTTP_201_CREATED)
def import_image(payload: ImageImport, session: Session = Depends(get_session)) -> ImageCreateResponse:
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM storage is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    dest_path = IMAGE_DIR / Path(payload.filename).name
    if not dest_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found on storage")
    suffix = dest_path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    if suffix in RAW_CONVERSION_SUFFIXES or suffix in QCOW2_CONVERSION_SUFFIXES:
        try:
            if suffix in RAW_CONVERSION_SUFFIXES:
                converted_name = _convert_image_on_pvc(dest_path.name, output_format="raw", output_suffix="raw")
            else:
                converted_name = _convert_image_on_pvc(dest_path.name, output_format="qcow2", output_suffix="qcow2")
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"image conversion failed: {exc}") from exc
        dest_path = IMAGE_DIR / converted_name
        if not dest_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"converted image missing on storage: {converted_name}",
            )
    existing = session.exec(select(Image).where(Image.filename == dest_path.name)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="image already registered")

    image_id = str(uuid4())
    source_pvc = None

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
    try:
        source_pvc = _ensure_image_source_pvc(image_id, dest_path, size_bytes)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"source pvc provision failed: {exc}") from exc

    record = Image(
        id=image_id,
        name=payload.name or dest_path.name,
        filename=dest_path.name,
        source_pvc=source_pvc,
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
            source_pvc=None,
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
    if record.source_pvc:
        try:
            kube._client().delete_namespaced_persistent_volume_claim(
                name=record.source_pvc,
                namespace=settings.kube_namespace,
            )
        except ApiException as exc:
            if exc.status != 404:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"failed to delete source pvc: {exc.reason}",
                ) from exc
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
    if record.source_pvc and record.filename != new_filename:
        try:
            _with_pvc_helper(
                ["/bin/sh", "-c", f"if [ -f /images/{record.filename} ]; then mv /images/{record.filename} /images/{new_filename}; fi"],
                capture_output=False,
                claim_name=record.source_pvc,
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"source pvc rename failed: {exc}") from exc

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
    if not image.source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not ready for clone-based launch; re-import or re-upload the image",
        )
    pool_min = int(payload.preclone_pool_size or 0)
    pool_max = int(payload.preclone_pool_max or 0)
    if pool_max < pool_min:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preclone_pool_max must be greater than or equal to preclone_pool_size",
        )
    record = Template(
        id=str(uuid4()),
        name=payload.name,
        description=payload.description or "",
        os_type=payload.os_type or "windows",
        image_id=payload.image_id,
        cpu_cores=payload.cpu_cores,
        ram_mb=payload.ram_mb,
        auto_delete_minutes=payload.auto_delete_minutes,
        idle_timeout_minutes=payload.idle_timeout_minutes or settings.idle_timeout_minutes,
        preclone_pool_size=pool_min,
        preclone_pool_max=pool_max,
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
        idle_timeout_minutes=record.idle_timeout_minutes,
        preclone_pool_size=record.preclone_pool_size,
        preclone_pool_max=record.preclone_pool_max,
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
            idle_timeout_minutes=record.idle_timeout_minutes,
            preclone_pool_size=record.preclone_pool_size,
            preclone_pool_max=record.preclone_pool_max,
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
        if not image.source_pvc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="image is not ready for clone-based launch; re-import or re-upload the image",
            )
        record.image_id = payload.image_id
    if payload.cpu_cores is not None:
        record.cpu_cores = payload.cpu_cores
    if payload.ram_mb is not None:
        record.ram_mb = payload.ram_mb
    if payload.auto_delete_minutes is not None:
        record.auto_delete_minutes = payload.auto_delete_minutes
    if payload.idle_timeout_minutes is not None:
        record.idle_timeout_minutes = payload.idle_timeout_minutes
    next_min = record.preclone_pool_size
    next_max = getattr(record, "preclone_pool_max", record.preclone_pool_size)
    if payload.preclone_pool_size is not None:
        next_min = payload.preclone_pool_size
    if payload.preclone_pool_max is not None:
        next_max = payload.preclone_pool_max
    if next_max < next_min:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="preclone_pool_max must be greater than or equal to preclone_pool_size",
        )
    record.preclone_pool_size = next_min
    record.preclone_pool_max = next_max
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
        idle_timeout_minutes=record.idle_timeout_minutes,
        preclone_pool_size=record.preclone_pool_size,
        preclone_pool_max=record.preclone_pool_max,
        enabled=record.enabled,
        network_mode=record.network_mode,
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


@router.get("/alerts-errors", response_model=AlertsAndErrorsView)
def alerts_and_errors() -> AlertsAndErrorsView:
    max_bytes = min(max(1024, int(settings.error_log_max_bytes)), ALERTS_ERRORS_MAX_LOG_BYTES)
    alerts, alertmanager_error = _fetch_alertmanager_alerts()
    log_file_path = _to_str(settings.error_log_file_path)
    if log_file_path:
        error_log = _read_error_log_file(Path(log_file_path), max_bytes=max_bytes)
        if error_log.content.startswith("Log file not found.") or error_log.content.startswith("Failed to read log file:"):
            # Fall back to Kubernetes logs if file logging is not available.
            error_log = _collect_k8s_error_logs(max_bytes=max_bytes)
    else:
        error_log = _collect_k8s_error_logs(max_bytes=max_bytes)

    return AlertsAndErrorsView(
        fetched_at=datetime.now(timezone.utc),
        alertmanager_url=_to_str(settings.alertmanager_api_url),
        alertmanager_error=alertmanager_error,
        alerts=alerts,
        error_log=error_log,
    )


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
        kube_vm_storage_class=settings.kube_vm_storage_class,
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
        kube_vm_storage_class=settings.kube_vm_storage_class,
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
    kube.delete_pod(instance_id, record.owner, disk_pvc=record.disk_pvc)
    session.delete(record)
    session.commit()
