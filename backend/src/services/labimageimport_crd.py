from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException

from ..config import settings
from ..tables import ImageUploadTask

logger = logging.getLogger(__name__)

_ALLOWED_BACKENDS = {"db", "dual", "crd"}


def normalized_image_import_backend() -> str:
    raw = str(getattr(settings, "image_import_backend", "db") or "db").strip().lower()
    return raw if raw in _ALLOWED_BACKENDS else "db"


def image_import_writes_crd() -> bool:
    return normalized_image_import_backend() in {"dual", "crd"}


def image_import_controller_owns_progress() -> bool:
    return normalized_image_import_backend() in {"dual", "crd"}


def _load_kube_config_once() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _custom_objects() -> client.CustomObjectsApi:
    _load_kube_config_once()
    return client.CustomObjectsApi()


def _group() -> str:
    return str(getattr(settings, "labimageimport_crd_group", "labs.bretter.io") or "labs.bretter.io").strip()


def _version() -> str:
    return str(getattr(settings, "labimageimport_crd_version", "v1alpha1") or "v1alpha1").strip()


def _plural() -> str:
    return str(getattr(settings, "labimageimport_crd_plural", "labimageimports") or "labimageimports").strip()


def _finalizer() -> str:
    return str(getattr(settings, "labimageimport_crd_finalizer", "labs.bretter.io/imageimport-finalizer") or "").strip()


def _ts() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _phase_from_task(task: ImageUploadTask) -> str:
    status_value = str(getattr(task, "status", "") or "").strip().lower()
    stage_value = str(getattr(task, "stage", "") or "").strip().lower()
    value = stage_value or status_value
    if value in {"queued", "uploading", "finalizing", "copying", "completed", "failed"}:
        return value.capitalize()
    if status_value in {"completed", "failed"}:
        return status_value.capitalize()
    return "Queued"


def _progress_percent(task: ImageUploadTask) -> int:
    pct = int(getattr(task, "progress_percent", 0) or 0)
    return max(0, min(100, pct))


def _labimageimport_body(task: ImageUploadTask) -> dict[str, Any]:
    filename = str(getattr(task, "filename", "") or "").strip()
    original_filename = str(getattr(task, "original_filename", "") or filename).strip()
    image_id = str(getattr(task, "image_id", "") or "").strip() or task.id
    phase = _phase_from_task(task)
    detail = str(getattr(task, "detail", "") or "").strip()
    status: dict[str, Any] = {
        "phase": phase,
        "progress": {"percent": _progress_percent(task), "detail": detail},
    }
    checksum = str(getattr(task, "checksum", "") or "").strip()
    source_pvc = str(getattr(task, "source_pvc", "") or "").strip()
    if checksum or source_pvc or filename:
        status["artifacts"] = {
            "checksum": checksum,
            "sourcePvc": source_pvc,
            "canonicalFilename": filename,
        }
    error_message = str(getattr(task, "error_message", "") or "").strip()
    if error_message:
        status["lastError"] = {"code": "TaskFailed", "message": error_message[:2048]}

    metadata: dict[str, Any] = {
        "name": task.id,
        "namespace": settings.kube_namespace,
        "labels": {
            "labs.bretter.io/workload-kind": "image-import",
            "labs.bretter.io/image-id": image_id,
        },
    }
    finalizer = _finalizer()
    if finalizer:
        metadata["finalizers"] = [finalizer]

    return {
        "apiVersion": f"{_group()}/{_version()}",
        "kind": "LabImageImport",
        "metadata": metadata,
        "spec": {
            "requestedBy": "admin",
            "source": {"filename": original_filename, "pvc": settings.kube_image_pvc},
            "target": {"imageId": image_id},
            "retries": {"maxAttempts": max(0, int(getattr(task, "max_retries", 0) or 0))},
        },
        "status": status,
    }


def upsert_labimageimport_for_task(task: ImageUploadTask) -> None:
    body = _labimageimport_body(task)
    custom = _custom_objects()
    namespace = settings.kube_namespace
    try:
        custom.create_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=namespace,
            plural=_plural(),
            body=body,
        )
        return
    except ApiException as exc:
        if exc.status != 409:
            raise

    custom.patch_namespaced_custom_object(
        group=_group(),
        version=_version(),
        namespace=namespace,
        plural=_plural(),
        name=task.id,
        body={"spec": body["spec"], "metadata": {"labels": body["metadata"].get("labels", {})}},
    )
    custom.patch_namespaced_custom_object_status(
        group=_group(),
        version=_version(),
        namespace=namespace,
        plural=_plural(),
        name=task.id,
        body={"status": body["status"]},
    )


def patch_labimageimport_status_for_task(task: ImageUploadTask) -> None:
    body = _labimageimport_body(task)
    _custom_objects().patch_namespaced_custom_object_status(
        group=_group(),
        version=_version(),
        namespace=settings.kube_namespace,
        plural=_plural(),
        name=task.id,
        body={"status": body["status"]},
    )


def delete_labimageimport(task_id: str, *, missing_ok: bool = True) -> None:
    custom = _custom_objects()
    try:
        custom.delete_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=settings.kube_namespace,
            plural=_plural(),
            name=task_id,
        )
    except ApiException as exc:
        if missing_ok and exc.status == 404:
            return
        raise


def delete_labimageimport_best_effort(task_id: str) -> None:
    try:
        delete_labimageimport(task_id, missing_ok=True)
    except Exception:
        logger.warning("Failed to delete LabImageImport CRD for task=%s", task_id, exc_info=True)


def list_labimageimports() -> list[dict[str, Any]]:
    payload = _custom_objects().list_namespaced_custom_object(
        group=_group(),
        version=_version(),
        namespace=settings.kube_namespace,
        plural=_plural(),
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    return list(items) if isinstance(items, list) else []
