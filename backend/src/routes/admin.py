import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import shlex
import shutil
import sqlite3
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as urlquote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
import requests
from sqlalchemy import text
from sqlmodel import Session, func, select
from kubernetes import client
from kubernetes.client import ApiException
from kubernetes.stream import stream
from kubernetes.utils import parse_quantity

from ..auth import hash_password, require_permission, require_user, revoke_tokens
from ..console_providers import normalize_vm_console_provider
from ..config import settings
from ..db import SQLITE_DB, get_session, session_scope
from ..models import (
    AdminLaunchTaskOut,
    AdminOperationActionResult,
    AlertManagerAlert,
    AlertsAndErrorsView,
    AdminAuditEventOut,
    ConcurrencySettings,
    ErrorLogClearResult,
    ErrorLogView,
    IdleTimeoutSettings,
    ImageCreateResponse,
    ImageMeta,
    ImageUploadTaskStatus,
    IsoImageMeta,
    LDAPSettings,
    LDAPSettingsUpdate,
    RuntimeDriftItem,
    RuntimeHealthCheck,
    OrchestrationParityItem,
    OrchestrationParityReport,
    RdpReadinessTelemetry,
    RoleDefinitionCreate,
    RoleDefinitionUpdate,
    RoleCatalogOut,
    RoleManagementCatalogOut,
    StorageSettingsRead,
    StorageSettingsUpdate,
    SiteBackgroundAsset,
    StorageValidationCheck,
    RuntimeSettingsRead,
    SiteSettings,
    SSOSettings,
    SSOSettingsUpdate,
    TeamQuotaCreate,
    TeamQuotaOut,
    TeamQuotaUpdate,
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
from ..network_modes import normalize_vm_network_mode
from ..rbac import (
    Permission,
    Role,
    can_non_platform_assign_role,
    can_access_admin,
    delete_role_definition,
    ensure_user_role_fields,
    list_permissions_for_role,
    normalize_requested_role,
    permission_catalog,
    role_description,
    role_for_user,
    role_is_deletable,
    role_is_editable,
    role_label,
    role_config_payload,
    roles_catalog,
    set_role_definition,
)
from ..services.labimageimport_crd import (
    delete_labimageimport_best_effort,
    image_import_writes_crd,
    patch_labimageimport_status_for_task,
    upsert_labimageimport_for_task,
)
from ..services.labinstance_crd import (
    delete_vm_labinstance,
    delete_vm_labinstance_best_effort,
    vm_orchestration_uses_legacy_path,
    vm_orchestration_writes_crd,
)
from ..services.kubernetes import kube
from ..services.multi_cluster import kube_service_for_cluster, local_cluster_id
from ..services.namespace_policies import get_namespace_runtime_policy
from ..services.team_quotas import normalize_namespace, normalize_optional_limit, normalize_team
from ..services.tenant_context import (
    GLOBAL_TENANT,
    actor_can_access_tenant,
    actor_namespace_scopes,
    actor_tenant,
    assert_actor_can_access_namespace,
    assert_actor_can_manage_tenant,
    is_platform_admin,
    normalize_namespace_scopes,
    normalize_tenant,
    resolve_resource_namespace,
    resolve_resource_tenant,
    set_user_namespace_scopes,
    tenant_namespace_for_team,
    user_namespace_scopes,
)
from ..secret_codec import encrypt_secret, secret_is_configured
from ..tables import (
    Config,
    ContainerImage,
    ContainerInstance as ContainerInstanceTable,
    ContainerTemplate,
    Image,
    IsoImage,
    ImageUploadTask,
    Instance,
    ManagedNamespace,
    TeamQuota,
    Template,
    User,
)
from ..tables import AdminAuditEvent
from ..time_utils import utc_now

router = APIRouter(dependencies=[Depends(require_permission(Permission.ADMIN_ACCESS))])
logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024 * 1024  # 60 GB
ALLOWED_SUFFIXES = {".vhd", ".vhdx", ".qcow", ".qcow2", ".vdi"}
ALLOWED_ISO_SUFFIXES = {".iso"}
RAW_CONVERSION_SUFFIXES = {".qcow", ".qcow2"}
QCOW2_CONVERSION_SUFFIXES = {".vhd", ".vhdx", ".vdi"}
MIN_FREE_UPLOAD_BYTES = 18 * 1024 * 1024 * 1024  # keep nodefs above kubelet disk-pressure headroom
SOURCE_PVC_OVERHEAD_BYTES = 1024 * 1024 * 1024  # account for filesystem metadata/lost+found overhead
MIN_UPLOAD_PVC_GIB = max(1, int(getattr(settings, "min_upload_pvc_gib", 80) or 80))
SITE_BACKGROUND_MAX_BYTES = 20 * 1024 * 1024
SITE_BACKGROUND_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
SITE_BACKGROUND_PUBLIC_PREFIX = "/user/site-assets/"

# Reuse the runner image for helper pods so fresh/private clusters do not depend on Docker Hub pulls.
PVC_HELPER_IMAGE = settings.runner_image or "alpine:3.19"
POD_READY_WAIT_SECONDS = 120
POD_READY_SLEEP = 2
FINALIZE_JOB_TIMEOUT_SECONDS = 3 * 60 * 60
COPY_JOB_TIMEOUT_SECONDS = 3 * 60 * 60
TASK_RETENTION_SECONDS = 300
FINALIZE_MAX_RETRIES = max(0, int(getattr(settings, "image_finalize_max_retries", 3) or 3))
FINALIZE_RETRY_BASE_SECONDS = max(5, int(getattr(settings, "image_finalize_retry_base_seconds", 15) or 15))
FINALIZE_RETRY_MAX_SECONDS = max(30, int(getattr(settings, "image_finalize_retry_max_seconds", 600) or 600))
UPLOAD_STAGE_UPLOADED = "uploaded"
UPLOAD_STAGE_NORMALIZING = "normalizing"
UPLOAD_STAGE_SEEDED = "seeded"
UPLOAD_STAGE_READY = "ready"
UPLOAD_STAGE_FAILED = "failed"
UPLOAD_ACTIVE_STATUSES = {"queued", "uploading", "finalizing", "importing", "pending", "running"}
UPLOAD_FAILED_STATUSES = {"failed", "error"}
LAUNCH_ACTIVE_STATUSES = {"queued", "pending", "failed", "error"}

_CDI_AVAILABLE: bool | None = None
FINALIZE_PROGRESS_RE = re.compile(r"(?<![-0-9.])([0-9]+(?:\.[0-9]+)?)\s*/\s*100%")
ALERTS_ERRORS_MAX_LOG_BYTES = 10 * 1024 * 1024
ERROR_LOG_PAGE_SIZE = 50
ERROR_LOG_LINE_RE = re.compile(r"(error|exception|traceback|critical|failed)", re.IGNORECASE)
AUDIT_NAMESPACE_DETAIL_RE = re.compile(r"(?:^|[\s,;])namespace=([a-z0-9]([-a-z0-9]*[a-z0-9])?)")
ADMIN_AUDIT_EVENT_MAX_PER_TENANT = 50
RUNTIME_ENV_NAMES: dict[str, str] = {
    "storage_root": "BLABS_STORAGE_ROOT",
    "iso_storage_root": "BLABS_ISO_STORAGE_ROOT",
    "kube_namespace": "BLABS_KUBE_NAMESPACE",
    "kube_image_pvc": "BLABS_KUBE_IMAGE_PVC",
    "kube_runtime_class": "BLABS_KUBE_RUNTIME_CLASS",
    "kube_vm_storage_class": "BLABS_KUBE_VM_STORAGE_CLASS",
    "runner_image": "BLABS_RUNNER_IMAGE",
    "image_pull_secret": "BLABS_IMAGE_PULL_SECRET",
    "kube_node_selector_key": "BLABS_KUBE_NODE_SELECTOR_KEY",
    "kube_node_selector_value": "BLABS_KUBE_NODE_SELECTOR_VALUE",
    "kube_use_kvm": "BLABS_KUBE_USE_KVM",
    "kube_spice_embed_configmap": "BLABS_KUBE_SPICE_EMBED_CONFIGMAP",
    "kube_node_external_host": "BLABS_KUBE_NODE_EXTERNAL_HOST",
}
RUNTIME_APPLY_BEHAVIOR: dict[str, str] = {
    "storage_root": "Immediate for current backend process; persists via DB override.",
    "iso_storage_root": "Immediate for ISO upload/read operations; persists via DB override.",
    "kube_image_pvc": "Immediate for new image operations; persists via DB override.",
    "kube_vm_storage_class": "Immediate for new clone operations; persists via DB override.",
    "kube_namespace": "Environment controlled. Requires backend rollout after env change.",
    "kube_runtime_class": "Environment controlled. Requires backend rollout after env change.",
    "runner_image": "Environment controlled. Requires backend rollout after env change.",
    "image_pull_secret": "Environment controlled. Requires backend rollout after env change.",
    "kube_node_selector_key": "Environment controlled. Requires backend rollout after env change.",
    "kube_node_selector_value": "Environment controlled. Requires backend rollout after env change.",
    "kube_use_kvm": "Environment controlled. Requires backend rollout after env change.",
    "kube_spice_embed_configmap": "Environment controlled. Requires backend rollout after env change.",
    "kube_node_external_host": "Environment controlled. Requires backend rollout after env change.",
}


def _image_dir() -> Path:
    path = Path(settings.storage_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iso_dir() -> Path:
    path = Path(settings.iso_storage_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _site_assets_dir() -> Path:
    path = Path(settings.site_assets_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _site_background_public_path(filename: str) -> str:
    return f"{SITE_BACKGROUND_PUBLIC_PREFIX}{filename}"


def _site_background_local_filename(public_path: str | None) -> str | None:
    raw = str(public_path or "").strip()
    if not raw.startswith(SITE_BACKGROUND_PUBLIC_PREFIX):
        return None
    relative = raw[len(SITE_BACKGROUND_PUBLIC_PREFIX) :]
    safe_name = Path(relative).name
    if not safe_name:
        return None
    return safe_name


def _delete_local_site_background(public_path: str | None) -> None:
    filename = _site_background_local_filename(public_path)
    if not filename:
        return
    path = _site_assets_dir() / filename
    try:
        if path.exists():
            path.unlink()
    except Exception:
        logger.warning("Failed to remove old site background %s", path, exc_info=True)


def _to_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tenant_scope_for_actor(actor: User, *, include_global: bool = True) -> set[str] | None:
    if is_platform_admin(actor):
        return None
    scoped = {actor_tenant(actor), "default"}
    if include_global:
        scoped.add(GLOBAL_TENANT)
    return scoped


def _tenant_scoped_record(record: object, actor: User, *, include_global: bool = True) -> bool:
    if is_platform_admin(actor):
        return True
    tenant = normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT)
    scope = _tenant_scope_for_actor(actor, include_global=include_global) or set()
    return tenant in scope


def _namespace_scope_for_actor(actor: User) -> set[str] | None:
    return actor_namespace_scopes(actor)


def _record_namespace(record: object) -> str:
    return (
        normalize_namespace(getattr(record, "namespace", None))
        or normalize_namespace(settings.kube_namespace)
        or "labs"
    )


def _namespace_scoped_record(record: object, actor: User) -> bool:
    if is_platform_admin(actor):
        return True
    scope = _namespace_scope_for_actor(actor)
    if scope is None:
        return True
    return _record_namespace(record) in scope


def _record_shared_catalog(record: object) -> bool:
    return bool(getattr(record, "shared_catalog", False))


def _explicit_namespace(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    return normalize_namespace(raw)


def _record_visible_for_namespace(record: object, namespace: str | None) -> bool:
    selected = _explicit_namespace(namespace)
    if not selected:
        return True
    record_namespace = _record_namespace(record)
    return record_namespace == selected or (_record_shared_catalog(record) and record_namespace != "")


def _record_visible_for_actor(record: object, actor: User, *, requested_namespace: str | None = None) -> bool:
    if not _tenant_scoped_record(record, actor, include_global=True):
        return False
    if is_platform_admin(actor):
        return _record_visible_for_namespace(record, requested_namespace)
    scope = _namespace_scope_for_actor(actor)
    record_namespace = _record_namespace(record)
    in_scope = scope is None or record_namespace in scope
    if in_scope and _record_visible_for_namespace(record, requested_namespace):
        return True
    if not _record_shared_catalog(record):
        return False
    if requested_namespace:
        return scope is None or requested_namespace in scope
    return bool(scope) or scope is None


def _requested_namespace_hint(request: Request | None) -> str | None:
    if request is None:
        return None
    for header in ("x-bretter-namespace", "x-blabs-namespace"):
        value = _explicit_namespace(request.headers.get(header))
        if value:
            return value
    query_value = _explicit_namespace(request.query_params.get("namespace"))
    return query_value or None


def _format_bytes(value: int) -> str:
    if value <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


def _get_or_create_config(session: Session) -> Config:
    cfg = session.get(Config, 1)
    if cfg:
        return cfg
    cfg = Config(
        id=1,
        max_concurrent_vms=settings.max_concurrent_vms,
        per_user_vm_limit=settings.per_user_vm_limit,
        idle_timeout_minutes=settings.idle_timeout_minutes,
    )
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg


def _effective_storage_values(cfg: Config | None) -> tuple[str, str, str, dict[str, str]]:
    source: dict[str, str] = {}

    if cfg and cfg.storage_root_override is not None and _to_str(cfg.storage_root_override):
        storage_root = _to_str(cfg.storage_root_override)
        source["storage_root"] = "database override"
    else:
        storage_root = _to_str(settings.storage_root)
        source["storage_root"] = "environment"

    if cfg and cfg.kube_image_pvc_override is not None and _to_str(cfg.kube_image_pvc_override):
        kube_image_pvc = _to_str(cfg.kube_image_pvc_override)
        source["kube_image_pvc"] = "database override"
    else:
        kube_image_pvc = _to_str(settings.kube_image_pvc)
        source["kube_image_pvc"] = "environment"

    if cfg and cfg.kube_vm_storage_class_override is not None:
        kube_vm_storage_class = _to_str(cfg.kube_vm_storage_class_override)
        source["kube_vm_storage_class"] = "database override"
    else:
        kube_vm_storage_class = _to_str(settings.kube_vm_storage_class)
        source["kube_vm_storage_class"] = "environment"

    return storage_root, kube_image_pvc, kube_vm_storage_class, source


def _apply_runtime_storage_settings(storage_root: str, kube_image_pvc: str, kube_vm_storage_class: str) -> None:
    settings.storage_root = storage_root
    settings.kube_image_pvc = kube_image_pvc
    settings.kube_vm_storage_class = kube_vm_storage_class
    _image_dir()


def _serialize_runtime_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return _to_str(value)


def _runtime_config_values(cfg: Config | None) -> tuple[dict[str, object], dict[str, str]]:
    storage_root, kube_image_pvc, kube_vm_storage_class, sources = _effective_storage_values(cfg)
    values: dict[str, object] = {
        "storage_root": storage_root,
        "kube_namespace": settings.kube_namespace,
        "kube_image_pvc": kube_image_pvc,
        "kube_runtime_class": settings.kube_runtime_class,
        "kube_vm_storage_class": kube_vm_storage_class,
        "runner_image": settings.runner_image,
        "image_pull_secret": settings.image_pull_secret,
        "kube_node_selector_key": settings.kube_node_selector_key,
        "kube_node_selector_value": settings.kube_node_selector_value,
        "kube_use_kvm": settings.kube_use_kvm,
        "kube_spice_embed_configmap": settings.kube_spice_embed_configmap,
        "kube_node_external_host": settings.kube_node_external_host,
    }
    for key in values:
        sources.setdefault(key, "environment")
    return values, sources


def _runtime_drift(values: dict[str, object], kube_namespace: str) -> tuple[list[RuntimeDriftItem], int]:
    drift: list[RuntimeDriftItem] = []
    try:
        core = kube._client()
        pods = core.list_namespaced_pod(namespace=kube_namespace, label_selector="app=bretter-backend").items
    except Exception:
        return drift, 0

    for pod in pods:
        pod_name = _to_str(pod.metadata.name)
        backend_container = None
        for container in pod.spec.containers or []:
            if _to_str(container.name) == "backend":
                backend_container = container
                break
        if backend_container is None and (pod.spec.containers or []):
            backend_container = pod.spec.containers[0]
        if backend_container is None:
            continue

        pod_env: dict[str, str] = {}
        for env_item in backend_container.env or []:
            env_name = _to_str(env_item.name)
            if not env_name:
                continue
            if env_item.value is not None:
                pod_env[env_name] = _to_str(env_item.value)
            elif env_item.value_from is not None:
                pod_env[env_name] = "<valueFrom>"

        for field_key, env_name in RUNTIME_ENV_NAMES.items():
            if env_name not in pod_env:
                continue
            pod_value = _to_str(pod_env[env_name])
            if pod_value == "<valueFrom>":
                continue
            configured_value = _serialize_runtime_value(values.get(field_key))
            if pod_value != configured_value:
                drift.append(
                    RuntimeDriftItem(
                        field_key=field_key,
                        env_var=env_name,
                        pod_name=pod_name,
                        configured_value=configured_value,
                        pod_value=pod_value,
                        detail=f"{env_name} in pod {pod_name} differs from effective runtime configuration.",
                    )
                )

    return drift, len(pods)


def _phase_to_db_status(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"running", "pending", "completed", "stopped", "failed"}:
        return normalized
    if normalized in {"starting", "building"}:
        return "pending"
    return "unknown"


def _runtime_orchestration_parity_report(session: Session) -> OrchestrationParityReport:
    mode = str(getattr(settings, "orchestration_backend", "db") or "db").strip().lower()
    if mode not in {"dual", "crd"}:
        return OrchestrationParityReport(
            available=False,
            detail=f"ORCHESTRATION_BACKEND={mode or 'db'} does not use LabInstance CRDs.",
            mode=mode or "db",
        )

    db_rows = session.exec(select(Instance)).all()
    db_map = {row.id: str(row.status or "").strip().lower() for row in db_rows}

    try:
        kube._client()
        custom = client.CustomObjectsApi()
        payload = custom.list_namespaced_custom_object(
            group=str(settings.labinstance_crd_group or "labs.bretter.io"),
            version=str(settings.labinstance_crd_version or "v1alpha1"),
            namespace=settings.kube_namespace,
            plural=str(settings.labinstance_crd_plural or "labinstances"),
        )
    except ApiException as exc:
        return OrchestrationParityReport(
            available=False,
            detail=f"Unable to list LabInstance CRDs ({exc.status} {exc.reason}).",
            mode=mode,
            db_instances=len(db_map),
        )
    except Exception as exc:
        return OrchestrationParityReport(
            available=False,
            detail=f"Unable to list LabInstance CRDs ({exc}).",
            mode=mode,
            db_instances=len(db_map),
        )

    crd_items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(crd_items, list):
        crd_items = []
    crd_map: dict[str, str] = {}
    for item in crd_items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        status_obj = item.get("status") if isinstance(item.get("status"), dict) else {}
        name = str(metadata.get("name") or "").strip()
        if not name:
            continue
        crd_map[name] = _phase_to_db_status(status_obj.get("phase"))

    missing_in_crd = sorted(instance_id for instance_id in db_map if instance_id not in crd_map)
    missing_in_db = sorted(instance_id for instance_id in crd_map if instance_id not in db_map)

    status_mismatch: list[OrchestrationParityItem] = []
    for instance_id, db_status in db_map.items():
        crd_phase = crd_map.get(instance_id)
        if not crd_phase:
            continue
        if db_status != crd_phase:
            status_mismatch.append(
                OrchestrationParityItem(instance_id=instance_id, db_status=db_status, crd_phase=crd_phase)
            )
    status_mismatch.sort(key=lambda item: item.instance_id)

    return OrchestrationParityReport(
        available=True,
        detail=f"Compared {len(db_map)} DB instance(s) with {len(crd_map)} LabInstance CRD object(s).",
        mode=mode,
        db_instances=len(db_map),
        crd_instances=len(crd_map),
        missing_in_crd=len(missing_in_crd),
        missing_in_db=len(missing_in_db),
        status_mismatch=len(status_mismatch),
        missing_in_crd_samples=missing_in_crd[:25],
        missing_in_db_samples=missing_in_db[:25],
        status_mismatch_samples=status_mismatch[:25],
    )


def _build_runtime_health(
    *,
    values: dict[str, object],
    drift: list[RuntimeDriftItem],
    backend_pod_count: int,
) -> tuple[str, list[RuntimeHealthCheck]]:
    checks: list[RuntimeHealthCheck] = []
    storage_checks, _ = _build_storage_validation(
        storage_root=_to_str(values.get("storage_root")),
        kube_namespace=_to_str(values.get("kube_namespace")),
        kube_image_pvc=_to_str(values.get("kube_image_pvc")),
        kube_vm_storage_class=_to_str(values.get("kube_vm_storage_class")),
    )
    for check in storage_checks:
        checks.append(
            RuntimeHealthCheck(
                key=check.key,
                status=check.status,
                title=check.title,
                detail=check.detail,
            )
        )

    if drift:
        checks.append(
            RuntimeHealthCheck(
                key="runtime_drift",
                status="warn",
                title="Runtime drift",
                detail=f"Detected {len(drift)} backend env drift item(s). Roll out backend after config/env updates.",
            )
        )
    else:
        checks.append(
            RuntimeHealthCheck(
                key="runtime_drift",
                status="ok",
                title="Runtime drift",
                detail="No backend env drift detected across current backend pods.",
            )
        )

    if backend_pod_count <= 0:
        checks.append(
            RuntimeHealthCheck(
                key="backend_pods",
                status="warn",
                title="Backend pods",
                detail="No backend pods found to validate runtime state.",
            )
        )
    else:
        checks.append(
            RuntimeHealthCheck(
                key="backend_pods",
                status="ok",
                title="Backend pods",
                detail=f"Validated runtime state against {backend_pod_count} backend pod(s).",
            )
        )

    if not bool(values.get("kube_use_kvm")):
        checks.append(
            RuntimeHealthCheck(
                key="kvm",
                status="info",
                title="KVM acceleration",
                detail="KVM is disabled by configuration.",
            )
        )
    else:
        try:
            core = kube._client()
            vm_pods = core.list_namespaced_pod(
                namespace=_to_str(values.get("kube_namespace")),
                label_selector="app.kubernetes.io/component=vm-runner",
            ).items
            kvm_signals: list[str] = []
            running = 0
            for pod in vm_pods:
                if _to_str(pod.status.phase).lower() == "running":
                    running += 1
                for status_obj in pod.status.container_statuses or []:
                    candidates = []
                    if status_obj.state and status_obj.state.waiting:
                        candidates.append(status_obj.state.waiting)
                    if status_obj.state and status_obj.state.terminated:
                        candidates.append(status_obj.state.terminated)
                    for state_obj in candidates:
                        text = f"{_to_str(state_obj.reason)} {_to_str(state_obj.message)}".lower()
                        if "kvm" in text or "/dev/kvm" in text:
                            kvm_signals.append(_to_str(pod.metadata.name))
                            break

            if kvm_signals:
                examples = ", ".join(sorted(set(kvm_signals))[:3])
                checks.append(
                    RuntimeHealthCheck(
                        key="kvm",
                        status="error",
                        title="KVM acceleration",
                        detail=f"Detected KVM-related errors on VM runner pod(s): {examples}.",
                    )
                )
            elif running > 0:
                checks.append(
                    RuntimeHealthCheck(
                        key="kvm",
                        status="ok",
                        title="KVM acceleration",
                        detail=f"KVM enabled and {running} VM runner pod(s) currently running.",
                    )
                )
            else:
                checks.append(
                    RuntimeHealthCheck(
                        key="kvm",
                        status="info",
                        title="KVM acceleration",
                        detail="KVM enabled; no active VM runner pods to probe right now.",
                    )
                )
        except Exception as exc:
            checks.append(
                RuntimeHealthCheck(
                    key="kvm",
                    status="warn",
                    title="KVM acceleration",
                    detail=f"Unable to verify KVM runner state: {exc}",
                )
            )

    statuses = {check.status for check in checks}
    if "error" in statuses:
        overall = "critical"
    elif "warn" in statuses:
        overall = "warning"
    elif statuses:
        overall = "healthy"
    else:
        overall = "unknown"
    return overall, checks


def _build_storage_validation(
    *,
    storage_root: str,
    kube_namespace: str,
    kube_image_pvc: str,
    kube_vm_storage_class: str,
) -> tuple[list[StorageValidationCheck], list[str]]:
    checks: list[StorageValidationCheck] = []
    warnings: list[str] = []

    storage_path = Path(storage_root)
    try:
        storage_path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(storage_path)
        free = int(usage.free)
        status = "ok" if free >= MIN_FREE_UPLOAD_BYTES else "warn"
        checks.append(
            StorageValidationCheck(
                key="storage_root",
                status=status,
                title="Backend image path",
                detail=(
                    f"{storage_path} is writable. Free: {_format_bytes(free)} of {_format_bytes(int(usage.total))}."
                    + (" Low free space can cause upload/finalize failures." if status != "ok" else "")
                ),
            )
        )
        if status != "ok":
            warnings.append(
                f"Low free space on backend storage root ({_format_bytes(free)} free). "
                "Increase node disk or cleanup stale upload artifacts."
            )
    except Exception as exc:
        checks.append(
            StorageValidationCheck(
                key="storage_root",
                status="error",
                title="Backend image path",
                detail=f"{storage_path} is not usable by backend: {exc}",
            )
        )
        warnings.append("Backend storage root is not writable; uploads and image finalization will fail.")

    core = None
    try:
        core = kube._client()
    except Exception as exc:
        checks.append(
            StorageValidationCheck(
                key="kube_api",
                status="error",
                title="Kubernetes API",
                detail=f"Failed to initialize Kubernetes client: {exc}",
            )
        )
        warnings.append("Kubernetes API is unavailable; storage checks are incomplete.")
    namespace_ok = False
    if core is not None:
        try:
            core.read_namespace(name=kube_namespace)
            namespace_ok = True
            checks.append(
                StorageValidationCheck(
                    key="kube_namespace",
                    status="ok",
                    title="Kubernetes namespace",
                    detail=f"Namespace {kube_namespace} exists.",
                )
            )
        except ApiException as exc:
            checks.append(
                StorageValidationCheck(
                    key="kube_namespace",
                    status="error",
                    title="Kubernetes namespace",
                    detail=f"Namespace {kube_namespace} lookup failed: {exc.reason or exc.status}",
                )
            )
            warnings.append(f"Kubernetes namespace {kube_namespace} is not reachable; storage checks are incomplete.")
    else:
        checks.append(
            StorageValidationCheck(
                key="kube_namespace",
                status="warn",
                title="Kubernetes namespace",
                detail=f"Skipped namespace check for {kube_namespace} because Kubernetes client failed to initialize.",
            )
        )

    image_pvc = None
    if namespace_ok:
        try:
            image_pvc = core.read_namespaced_persistent_volume_claim(name=kube_image_pvc, namespace=kube_namespace)
            phase = _to_str(image_pvc.status.phase) or "Unknown"
            storage_class = _to_str(image_pvc.spec.storage_class_name) or "unspecified"
            capacity_raw = ""
            if image_pvc.status and image_pvc.status.capacity:
                capacity_raw = _to_str(image_pvc.status.capacity.get("storage"))
            requested_raw = ""
            if image_pvc.spec and image_pvc.spec.resources and image_pvc.spec.resources.requests:
                requested_raw = _to_str(image_pvc.spec.resources.requests.get("storage"))
            capacity_detail = ""
            if requested_raw:
                capacity_detail = f" requested={requested_raw}"
            if capacity_raw:
                capacity_detail += f", capacity={capacity_raw}"
            access_modes = ",".join(image_pvc.spec.access_modes or []) if image_pvc.spec else ""
            status = "ok" if phase.lower() == "bound" else "error"
            checks.append(
                StorageValidationCheck(
                    key="kube_image_pvc",
                    status=status,
                    title="Golden image PVC",
                    detail=(
                        f"PVC {kube_image_pvc} phase={phase}, storageClass={storage_class}"
                        f"{capacity_detail}, accessModes={access_modes or 'unknown'}."
                    ),
                )
            )
            if status != "ok":
                warnings.append(f"Image PVC {kube_image_pvc} is not Bound; VM launches can remain Pending.")
        except ApiException as exc:
            checks.append(
                StorageValidationCheck(
                    key="kube_image_pvc",
                    status="error",
                    title="Golden image PVC",
                    detail=f"PVC {kube_image_pvc} not available in {kube_namespace}: {exc.reason or exc.status}",
                )
            )
            warnings.append(f"Image PVC {kube_image_pvc} is missing in namespace {kube_namespace}.")

    if kube_vm_storage_class:
        storage_v1 = client.StorageV1Api()
        try:
            storage_class_obj = storage_v1.read_storage_class(name=kube_vm_storage_class)
            provisioner = _to_str(storage_class_obj.provisioner) or "unknown"
            volume_binding_mode = _to_str(storage_class_obj.volume_binding_mode) or "default"
            checks.append(
                StorageValidationCheck(
                    key="kube_vm_storage_class",
                    status="ok",
                    title="VM clone storage class",
                    detail=(
                        f"StorageClass {kube_vm_storage_class} exists "
                        f"(provisioner={provisioner}, bindingMode={volume_binding_mode})."
                    ),
                )
            )
            source_pvc_rows: list[tuple[str, str]] = []
            if namespace_ok and core is not None:
                try:
                    pvc_items = core.list_namespaced_persistent_volume_claim(
                        namespace=kube_namespace,
                        label_selector="app.kubernetes.io/part-of=bretter-labs",
                    ).items
                    for pvc in pvc_items:
                        name = _to_str(pvc.metadata.name)
                        if not name.startswith("img-src-"):
                            continue
                        sc = _to_str(pvc.spec.storage_class_name) if pvc.spec else ""
                        source_pvc_rows.append((name, sc))
                except ApiException as exc:
                    checks.append(
                        StorageValidationCheck(
                            key="clone_compatibility",
                            status="warn",
                            title="Clone compatibility",
                            detail=f"Unable to list image source PVCs: {exc.reason or exc.status}",
                        )
                    )

            if source_pvc_rows:
                mismatched = [
                    (name, sc or "unspecified") for name, sc in source_pvc_rows if (sc or "") != kube_vm_storage_class
                ]
                if mismatched:
                    examples = ", ".join(f"{name}({sc})" for name, sc in mismatched[:3])
                    checks.append(
                        StorageValidationCheck(
                            key="clone_compatibility",
                            status="warn",
                            title="Clone compatibility",
                            detail=(
                                f"{len(mismatched)} image source PVC(s) do not use {kube_vm_storage_class} "
                                f"(examples: {examples}). New VM clone PVCs may fail for those images."
                            ),
                        )
                    )
                    warnings.append("Some image source PVCs use a different storage class than VM clone storage class.")
                else:
                    checks.append(
                        StorageValidationCheck(
                            key="clone_compatibility",
                            status="ok",
                            title="Clone compatibility",
                            detail=(
                                f"Detected {len(source_pvc_rows)} image source PVC(s); all use "
                                f"{kube_vm_storage_class} for clone-based launches."
                            ),
                        )
                    )
            else:
                checks.append(
                    StorageValidationCheck(
                        key="clone_compatibility",
                        status="info",
                        title="Clone compatibility",
                        detail="No image source PVCs detected yet. Upload/import an image to validate clone alignment.",
                    )
                )

            if image_pvc and image_pvc.spec:
                staging_sc = _to_str(image_pvc.spec.storage_class_name)
                if staging_sc and staging_sc != kube_vm_storage_class:
                    checks.append(
                        StorageValidationCheck(
                            key="upload_staging_compatibility",
                            status="info",
                            title="Upload staging compatibility",
                            detail=(
                                f"Golden image PVC uses {staging_sc} while VM clone storage class is "
                                f"{kube_vm_storage_class}. This is acceptable when images are imported into "
                                "per-image source PVCs before VM launch."
                            ),
                        )
                    )
        except ApiException as exc:
            checks.append(
                StorageValidationCheck(
                    key="kube_vm_storage_class",
                    status="error",
                    title="VM clone storage class",
                    detail=f"StorageClass {kube_vm_storage_class} lookup failed: {exc.reason or exc.status}",
                )
            )
            warnings.append(
                f"StorageClass {kube_vm_storage_class} is unavailable; clone-only VM launch path will fail."
            )
        except Exception as exc:
            checks.append(
                StorageValidationCheck(
                    key="kube_vm_storage_class",
                    status="error",
                    title="VM clone storage class",
                    detail=f"StorageClass {kube_vm_storage_class} check failed: {exc}",
                )
            )
            warnings.append(f"StorageClass {kube_vm_storage_class} check failed due to Kubernetes API error.")
    else:
        checks.append(
            StorageValidationCheck(
                key="kube_vm_storage_class",
                status="warn",
                title="VM clone storage class",
                detail="Not set. Clone-only VM launch path is disabled; starts may fall back or fail.",
            )
        )
        warnings.append("Set VM clone storage class to keep cross-node clone-based VM launches reliable.")

    cdi_enabled = bool(settings.cdi_direct_upload_enabled)
    cdi_available = _has_cdi_datavolume()
    if cdi_enabled and cdi_available:
        checks.append(
            StorageValidationCheck(
                key="cdi",
                status="ok",
                title="CDI direct upload",
                detail="DataVolume CRD detected and direct upload is enabled.",
            )
        )
    elif cdi_enabled and not cdi_available:
        checks.append(
            StorageValidationCheck(
                key="cdi",
                status="warn",
                title="CDI direct upload",
                detail="Direct upload enabled but DataVolume CRD not detected; uploads can fall back to slower paths.",
            )
        )
        warnings.append("Install/repair CDI so uploads use direct DataVolume flow.")
    else:
        checks.append(
            StorageValidationCheck(
                key="cdi",
                status="info",
                title="CDI direct upload",
                detail="Direct upload is disabled by configuration.",
            )
        )

    return checks, warnings


def _storage_settings_view(cfg: Config | None) -> StorageSettingsRead:
    storage_root, kube_image_pvc, kube_vm_storage_class, sources = _effective_storage_values(cfg)
    checks, warnings = _build_storage_validation(
        storage_root=storage_root,
        kube_namespace=settings.kube_namespace,
        kube_image_pvc=kube_image_pvc,
        kube_vm_storage_class=kube_vm_storage_class,
    )
    return StorageSettingsRead(
        storage_root=storage_root,
        kube_namespace=settings.kube_namespace,
        kube_image_pvc=kube_image_pvc,
        kube_vm_storage_class=kube_vm_storage_class,
        sources=sources,
        checks=checks,
        warnings=warnings,
    )


def _runtime_settings_view(cfg: Config | None) -> RuntimeSettingsRead:
    values, sources = _runtime_config_values(cfg)
    drift, backend_pod_count = _runtime_drift(values, _to_str(values.get("kube_namespace")))
    health_status, health_checks = _build_runtime_health(
        values=values,
        drift=drift,
        backend_pod_count=backend_pod_count,
    )
    return RuntimeSettingsRead(
        storage_root=_to_str(values.get("storage_root")),
        kube_namespace=_to_str(values.get("kube_namespace")),
        kube_image_pvc=_to_str(values.get("kube_image_pvc")),
        kube_runtime_class=_to_str(values.get("kube_runtime_class")),
        kube_vm_storage_class=_to_str(values.get("kube_vm_storage_class")),
        runner_image=_to_str(values.get("runner_image")),
        image_pull_secret=_to_str(values.get("image_pull_secret")),
        kube_node_selector_key=_to_str(values.get("kube_node_selector_key")),
        kube_node_selector_value=_to_str(values.get("kube_node_selector_value")),
        kube_use_kvm=bool(values.get("kube_use_kvm")),
        kube_spice_embed_configmap=_to_str(values.get("kube_spice_embed_configmap")),
        kube_node_external_host=_to_str(values.get("kube_node_external_host")),
        sources=sources,
        apply_behavior=RUNTIME_APPLY_BEHAVIOR,
        env_names=RUNTIME_ENV_NAMES,
        health_status=health_status,
        health_checks=health_checks,
        drift=drift,
        backend_pod_count=backend_pod_count,
    )


def _extract_error_lines(content: str) -> list[str]:
    return [line for line in content.splitlines() if ERROR_LOG_LINE_RE.search(line)]


def _cap_lines_to_bytes(lines: list[str], max_bytes: int) -> tuple[list[str], bool, int]:
    if not lines:
        return [], False, 0
    kept: deque[tuple[str, int]] = deque()
    total_bytes = 0
    truncated = False
    for line in lines:
        line_bytes = len((line + "\n").encode("utf-8", errors="replace"))
        kept.append((line, line_bytes))
        total_bytes += line_bytes
        while total_bytes > max_bytes and kept:
            _, dropped_bytes = kept.popleft()
            total_bytes -= dropped_bytes
            truncated = True
    return [line for line, _ in kept], truncated, total_bytes


def _paginate_lines(lines: list[str], page: int, per_page: int) -> tuple[list[str], int, int, int, bool, bool]:
    total_lines = len(lines)
    if total_lines == 0:
        return [], 1, 1, 0, False, False
    total_pages = max(1, math.ceil(total_lines / per_page))
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    page_lines = lines[start:end]
    return page_lines, page, total_pages, total_lines, page > 1, page < total_pages


def _build_error_log_view(
    source: str, lines: list[str], max_bytes: int, truncated: bool, page: int, per_page: int
) -> ErrorLogView:
    bounded_lines, bounded_truncated, bounded_bytes = _cap_lines_to_bytes(lines, max_bytes=max_bytes)
    page_lines, page, total_pages, total_lines, has_prev, has_next = _paginate_lines(
        bounded_lines, page=page, per_page=per_page
    )
    if page_lines:
        content = "\n".join(page_lines)
    elif total_lines == 0:
        content = "No error lines found."
    else:
        content = ""
    return ErrorLogView(
        source=source,
        bytes=bounded_bytes,
        truncated=truncated or bounded_truncated,
        total_lines=total_lines,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_prev=has_prev,
        has_next=has_next,
        lines=page_lines,
        content=content,
    )


def _read_error_log_file(path: Path, max_bytes: int, page: int, per_page: int) -> ErrorLogView:
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
    filtered_lines = _extract_error_lines(text)
    return _build_error_log_view(
        source=source,
        lines=filtered_lines,
        max_bytes=max_bytes,
        truncated=file_size > max_bytes,
        page=page,
        per_page=per_page,
    )


def _list_backend_pods(core: client.CoreV1Api) -> tuple[list[client.V1Pod], str]:
    namespace = _to_str(settings.kube_namespace)

    def _is_live_backend_pod(pod: client.V1Pod) -> bool:
        pod_name = _to_str(getattr(pod.metadata, "name", ""))
        if not pod_name:
            return False
        if getattr(pod.metadata, "deletion_timestamp", None) is not None:
            return False
        phase = _to_str(getattr(getattr(pod, "status", None), "phase", "")).lower()
        return phase in {"", "running"}

    try:
        labeled = core.list_namespaced_pod(namespace=namespace, label_selector="app=bretter-backend").items
    except Exception as exc:
        logger.warning("Failed listing labeled backend pods in %s: %s", namespace, exc)
        return [], f"Failed to list backend pods: {exc}"

    pods = [pod for pod in labeled if _is_live_backend_pod(pod)]
    if pods:
        return pods, ""

    # Legacy fallback for clusters that do not carry expected labels on backend pods.
    try:
        all_pods = core.list_namespaced_pod(namespace=namespace).items
    except Exception as exc:
        logger.warning("Failed listing pods for backend-name fallback in %s: %s", namespace, exc)
        return [], f"Failed to list backend pods: {exc}"

    pods = []
    for pod in all_pods:
        pod_name = _to_str(getattr(pod.metadata, "name", ""))
        if pod_name and "bretter-backend" in pod_name and _is_live_backend_pod(pod):
            pods.append(pod)
    if not pods:
        return [], "No backend pods found."
    return pods, ""


def _collect_k8s_error_logs(max_bytes: int, page: int, per_page: int) -> ErrorLogView:
    source = f"kubernetes:{settings.kube_namespace}:backend"
    try:
        core = kube._client()
    except Exception as exc:
        return ErrorLogView(
            source=source,
            bytes=0,
            truncated=False,
            content=f"Failed to initialize Kubernetes client: {exc}",
        )

    pods, pods_error = _list_backend_pods(core)
    if not pods:
        return ErrorLogView(source=source, bytes=0, truncated=False, content=pods_error or "No backend pods found.")

    # Most recent pods first so operators see the latest failures first.
    pods_sorted = sorted(
        pods,
        key=lambda pod: (pod.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    lines: list[str] = []
    approx_bytes = 0
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
        except Exception:
            continue
        pod_lines = _extract_error_lines(log_text or "")
        if not pod_lines:
            continue
        prefixed_lines = [f"[{name}] {line}" for line in pod_lines]
        lines.extend(prefixed_lines)
        approx_bytes += sum(len((line + "\n").encode("utf-8", errors="replace")) for line in prefixed_lines)
        if approx_bytes >= max_bytes * 2:
            break

    return _build_error_log_view(
        source=source,
        lines=lines,
        max_bytes=max_bytes,
        truncated=False,
        page=page,
        per_page=per_page,
    )


def _truncate_local_error_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb"):
        pass


def _clear_backend_error_logs(path: Path) -> ErrorLogClearResult:
    source = f"file:{path}"
    clear_cmd = f"mkdir -p {shlex.quote(str(path.parent))} && : > {shlex.quote(str(path))}"
    core = kube._client()
    pods, pods_error = _list_backend_pods(core)
    pod_names = [_to_str(pod.metadata.name) for pod in pods if _to_str(pod.metadata.name)]
    if not pod_names:
        _truncate_local_error_log(path)
        detail = "Cleared local backend error log."
        if pods_error:
            detail = f"{detail} {pods_error}"
        return ErrorLogClearResult(
            source=source,
            cleared_pods=1,
            total_pods=1,
            detail=detail,
        )

    failed_pods: list[str] = []
    cleared = 0
    for pod_name in pod_names:
        try:
            stream(
                core.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=settings.kube_namespace,
                command=["/bin/sh", "-c", clear_cmd],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
            cleared += 1
        except Exception as exc:
            failed_pods.append(f"{pod_name}: {exc}")

    if cleared == 0:
        _truncate_local_error_log(path)
        cleared = 1
        failed_pods.append("Fallback applied: cleared local pod log only.")

    detail = f"Cleared error logs on {cleared}/{len(pod_names)} backend pods."
    if failed_pods:
        detail = f"{detail} {len(failed_pods)} pod(s) failed."
    return ErrorLogClearResult(
        source=source,
        cleared_pods=cleared,
        total_pods=len(pod_names),
        failed_pods=failed_pods,
        detail=detail,
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

    suppressed_names_raw = str(getattr(settings, "alertmanager_suppressed_alert_names", "") or "").strip()
    suppressed_names = {item.strip().lower() for item in suppressed_names_raw.split(",") if item.strip()}

    job_name_re: re.Pattern[str] | None = None
    job_name_pattern = str(getattr(settings, "alertmanager_suppressed_job_name_regex", "") or "").strip()
    if job_name_pattern:
        try:
            job_name_re = re.compile(job_name_pattern)
        except re.error:
            logger.warning("Invalid BLABS_ALERTMANAGER_SUPPRESSED_JOB_NAME_REGEX: %s", job_name_pattern)

    pod_name_re: re.Pattern[str] | None = None
    pod_name_pattern = str(getattr(settings, "alertmanager_suppressed_pod_regex", "") or "").strip()
    if pod_name_pattern:
        try:
            pod_name_re = re.compile(pod_name_pattern)
        except re.error:
            logger.warning("Invalid BLABS_ALERTMANAGER_SUPPRESSED_POD_REGEX: %s", pod_name_pattern)

    def _is_suppressed_alert(alert_name: str, labels: dict[str, str]) -> bool:
        if not suppressed_names:
            return False
        normalized_name = str(alert_name or "").strip().lower()
        if normalized_name not in suppressed_names:
            return False
        if job_name_re is None and pod_name_re is None:
            return True
        job_name = _to_str(labels.get("job_name")) or _to_str(labels.get("job")) or _to_str(labels.get("cronjob"))
        pod_name = _to_str(labels.get("pod"))
        if not job_name and not pod_name:
            # Alerts without workload labels (for example Watchdog) should still be
            # suppressible by explicit alert name.
            return True
        if job_name and job_name_re and job_name_re.search(job_name):
            return True
        if pod_name and pod_name_re and pod_name_re.search(pod_name):
            return True
        return False

    alerts: list[AlertManagerAlert] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        status_obj = item.get("status") if isinstance(item.get("status"), dict) else {}
        alert_name = _to_str(labels.get("alertname")) or "unnamed-alert"
        if _is_suppressed_alert(alert_name, {str(k): _to_str(v) for k, v in labels.items()}):
            continue
        alerts.append(
            AlertManagerAlert(
                name=alert_name,
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


def _collect_rdp_readiness_telemetry(
    session: Session,
    *,
    alerts: list[AlertManagerAlert],
) -> RdpReadinessTelemetry:
    stuck_minutes = max(2, int(getattr(settings, "userflow_slo_rdp_stuck_minutes", 12) or 12))
    warning_threshold = max(0, int(getattr(settings, "userflow_slo_rdp_stuck_warn_max", 0) or 0))
    critical_threshold = max(0, int(getattr(settings, "userflow_slo_rdp_stuck_max", 2) or 2))
    if warning_threshold > critical_threshold:
        warning_threshold = critical_threshold
    cutoff = utc_now() - timedelta(minutes=stuck_minutes)

    totals_row = (
        session.exec(
            text(
                """
                SELECT
                  COUNT(*) AS total_instances,
                  SUM(
                    CASE WHEN lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting')
                    THEN 1 ELSE 0 END
                  ) AS pending_or_starting_instances,
                  SUM(
                    CASE
                      WHEN i.started_at <= :cutoff
                       AND lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting')
                      THEN 1 ELSE 0
                    END
                  ) AS stuck_instances
                FROM instance i
                JOIN template t ON t.id = i.template_id
                WHERE lower(COALESCE(t.console_provider, '')) IN
                  ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp')
                """
            ).bindparams(cutoff=cutoff)
        )
        .mappings()
        .one()
    )

    sample_rows = (
        session.exec(
            text(
                """
                SELECT i.owner AS owner, i.id AS instance_id
                FROM instance i
                JOIN template t ON t.id = i.template_id
                WHERE i.started_at <= :cutoff
                AND lower(COALESCE(i.status, '')) IN ('pending', 'building', 'starting')
                AND lower(COALESCE(t.console_provider, '')) IN
                  ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp')
                ORDER BY i.started_at ASC
                LIMIT 5
                """
            ).bindparams(cutoff=cutoff)
        )
        .mappings()
        .all()
    )
    sample_instances = [f"{str(row['owner'])}/{str(row['instance_id'])[:8]}" for row in sample_rows]

    stuck_instances = int(totals_row.get("stuck_instances") or 0)
    if critical_threshold == 0 and stuck_instances > 0:
        computed_status = "critical"
    elif stuck_instances > critical_threshold:
        computed_status = "critical"
    elif stuck_instances > warning_threshold:
        computed_status = "warning"
    else:
        computed_status = "ok"

    active_rdp_alerts = sorted(
        {
            alert.name
            for alert in alerts
            if str(alert.state or "").lower() == "active" and "rdpreadinessslo" in str(alert.name or "").lower()
        }
    )
    return RdpReadinessTelemetry(
        status=computed_status,
        total_instances=int(totals_row.get("total_instances") or 0),
        pending_or_starting_instances=int(totals_row.get("pending_or_starting_instances") or 0),
        stuck_instances=stuck_instances,
        stuck_minutes_threshold=stuck_minutes,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        sample_instances=sample_instances,
        slo_alert_active=bool(active_rdp_alerts),
        slo_alert_names=active_rdp_alerts,
    )


def _ensure_config_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(config)")}
        to_add = []
        if "storage_root_override" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN storage_root_override TEXT")
        if "kube_image_pvc_override" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN kube_image_pvc_override TEXT")
        if "kube_vm_storage_class_override" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN kube_vm_storage_class_override TEXT")
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
        if "theme_bg_image_overlay_opacity" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_bg_image_overlay_opacity REAL DEFAULT 0.0")
        if "theme_contrast_body" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_contrast_body REAL DEFAULT 4.5")
        if "theme_contrast_button" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_contrast_button REAL DEFAULT 4.5")
        if "theme_contrast_tile" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_contrast_tile REAL DEFAULT 4.5")
        if "theme_contrast_tile_border" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_contrast_tile_border REAL DEFAULT 1.5")
        if "theme_font_family" not in cols:
            to_add.append(
                "ALTER TABLE config ADD COLUMN theme_font_family TEXT DEFAULT 'Inter, system-ui, -apple-system, sans-serif'"
            )
        if "theme_font_size_base" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_font_size_base REAL DEFAULT 16.0")
        if "theme_font_size_h1" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_font_size_h1 REAL DEFAULT 32.0")
        if "theme_font_size_h2" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN theme_font_size_h2 REAL DEFAULT 24.0")
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
        if "sso_role_claim" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_role_claim TEXT DEFAULT 'groups'")
        if "sso_default_role" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_default_role TEXT DEFAULT 'user'")
        if "sso_role_mappings_json" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_role_mappings_json TEXT DEFAULT '{}'")
        if "rbac_roles_json" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN rbac_roles_json TEXT DEFAULT '{}'")
        if "sso_auto_create_users" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_auto_create_users BOOLEAN DEFAULT 1")
        if "sso_sync_roles_on_login" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN sso_sync_roles_on_login BOOLEAN DEFAULT 1")
        if "ldap_enabled" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_enabled BOOLEAN DEFAULT 0")
        if "ldap_server_uri" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_server_uri TEXT DEFAULT ''")
        if "ldap_bind_dn" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_bind_dn TEXT DEFAULT ''")
        if "ldap_bind_password" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_bind_password TEXT DEFAULT ''")
        if "ldap_user_base_dn" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_user_base_dn TEXT DEFAULT ''")
        if "ldap_user_filter" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_user_filter TEXT DEFAULT '(uid={username})'")
        if "ldap_start_tls" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_start_tls BOOLEAN DEFAULT 0")
        if "ldap_insecure_skip_verify" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_insecure_skip_verify BOOLEAN DEFAULT 0")
        if "ldap_timeout_seconds" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_timeout_seconds INTEGER DEFAULT 10")
        if "ldap_auto_create_users" not in cols:
            to_add.append("ALTER TABLE config ADD COLUMN ldap_auto_create_users BOOLEAN DEFAULT 1")
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
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "template" not in tables:
            return
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
        if "max_active_instances" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN max_active_instances INTEGER DEFAULT 2")
        if "console_provider" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN console_provider TEXT DEFAULT 'spice'")
        if "rdp_default_username" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN rdp_default_username TEXT DEFAULT ''")
        if "rdp_default_password" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN rdp_default_password TEXT DEFAULT ''")
        if "tenant" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN tenant TEXT DEFAULT 'global'")
        if "namespace" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN namespace TEXT DEFAULT 'labs'")
        if "cluster_id" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN cluster_id TEXT DEFAULT 'local'")
        if "enabled_namespaces_json" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN enabled_namespaces_json TEXT DEFAULT '[]'")
        if "shared_catalog" not in cols:
            to_add.append("ALTER TABLE template ADD COLUMN shared_catalog BOOLEAN DEFAULT 0")
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
            cur.execute(
                "UPDATE template SET preclone_pool_max = preclone_pool_size WHERE preclone_pool_max < preclone_pool_size"
            )
            cur.execute("UPDATE template SET max_active_instances = 2 WHERE max_active_instances IS NULL")
            cur.execute("UPDATE template SET rdp_default_username = '' WHERE rdp_default_username IS NULL")
            cur.execute("UPDATE template SET rdp_default_password = '' WHERE rdp_default_password IS NULL")
            cur.execute("UPDATE template SET tenant = 'global' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute("UPDATE template SET namespace = 'labs' WHERE namespace IS NULL OR trim(namespace) = ''")
            cur.execute("UPDATE template SET cluster_id = 'local' WHERE cluster_id IS NULL OR trim(cluster_id) = ''")
            cur.execute(
                "UPDATE template SET enabled_namespaces_json = '[]' WHERE enabled_namespaces_json IS NULL OR trim(enabled_namespaces_json) = ''"
            )
            cur.execute("UPDATE template SET shared_catalog = 0 WHERE shared_catalog IS NULL")
            conn.commit()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(template)")}
        if "console_provider" in cols:
            cur.execute(
                """
                UPDATE template
                SET console_provider = CASE
                    WHEN console_provider IS NULL OR trim(console_provider) = '' THEN 'spice'
                    WHEN lower(trim(console_provider)) IN ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp') THEN 'guacamole_rdp'
                    WHEN lower(trim(console_provider)) IN ('guacamole', 'guac', 'novnc', 'vnc') THEN 'guacamole'
                    WHEN lower(trim(console_provider)) = 'spice' THEN 'spice'
                    ELSE 'spice'
                END
                """
            )
            conn.commit()
        if "rdp_default_username" in cols:
            cur.execute("UPDATE template SET rdp_default_username = '' WHERE rdp_default_username IS NULL")
        if "rdp_default_password" in cols:
            cur.execute("UPDATE template SET rdp_default_password = '' WHERE rdp_default_password IS NULL")
        if "tenant" in cols:
            cur.execute("UPDATE template SET tenant = 'global' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_template_tenant ON template(tenant)")
        if "namespace" in cols:
            cur.execute("UPDATE template SET namespace = 'labs' WHERE namespace IS NULL OR trim(namespace) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_template_namespace ON template(namespace)")
        if "cluster_id" in cols:
            cur.execute("UPDATE template SET cluster_id = 'local' WHERE cluster_id IS NULL OR trim(cluster_id) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_template_cluster_id ON template(cluster_id)")
        if "enabled_namespaces_json" in cols:
            cur.execute(
                "UPDATE template SET enabled_namespaces_json = '[]' WHERE enabled_namespaces_json IS NULL OR trim(enabled_namespaces_json) = ''"
            )
        if "shared_catalog" in cols:
            cur.execute("UPDATE template SET shared_catalog = 0 WHERE shared_catalog IS NULL")
        if (
            "rdp_default_username" in cols
            or "rdp_default_password" in cols
            or "tenant" in cols
            or "namespace" in cols
            or "cluster_id" in cols
            or "enabled_namespaces_json" in cols
            or "shared_catalog" in cols
        ):
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
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "image" not in tables:
            return
        cols = {row[1] for row in cur.execute("PRAGMA table_info(image)")}
        if "source_pvc" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN source_pvc TEXT")
        if "tenant" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN tenant TEXT DEFAULT 'global'")
        if "namespace" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN namespace TEXT DEFAULT 'labs'")
        if "cluster_id" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN cluster_id TEXT DEFAULT 'local'")
        if "shared_catalog" not in cols:
            cur.execute("ALTER TABLE image ADD COLUMN shared_catalog BOOLEAN DEFAULT 0")
        cols = {row[1] for row in cur.execute("PRAGMA table_info(image)")}
        if "tenant" in {row[1] for row in cur.execute("PRAGMA table_info(image)")}:
            cur.execute("UPDATE image SET tenant = 'global' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_image_tenant ON image(tenant)")
        if "namespace" in cols:
            cur.execute("UPDATE image SET namespace = 'labs' WHERE namespace IS NULL OR trim(namespace) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_image_namespace ON image(namespace)")
        if "cluster_id" in cols:
            cur.execute("UPDATE image SET cluster_id = 'local' WHERE cluster_id IS NULL OR trim(cluster_id) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_image_cluster_id ON image(cluster_id)")
        if "shared_catalog" in cols:
            cur.execute("UPDATE image SET shared_catalog = 0 WHERE shared_catalog IS NULL")
        conn.commit()
    except Exception:
        logger.exception("Failed to ensure image columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_iso_image_columns() -> None:
    if not SQLITE_DB:
        return
    db_path = settings.database_path
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "isoimage" not in tables:
            return
        cols = {row[1] for row in cur.execute("PRAGMA table_info(isoimage)")}
        if "description" not in cols:
            cur.execute("ALTER TABLE isoimage ADD COLUMN description TEXT DEFAULT ''")
            conn.commit()
            cols = {row[1] for row in cur.execute("PRAGMA table_info(isoimage)")}
        if "description" in cols:
            cur.execute("UPDATE isoimage SET description = '' WHERE description IS NULL")
            conn.commit()
    except Exception:
        logger.exception("Failed to ensure iso image columns")
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
        if "tenant" not in cols:
            cur.execute("ALTER TABLE instance ADD COLUMN tenant TEXT DEFAULT 'default'")
        if "launch_namespace" not in cols:
            cur.execute("ALTER TABLE instance ADD COLUMN launch_namespace TEXT DEFAULT 'labs'")
        if "namespace" not in cols:
            cur.execute("ALTER TABLE instance ADD COLUMN namespace TEXT DEFAULT 'labs'")
        if "cluster_id" not in cols:
            cur.execute("ALTER TABLE instance ADD COLUMN cluster_id TEXT DEFAULT 'local'")
        cols = {row[1] for row in cur.execute("PRAGMA table_info(instance)")}
        if "tenant" in cols:
            cur.execute("UPDATE instance SET tenant = 'default' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_instance_tenant ON instance(tenant)")
        if "launch_namespace" in cols:
            cur.execute(
                "UPDATE instance SET launch_namespace = namespace WHERE launch_namespace IS NULL OR trim(launch_namespace) = ''"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_instance_launch_namespace ON instance(launch_namespace)")
        if "namespace" in cols:
            cur.execute("UPDATE instance SET namespace = 'labs' WHERE namespace IS NULL OR trim(namespace) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_instance_namespace ON instance(namespace)")
        if "cluster_id" in cols:
            cur.execute("UPDATE instance SET cluster_id = 'local' WHERE cluster_id IS NULL OR trim(cluster_id) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_instance_cluster_id ON instance(cluster_id)")
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
        if "stage" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN stage TEXT DEFAULT 'queued'")
        if "progress_percent" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN progress_percent INTEGER")
        if "retry_count" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN retry_count INTEGER DEFAULT 0")
        if "max_retries" not in cols:
            to_add.append(f"ALTER TABLE imageuploadtask ADD COLUMN max_retries INTEGER DEFAULT {FINALIZE_MAX_RETRIES}")
        if "next_retry_at" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN next_retry_at TIMESTAMP")
        if "last_retry_error" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN last_retry_error TEXT")
        if "finalize_started_at" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN finalize_started_at TIMESTAMP")
        if "tenant" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN tenant TEXT DEFAULT 'global'")
        if "namespace" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN namespace TEXT DEFAULT 'labs'")
        if "cluster_id" not in cols:
            to_add.append("ALTER TABLE imageuploadtask ADD COLUMN cluster_id TEXT DEFAULT 'local'")
        for stmt in to_add:
            try:
                cur.execute(stmt)
            except sqlite3.OperationalError:
                pass
        if to_add:
            conn.commit()
        cols = {row[1] for row in cur.execute("PRAGMA table_info(imageuploadtask)")}
        if "stage" in cols:
            cur.execute("UPDATE imageuploadtask SET stage = status WHERE stage IS NULL OR trim(stage) = ''")
        if "retry_count" in cols:
            cur.execute("UPDATE imageuploadtask SET retry_count = 0 WHERE retry_count IS NULL")
        if "max_retries" in cols:
            cur.execute(
                "UPDATE imageuploadtask SET max_retries = ? WHERE max_retries IS NULL OR max_retries < 0",
                (FINALIZE_MAX_RETRIES,),
            )
        if "tenant" in cols:
            cur.execute("UPDATE imageuploadtask SET tenant = 'global' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_imageuploadtask_tenant ON imageuploadtask(tenant)")
        if "namespace" in cols:
            cur.execute("UPDATE imageuploadtask SET namespace = 'labs' WHERE namespace IS NULL OR trim(namespace) = ''")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_imageuploadtask_namespace ON imageuploadtask(namespace)")
        if "cluster_id" in cols:
            cur.execute(
                "UPDATE imageuploadtask SET cluster_id = 'local' WHERE cluster_id IS NULL OR trim(cluster_id) = ''"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_imageuploadtask_cluster_id ON imageuploadtask(cluster_id)")
        conn.commit()
    except Exception:
        logger.exception("Failed to ensure image upload task columns")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_admin_audit_table() -> None:
    conn: sqlite3.Connection | None = None
    try:
        if SQLITE_DB:
            db_path = settings.database_path
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS adminauditevent (
                    id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL DEFAULT 'unknown',
                    tenant TEXT NOT NULL DEFAULT 'global',
                    namespace TEXT NOT NULL DEFAULT 'labs',
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
            cols = {row[1] for row in cur.execute("PRAGMA table_info(adminauditevent)")}
            if "tenant" not in cols:
                cur.execute("ALTER TABLE adminauditevent ADD COLUMN tenant TEXT NOT NULL DEFAULT 'global'")
            if "namespace" not in cols:
                cur.execute(
                    "ALTER TABLE adminauditevent ADD COLUMN namespace TEXT NOT NULL DEFAULT '"
                    + (normalize_namespace(settings.kube_namespace) or "labs")
                    + "'"
                )
            cur.execute("UPDATE adminauditevent SET tenant = 'global' WHERE tenant IS NULL OR trim(tenant) = ''")
            cur.execute(
                "UPDATE adminauditevent SET namespace = ? WHERE namespace IS NULL OR trim(namespace) = ''",
                (normalize_namespace(settings.kube_namespace) or "labs",),
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_actor ON adminauditevent(actor)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_tenant ON adminauditevent(tenant)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_namespace ON adminauditevent(namespace)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_action ON adminauditevent(action)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_target_type ON adminauditevent(target_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_created_at ON adminauditevent(created_at)")
            conn.commit()
            return

        with session_scope() as session:
            session.exec(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS adminauditevent (
                        id TEXT PRIMARY KEY,
                        actor VARCHAR(128) NOT NULL DEFAULT 'unknown',
                        tenant VARCHAR(64) NOT NULL DEFAULT 'global',
                        namespace VARCHAR(63) NOT NULL DEFAULT 'labs',
                        action VARCHAR(128) NOT NULL,
                        target_type VARCHAR(64) NOT NULL,
                        target_id VARCHAR(128) NOT NULL DEFAULT '',
                        detail VARCHAR(512) NOT NULL DEFAULT '',
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
            )
            session.exec(
                text(
                    "ALTER TABLE adminauditevent ADD COLUMN IF NOT EXISTS tenant VARCHAR(64) NOT NULL DEFAULT 'global'"
                )
            )
            session.exec(
                text(
                    "ALTER TABLE adminauditevent ADD COLUMN IF NOT EXISTS namespace VARCHAR(63) "
                    "NOT NULL DEFAULT 'labs'"
                )
            )
            session.exec(text("UPDATE adminauditevent SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''"))
            session.exec(
                text(
                    "UPDATE adminauditevent SET namespace = :namespace WHERE namespace IS NULL OR namespace = ''"
                ).bindparams(namespace=normalize_namespace(settings.kube_namespace) or "labs")
            )
            session.exec(text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_actor ON adminauditevent(actor)"))
            session.exec(text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_tenant ON adminauditevent(tenant)"))
            session.exec(text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_namespace ON adminauditevent(namespace)"))
            session.exec(text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_action ON adminauditevent(action)"))
            session.exec(
                text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_target_type ON adminauditevent(target_type)")
            )
            session.exec(
                text("CREATE INDEX IF NOT EXISTS ix_adminauditevent_created_at ON adminauditevent(created_at)")
            )
            session.commit()
    except Exception:
        logger.exception("Failed to ensure admin audit event table")
    finally:
        try:
            if SQLITE_DB and conn is not None:
                conn.close()
        except Exception:
            pass


_ensure_config_columns()
_ensure_template_columns()
_ensure_image_columns()
_ensure_iso_image_columns()
_ensure_instance_columns()
_ensure_upload_task_columns()
_ensure_admin_audit_table()


def _audit_namespace_from_detail(detail: str | None) -> str:
    text_value = str(detail or "").strip().lower()
    if not text_value:
        return ""
    match = AUDIT_NAMESPACE_DETAIL_RE.search(text_value)
    if not match:
        return ""
    return normalize_namespace(match.group(1))


def _resolve_audit_namespace(
    *,
    namespace: str | None,
    target_type: str | None,
    target_id: str | None,
    detail: str | None,
) -> str:
    explicit = normalize_namespace(namespace)
    if explicit:
        return explicit
    if str(target_type or "").strip().lower() in {"managed_namespace", "namespace"}:
        candidate = normalize_namespace(target_id)
        if candidate:
            return candidate
    from_detail = _audit_namespace_from_detail(detail)
    if from_detail:
        return from_detail
    return normalize_namespace(settings.kube_namespace) or "labs"


def _record_admin_audit_event(
    session: Session,
    *,
    actor: str | None,
    tenant: str | None = None,
    namespace: str | None = None,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: str | None = None,
) -> None:
    tenant_value = str(tenant or "").strip()
    if not tenant_value:
        actor_username = str(actor or "").strip()
        if actor_username:
            actor_row = session.get(User, actor_username)
            if actor_row:
                tenant_value = normalize_team(getattr(actor_row, "team", None))
    normalized_tenant = normalize_tenant(tenant_value, default=GLOBAL_TENANT)
    event = AdminAuditEvent(
        id=str(uuid4()),
        actor=(str(actor or "").strip() or "unknown")[:128],
        tenant=normalized_tenant,
        namespace=_resolve_audit_namespace(
            namespace=namespace,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        ),
        action=(str(action or "").strip() or "unknown")[:128],
        target_type=(str(target_type or "").strip() or "unknown")[:64],
        target_id=(str(target_id or "").strip())[:128],
        detail=(str(detail or "").strip())[:512],
        created_at=utc_now(),
    )
    session.add(event)
    _prune_admin_audit_events(session, tenant=normalized_tenant)


def _prune_admin_audit_events(session: Session, *, tenant: str) -> None:
    total_events = int(
        session.exec(select(func.count()).select_from(AdminAuditEvent).where(AdminAuditEvent.tenant == tenant)).one()
        or 0
    )
    overflow = total_events - ADMIN_AUDIT_EVENT_MAX_PER_TENANT
    if overflow <= 0:
        return
    stale_rows = session.exec(
        select(AdminAuditEvent)
        .where(AdminAuditEvent.tenant == tenant)
        .order_by(AdminAuditEvent.created_at.asc(), AdminAuditEvent.id.asc())
        .limit(overflow)
    ).all()
    for row in stale_rows:
        session.delete(row)


def _retry_backoff_seconds(retry_count: int) -> int:
    exponent = max(0, int(retry_count) - 1)
    delay = FINALIZE_RETRY_BASE_SECONDS * (2**exponent)
    return min(FINALIZE_RETRY_MAX_SECONDS, max(FINALIZE_RETRY_BASE_SECONDS, int(delay)))


def _coerce_progress_percent(value: object, *, default: int = 0, upper_bound: int = 100) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        parsed = int(default)
    return max(0, min(int(upper_bound), parsed))


def _advance_progress_percent(
    task: ImageUploadTask,
    *,
    floor: int = 0,
    cap: int = 99,
    step: int | None = None,
) -> int:
    normalized_floor = max(0, min(100, int(floor)))
    normalized_cap = max(normalized_floor, min(100, int(cap)))
    if step is None:
        step = max(1, int(getattr(settings, "image_import_progress_step_percent", 3) or 3))
    else:
        step = max(1, int(step))
    current = _coerce_progress_percent(
        getattr(task, "progress_percent", None), default=normalized_floor, upper_bound=100
    )
    next_progress = max(normalized_floor, min(normalized_cap, current + step))
    task.progress_percent = next_progress
    return next_progress


def _task_stage_progress(task: ImageUploadTask) -> tuple[str, int | None]:
    status = str(getattr(task, "status", "") or "").strip().lower()
    stage = str(getattr(task, "stage", "") or "").strip().lower()
    if status == "completed":
        return (UPLOAD_STAGE_READY, 100)
    if status == "failed":
        return (
            UPLOAD_STAGE_FAILED,
            _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=100),
        )

    if stage in {
        UPLOAD_STAGE_UPLOADED,
        UPLOAD_STAGE_NORMALIZING,
        UPLOAD_STAGE_SEEDED,
        UPLOAD_STAGE_READY,
        UPLOAD_STAGE_FAILED,
    }:
        bounded = 100 if stage == UPLOAD_STAGE_READY else (99 if stage == UPLOAD_STAGE_SEEDED else 100)
        return (stage, _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=bounded))

    if status == "uploading":
        return (
            UPLOAD_STAGE_UPLOADED,
            _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=100),
        )
    if status == "finalizing":
        progress = getattr(task, "progress_percent", None)
        return (UPLOAD_STAGE_NORMALIZING, _coerce_progress_percent(progress, default=0, upper_bound=100))
    if status == "importing":
        return (
            UPLOAD_STAGE_SEEDED,
            _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=99),
        )
    if stage == "completed":
        return (UPLOAD_STAGE_READY, 100)
    if stage == "finalizing":
        return (UPLOAD_STAGE_NORMALIZING, _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0))
    if stage == "importing":
        return (UPLOAD_STAGE_SEEDED, _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0))
    if stage == "uploading":
        return (UPLOAD_STAGE_UPLOADED, _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0))
    if stage == "failed":
        return (UPLOAD_STAGE_FAILED, _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0))
    return (UPLOAD_STAGE_UPLOADED if status else "queued", getattr(task, "progress_percent", None))


def _upload_task_out(task: ImageUploadTask) -> ImageUploadTaskStatus:
    stage, progress_percent = _task_stage_progress(task)
    return ImageUploadTaskStatus(
        task_id=task.id,
        status=task.status,
        stage=stage,
        progress_percent=progress_percent,
        original_filename=task.original_filename,
        filename=task.filename,
        namespace=_record_namespace(task),
        size_bytes=task.size_bytes,
        detail=task.detail or "",
        error=task.error_message,
        retry_count=max(0, int(getattr(task, "retry_count", 0) or 0)),
        max_retries=max(0, int(getattr(task, "max_retries", FINALIZE_MAX_RETRIES) or FINALIZE_MAX_RETRIES)),
        next_retry_at=getattr(task, "next_retry_at", None),
        last_retry_error=getattr(task, "last_retry_error", None),
        image_id=task.image_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _elapsed_seconds(started_at: datetime | None) -> int:
    if started_at is None:
        return 0
    try:
        return max(0, int((utc_now() - started_at).total_seconds()))
    except Exception:
        return 0


def _operation_request_for_namespace(namespace: str | None) -> Request:
    selected = normalize_namespace(namespace) or normalize_namespace(settings.kube_namespace) or "labs"
    header_value = selected.encode("utf-8")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/internal/admin/operations",
        "raw_path": b"/internal/admin/operations",
        "query_string": b"",
        "headers": [(b"x-bretter-namespace", header_value)],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "scheme": "http",
        "root_path": "",
    }
    return Request(scope)


def _launch_task_out_from_vm(record: Instance) -> AdminLaunchTaskOut:
    return AdminLaunchTaskOut(
        task_id=record.id,
        kind="vm",
        status=str(record.status or "unknown"),
        owner=record.owner,
        namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        template_id=record.template_id,
        detail="VM launch is queued or failed.",
        elapsed_seconds=_elapsed_seconds(getattr(record, "started_at", None)),
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


def _launch_task_out_from_container(record: ContainerInstanceTable) -> AdminLaunchTaskOut:
    detail = str(getattr(record, "queue_reason", "") or "").strip() or "Container launch is queued or failed."
    return AdminLaunchTaskOut(
        task_id=record.id,
        kind="container",
        status=str(record.status or "unknown"),
        owner=record.owner,
        namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        template_id=record.template_id,
        detail=detail,
        elapsed_seconds=_elapsed_seconds(getattr(record, "started_at", None)),
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


def _sync_labimageimport_crd(task: ImageUploadTask, *, create_if_missing: bool) -> None:
    if not image_import_writes_crd():
        return
    try:
        if create_if_missing:
            upsert_labimageimport_for_task(task)
            return
        patch_labimageimport_status_for_task(task)
    except ApiException as exc:
        if not create_if_missing and exc.status == 404:
            upsert_labimageimport_for_task(task)
            return
        logger.warning("Failed to sync LabImageImport CRD for task=%s: %s", task.id, exc, exc_info=True)
    except Exception:
        logger.warning("Failed to sync LabImageImport CRD for task=%s", task.id, exc_info=True)


def _update_upload_task(
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress_percent: int | None = None,
    detail: str | None = None,
    error_message: str | None = None,
    retry_count: int | None = None,
    max_retries: int | None = None,
    next_retry_at: datetime | None = None,
    last_retry_error: str | None = None,
    finalize_started_at: datetime | None = None,
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
        if stage is not None:
            task.stage = stage
        if progress_percent is not None:
            task.progress_percent = max(0, min(100, int(progress_percent)))
        if detail is not None:
            task.detail = detail
        if error_message is not None:
            task.error_message = error_message
        if retry_count is not None:
            task.retry_count = max(0, int(retry_count))
        if max_retries is not None:
            task.max_retries = max(0, int(max_retries))
        if next_retry_at is not None:
            task.next_retry_at = next_retry_at
        if last_retry_error is not None:
            task.last_retry_error = last_retry_error
        if finalize_started_at is not None:
            task.finalize_started_at = finalize_started_at
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
        task.updated_at = utc_now()
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
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=settings.kube_image_pvc
                        ),
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


def _requested_upload_pvc_gi(size_bytes: int) -> int:
    required_bytes = max(0, int(size_bytes)) + SOURCE_PVC_OVERHEAD_BYTES
    requested_gi = max(1, math.ceil(required_bytes / (1024**3)))
    return max(MIN_UPLOAD_PVC_GIB, requested_gi)


def _start_datavolume_import(task: ImageUploadTask, claim_name: str) -> str:
    if not _has_cdi_datavolume():
        raise RuntimeError("CDI DataVolume CRD is not installed")
    custom = client.CustomObjectsApi()
    core = kube._client()
    requested_gi = _requested_upload_pvc_gi(int(task.size_bytes))

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


def _ensure_source_filename_alias_on_pvc(claim_name: str, source_filename: str, desired_filename: str) -> None:
    claim = (claim_name or "").strip()
    if not claim:
        raise RuntimeError("source PVC claim name is required")
    src = Path(source_filename).name
    dst = Path(desired_filename).name
    if not src or not dst or src == dst:
        return
    quoted_src = shlex.quote(src)
    quoted_dst = shlex.quote(dst)
    _with_pvc_helper(
        [
            "/bin/sh",
            "-c",
            (
                "set -eu; "
                "cd /images; "
                f"if [ ! -f {quoted_src} ]; then echo 'BLABS_ERROR=source missing: {src}' >&2; exit 22; fi; "
                f"if [ -e {quoted_dst} ] || [ -L {quoted_dst} ]; then exit 0; fi; "
                f"ln -s {quoted_src} {quoted_dst}"
            ),
        ],
        claim_name=claim,
    )


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
    requested_gi = _requested_upload_pvc_gi(int(task.size_bytes))
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
        command=["/bin/bash", "-lc"],
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
  echo "BLABS_PHASE=convert"
  qemu-img convert -p -O "${CONVERT_FORMAT}" "${in}" "${out}" 2>&1 | tr '\r' '\n'
  rm -f "${in}"
fi
echo "BLABS_PHASE=sync"
sync
echo "BLABS_PHASE=checksum"
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
            ttl_seconds_after_finished=TASK_RETENTION_SECONDS,
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
        command=["/bin/bash", "-lc"],
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
  echo "BLABS_PHASE=convert"
  qemu-img convert -p -O "${CONVERT_FORMAT}" "${stage}" "${out}" 2>&1 | tr '\r' '\n'
  rm -f "${stage}"
fi
echo "BLABS_PHASE=sync"
sync
echo "BLABS_PHASE=checksum"
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
            ttl_seconds_after_finished=TASK_RETENTION_SECONDS,
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


def _parse_finalize_progress_percent(log_data: str) -> int | None:
    if not log_data:
        return None
    matches = FINALIZE_PROGRESS_RE.findall(log_data.replace("\r", "\n"))
    if not matches:
        return None
    try:
        progress = float(matches[-1])
    except ValueError:
        return None
    return max(0, min(100, int(progress)))


def _finalize_in_checksum_phase(log_data: str) -> bool:
    return "BLABS_PHASE=checksum" in (log_data or "")


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


def _ensure_upload_task_finalize_job(task: ImageUploadTask, *, force_recreate: bool = False) -> None:
    if task.finalize_job and not force_recreate:
        return
    if force_recreate and task.finalize_job:
        batch = client.BatchV1Api()
        try:
            batch.delete_namespaced_job(
                name=task.finalize_job,
                namespace=settings.kube_namespace,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
        task.finalize_job = None
    task.finalize_job = _create_finalize_from_upload_job(task) if task.upload_pvc else _create_finalize_job(task)
    task.status = "finalizing"
    task.stage = UPLOAD_STAGE_NORMALIZING
    task.progress_percent = 0
    task.max_retries = max(0, int(getattr(task, "max_retries", FINALIZE_MAX_RETRIES) or FINALIZE_MAX_RETRIES))
    task.next_retry_at = None
    if not getattr(task, "finalize_started_at", None):
        task.finalize_started_at = utc_now()
    task.detail = "Finalizing image format/checksum on cluster"
    task.updated_at = utc_now()


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
set -eu
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
            ttl_seconds_after_finished=TASK_RETENTION_SECONDS,
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
        existing.tenant = normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT)
        existing.namespace = _record_namespace(task)
        existing.cluster_id = str(getattr(task, "cluster_id", "") or local_cluster_id())
        existing.source_kind = "uploaded"
        existing.installer_iso_id = None
        existing.installer_iso_filename = None
        existing.installer_os_type = None
        existing.installer_disk_size_gib = None
        existing.source_pvc = task.source_pvc
        existing.checksum = task.checksum
        existing.size_bytes = task.size_bytes
        session.add(existing)
        return

    record = Image(
        id=task.image_id,
        name=task.filename,
        filename=task.filename,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        namespace=_record_namespace(task),
        cluster_id=str(getattr(task, "cluster_id", "") or local_cluster_id()),
        source_kind="uploaded",
        installer_iso_id=None,
        installer_iso_filename=None,
        installer_os_type=None,
        installer_disk_size_gib=None,
        source_pvc=task.source_pvc,
        checksum=task.checksum,
        size_bytes=task.size_bytes,
        created_at=utc_now(),
    )
    session.add(record)


def _refresh_upload_task(task: ImageUploadTask, session: Session) -> ImageUploadTask:
    if task.status in {"completed", "failed"}:
        return task

    batch = client.BatchV1Api()
    now = utc_now()
    task.max_retries = max(0, int(getattr(task, "max_retries", FINALIZE_MAX_RETRIES) or FINALIZE_MAX_RETRIES))
    task.retry_count = max(0, int(getattr(task, "retry_count", 0) or 0))
    if not str(getattr(task, "stage", "") or "").strip():
        if task.status == "uploading":
            task.stage = UPLOAD_STAGE_UPLOADED
        elif task.status == "finalizing":
            task.stage = UPLOAD_STAGE_NORMALIZING
        elif task.status == "importing":
            task.stage = UPLOAD_STAGE_SEEDED
        elif task.status == "completed":
            task.stage = UPLOAD_STAGE_READY
        elif task.status == "failed":
            task.stage = UPLOAD_STAGE_FAILED
        else:
            task.stage = task.status

    def _commit_task() -> ImageUploadTask:
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
        return task

    def _schedule_import_retry_or_fail(*, failure_detail: str, latest_error: str, final_detail: str) -> ImageUploadTask:
        task.last_retry_error = (str(latest_error or failure_detail).strip() or failure_detail)[:4096]
        if task.retry_count >= task.max_retries:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = final_detail
            task.error_message = task.last_retry_error
            _commit_task()
            return task
        task.retry_count += 1
        backoff = _retry_backoff_seconds(task.retry_count)
        task.next_retry_at = utc_now() + timedelta(seconds=backoff)
        task.detail = f"{failure_detail}; retrying in {backoff}s (attempt {task.retry_count}/{task.max_retries})"
        task.error_message = None
        task.stage = UPLOAD_STAGE_SEEDED
        if task.copy_job and not str(task.copy_job).startswith("dv:"):
            try:
                batch.delete_namespaced_job(
                    name=task.copy_job,
                    namespace=settings.kube_namespace,
                    propagation_policy="Background",
                )
            except ApiException as exc:
                if exc.status != 404:
                    logger.warning("Failed deleting copy job %s during retry", task.copy_job, exc_info=True)
        task.copy_job = None
        _commit_task()
        return task

    if task.upload_pvc and task.status == "uploading":
        try:
            phase, msg = _datavolume_phase(task.upload_pvc)
        except ApiException as exc:
            if exc.status == 404:
                task.status = "failed"
                task.stage = UPLOAD_STAGE_FAILED
                task.detail = "Direct upload DataVolume not found"
                task.error_message = "direct upload datavolume disappeared before completion"
                _commit_task()
                _cleanup_task_jobs(task)
                return task
            raise
        phase_lower = phase.lower()
        if phase_lower == "failed":
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Direct CDI upload failed"
            task.error_message = msg or "direct upload failed"
            _commit_task()
            _cleanup_task_jobs(task)
            return task
        if phase_lower != "succeeded":
            task.stage = UPLOAD_STAGE_UPLOADED
            task.detail = "Uploading image directly to CDI DataVolume"
            task.error_message = None
            task.progress_percent = None
            _commit_task()
            return task
        task.status = "finalizing"
        task.stage = UPLOAD_STAGE_NORMALIZING
        task.progress_percent = 0
        task.finalize_started_at = now
        task.max_retries = max(0, int(getattr(task, "max_retries", FINALIZE_MAX_RETRIES) or FINALIZE_MAX_RETRIES))
        task.retry_count = 0
        task.next_retry_at = None
        task.last_retry_error = None
        task.detail = "Upload complete; normalizing image format/checksum"
        task.error_message = None

    if task.status != "uploading":
        try:
            _ensure_upload_task_finalize_job(task)
        except Exception as exc:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Failed to submit finalize job"
            task.error_message = str(exc)
            _commit_task()
            return task

    if task.status == "finalizing":
        finalize_log = ""
        if task.finalize_started_at and (now - task.finalize_started_at).total_seconds() > max(
            FINALIZE_JOB_TIMEOUT_SECONDS + 300, FINALIZE_JOB_TIMEOUT_SECONDS
        ):
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Finalize job timed out"
            task.error_message = "finalize job exceeded timeout"
            _commit_task()
            return task
        try:
            job = batch.read_namespaced_job(name=task.finalize_job, namespace=settings.kube_namespace)
            phase = _job_phase(job)
        except ApiException as exc:
            if exc.status == 404:
                task.status = "failed"
                task.detail = "Finalize job not found"
                task.error_message = "finalize job disappeared before completion"
                task.stage = UPLOAD_STAGE_FAILED
                _commit_task()
                return task
            raise

        if phase == "failed":
            finalize_log = _read_job_log(task.finalize_job, tail_lines=180) if task.finalize_job else ""
            latest_error = (finalize_log.strip() or "finalize job failed")[:4096]
            task.last_retry_error = latest_error
            if task.retry_count < task.max_retries:
                if task.next_retry_at and now < task.next_retry_at:
                    wait_seconds = max(1, int((task.next_retry_at - now).total_seconds()))
                    task.detail = (
                        f"Finalize retry scheduled in {wait_seconds}s "
                        f"(attempt {task.retry_count}/{task.max_retries})"
                    )
                    task.error_message = None
                    task.stage = UPLOAD_STAGE_NORMALIZING
                    _commit_task()
                    return task
                if task.next_retry_at and now >= task.next_retry_at:
                    try:
                        _ensure_upload_task_finalize_job(task, force_recreate=True)
                    except Exception as exc:
                        task.status = "failed"
                        task.stage = UPLOAD_STAGE_FAILED
                        task.detail = "Failed to resubmit finalize job"
                        task.error_message = str(exc)
                        _commit_task()
                        return task
                    task.next_retry_at = None
                    task.detail = f"Retrying finalize job (attempt {task.retry_count}/{task.max_retries})"
                    task.error_message = None
                    task.stage = UPLOAD_STAGE_NORMALIZING
                    _commit_task()
                    return task
                task.retry_count += 1
                backoff = _retry_backoff_seconds(task.retry_count)
                task.next_retry_at = now + timedelta(seconds=backoff)
                task.detail = (
                    f"Finalize job failed; retrying in {backoff}s " f"(attempt {task.retry_count}/{task.max_retries})"
                )
                task.error_message = None
                task.stage = UPLOAD_STAGE_NORMALIZING
                _commit_task()
                return task
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Finalize job failed after retries"
            task.error_message = latest_error
            _commit_task()
            return task

        if phase in {"running", "pending"}:
            if task.finalize_job:
                finalize_log = _read_job_log(task.finalize_job, tail_lines=300)
            # In some clusters the job status can lag behind pod completion.
            # If finalize output markers are present, treat it as succeeded now.
            try:
                _parse_finalize_log(finalize_log)
                phase = "succeeded"
            except Exception:
                progress = _parse_finalize_progress_percent(finalize_log)
                if progress is None:
                    synthetic = _advance_progress_percent(task, floor=1, cap=95, step=2)
                    task.detail = f"Finalizing image format/checksum on cluster ({synthetic}% complete)"
                elif progress >= 100 and _finalize_in_checksum_phase(finalize_log):
                    task.detail = "Finalizing image format/checksum on cluster (95% complete; computing checksum)"
                    task.progress_percent = max(
                        _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=95),
                        95,
                    )
                else:
                    normalized_progress = max(
                        _coerce_progress_percent(getattr(task, "progress_percent", 0), default=0, upper_bound=95),
                        _coerce_progress_percent(progress, default=0, upper_bound=95),
                    )
                    task.detail = f"Finalizing image format/checksum on cluster ({normalized_progress}% complete)"
                    task.progress_percent = normalized_progress
                task.stage = UPLOAD_STAGE_NORMALIZING
                task.error_message = None
                _commit_task()
                return task

        try:
            if not finalize_log and task.finalize_job:
                finalize_log = _read_job_log(task.finalize_job, tail_lines=200)
            out_name, out_size, out_sha = _parse_finalize_log(finalize_log)
            task.filename = out_name
            task.size_bytes = out_size
            task.checksum = out_sha
            task.detail = "Normalization complete; preparing clone source seed (96% complete)"
            task.error_message = None
            task.stage = UPLOAD_STAGE_NORMALIZING
            task.progress_percent = max(
                _coerce_progress_percent(getattr(task, "progress_percent", 95), default=95, upper_bound=99),
                96,
            )
            _commit_task()
        except Exception as exc:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Failed to parse finalize output"
            task.error_message = str(exc)
            _commit_task()
            return task

        try:
            claim_name, copy_job = _create_task_copy_job(task)
            task.source_pvc = claim_name
            task.copy_job = copy_job
            task.status = "importing"
            task.stage = UPLOAD_STAGE_SEEDED
            task.detail = (
                "Seeding clone source PVC via CDI DataVolume"
                if copy_job.startswith("dv:")
                else "Seeding clone source PVC"
            )
            task.retry_count = 0
            task.next_retry_at = None
            task.last_retry_error = None
            task.progress_percent = max(
                _coerce_progress_percent(getattr(task, "progress_percent", 96), default=96, upper_bound=99),
                97,
            )
            task.detail = f"{task.detail} ({task.progress_percent}% complete)"
            _commit_task()
            return task
        except Exception as exc:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Failed to start source PVC copy job"
            task.error_message = str(exc)
            _commit_task()
            return task

    if task.status == "importing":
        if task.next_retry_at:
            if now < task.next_retry_at:
                wait_seconds = max(1, int((task.next_retry_at - now).total_seconds()))
                task.detail = (
                    f"Seed retry scheduled in {wait_seconds}s " f"(attempt {task.retry_count}/{task.max_retries})"
                )
                task.stage = UPLOAD_STAGE_SEEDED
                task.error_message = None
                _commit_task()
                return task
            task.next_retry_at = None
            task.detail = f"Retrying clone source seed (attempt {task.retry_count}/{task.max_retries})"
            task.stage = UPLOAD_STAGE_SEEDED
            task.error_message = None
            _commit_task()

        if not task.copy_job:
            try:
                claim_name, copy_job = _create_task_copy_job(task)
                task.source_pvc = claim_name
                task.copy_job = copy_job
                task.detail = (
                    "Seeding clone source PVC via CDI DataVolume"
                    if copy_job.startswith("dv:")
                    else "Seeding clone source PVC"
                )
                task.stage = UPLOAD_STAGE_SEEDED
                task.progress_percent = max(
                    _coerce_progress_percent(getattr(task, "progress_percent", 96), default=96, upper_bound=99),
                    97,
                )
                task.detail = f"{task.detail} ({task.progress_percent}% complete)"
                _commit_task()
            except Exception as exc:
                return _schedule_import_retry_or_fail(
                    failure_detail="Failed to start source PVC seed job",
                    latest_error=str(exc),
                    final_detail="Source PVC seed failed after retries",
                )

        if task.copy_job.startswith("dv:"):
            dv_name = task.copy_job.split(":", 1)[1]
            try:
                dv_phase, dv_msg = _datavolume_phase(dv_name)
            except ApiException as exc:
                if exc.status == 404:
                    return _schedule_import_retry_or_fail(
                        failure_detail="CDI DataVolume seed not found",
                        latest_error="datavolume disappeared before completion",
                        final_detail="CDI DataVolume seed failed after retries",
                    )
                raise

            phase_lower = dv_phase.lower()
            if phase_lower not in {"succeeded", "failed"}:
                import_progress = _advance_progress_percent(task, floor=97, cap=99, step=1)
                task.detail = f"Seeding clone source PVC via CDI DataVolume ({import_progress}% complete)"
                task.stage = UPLOAD_STAGE_SEEDED
                task.error_message = None
                _commit_task()
                return task
            if phase_lower == "failed":
                _cleanup_fileserver(task.id)
                return _schedule_import_retry_or_fail(
                    failure_detail="CDI DataVolume seed failed",
                    latest_error=dv_msg or "datavolume import failed",
                    final_detail="CDI DataVolume seed failed after retries",
                )
            _cleanup_fileserver(task.id)
            try:
                _ensure_source_filename_alias_on_pvc(
                    task.source_pvc or "",
                    settings.cdi_upload_source_filename or "disk.img",
                    task.filename,
                )
            except Exception as exc:
                return _schedule_import_retry_or_fail(
                    failure_detail="Failed to finalize source image filename alias",
                    latest_error=str(exc),
                    final_detail="Source image filename alias finalize failed after retries",
                )
        else:
            try:
                job = batch.read_namespaced_job(name=task.copy_job, namespace=settings.kube_namespace)
                phase = _job_phase(job)
            except ApiException as exc:
                if exc.status == 404:
                    return _schedule_import_retry_or_fail(
                        failure_detail="Source PVC seed job not found",
                        latest_error="copy job disappeared before completion",
                        final_detail="Source PVC seed failed after retries",
                    )
                raise

            if phase in {"running", "pending"}:
                import_progress = _advance_progress_percent(task, floor=97, cap=99, step=1)
                task.detail = f"Seeding clone source PVC ({import_progress}% complete)"
                task.stage = UPLOAD_STAGE_SEEDED
                task.error_message = None
                _commit_task()
                return task

            if phase == "failed":
                return _schedule_import_retry_or_fail(
                    failure_detail="Source PVC seed failed",
                    latest_error=_read_job_log(task.copy_job, tail_lines=120) or "copy job failed",
                    final_detail="Source PVC seed failed after retries",
                )

        try:
            _upsert_image_from_task(task, session)
            task.status = "completed"
            task.stage = UPLOAD_STAGE_READY
            task.progress_percent = 100
            task.detail = "Image ready"
            task.error_message = None
            task.next_retry_at = None
            task.last_retry_error = None
            _commit_task()
            _cleanup_task_jobs(task)
        except Exception as exc:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.detail = "Failed to register image metadata"
            task.error_message = str(exc)
            _commit_task()
    return task


def run_upload_task_watchdog(
    session: Session,
    *,
    max_tasks: int | None = None,
    stale_seconds: int | None = None,
) -> dict[str, int]:
    """Refresh active upload/finalize tasks so progress/retries continue without client polling."""
    limit = max(1, int(max_tasks or getattr(settings, "image_upload_watchdog_max_tasks", 25) or 25))
    query = select(ImageUploadTask).where(ImageUploadTask.status.notin_(["completed", "failed"]))
    stale_cutoff_seconds = max(0, int(stale_seconds or 0))
    if stale_cutoff_seconds > 0:
        cutoff = utc_now() - timedelta(seconds=stale_cutoff_seconds)
        query = query.where(ImageUploadTask.updated_at <= cutoff)
    tasks = session.exec(query.order_by(ImageUploadTask.updated_at.asc()).limit(limit)).all()

    stats = {"scanned": 0, "completed": 0, "failed": 0, "errors": 0}
    for task in tasks:
        stats["scanned"] += 1
        try:
            refreshed = _refresh_upload_task(task, session)
        except Exception as exc:
            stats["errors"] += 1
            logger.error("Upload watchdog failed to refresh task %s: %s", task.id, exc, exc_info=True)
            session.rollback()
            failed_task = session.get(ImageUploadTask, task.id)
            if failed_task:
                failed_task.status = "failed"
                failed_task.stage = UPLOAD_STAGE_FAILED
                failed_task.detail = "Watchdog refresh failed"
                failed_task.error_message = str(exc)[:4096]
                failed_task.updated_at = utc_now()
                session.add(failed_task)
                session.commit()
            continue

        current_status = str(getattr(refreshed, "status", "") or "").strip().lower()
        _sync_labimageimport_crd(refreshed, create_if_missing=False)
        if current_status == "completed":
            stats["completed"] += 1
        elif current_status == "failed":
            stats["failed"] += 1
    return stats


@dataclass
class _HelperCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _ensure_free_space(required_free_bytes: int, *, context: str) -> None:
    free_bytes = shutil.disk_usage(_image_dir()).free
    if free_bytes >= required_free_bytes:
        return
    free_gib = free_bytes / (1024**3)
    required_gib = required_free_bytes / (1024**3)
    raise HTTPException(
        status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        detail=f"insufficient free storage for {context} (free={free_gib:.1f}Gi, required={required_gib:.1f}Gi)",
    )


def _repair_image_dir_permissions() -> None:
    try:
        _with_pvc_helper(
            ["/bin/sh", "-c", "chown -R 10001:10001 /images && chmod -R u+rwX /images"],
            capture_output=False,
        )
    except Exception:
        logger.warning("Failed to repair image storage ownership/permissions", exc_info=True)


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
) -> _HelperCommandResult:
    helper = f"image-sync-{uuid4().hex[:8]}"
    helper_image = image or PVC_HELPER_IMAGE
    claim = claim_name or settings.kube_image_pvc
    namespace = settings.kube_namespace
    core = kube._client()
    _cleanup_stale_helper_pods()
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=helper,
            namespace=namespace,
            labels={"app.kubernetes.io/part-of": "bretter-labs", "job-type": "image-helper"},
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[
                client.V1Container(
                    name="worker",
                    image=helper_image,
                    image_pull_policy="IfNotPresent",
                    command=["/bin/sh", "-c", "sleep 3600"],
                    volume_mounts=[client.V1VolumeMount(name="images", mount_path="/images")],
                )
            ],
            volumes=[
                client.V1Volume(
                    name="images",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=claim),
                )
            ],
            image_pull_secrets=(
                [client.V1LocalObjectReference(name=settings.image_pull_secret)] if settings.image_pull_secret else None
            ),
        ),
    )
    try:
        core.create_namespaced_pod(namespace=namespace, body=pod)
        deadline = time.time() + POD_READY_WAIT_SECONDS
        while time.time() < deadline:
            snapshot = core.read_namespaced_pod(name=helper, namespace=namespace)
            phase = str(getattr(snapshot.status, "phase", "") or "").strip()
            if phase.lower() in {"running", "succeeded"}:
                break
            if phase.lower() in {"failed", "unknown"}:
                raise RuntimeError(f"helper pod failed to start (phase={phase})")
            time.sleep(POD_READY_SLEEP)
        else:
            raise RuntimeError("timed out waiting for helper pod")

        exec_stream = stream(
            core.connect_get_namespaced_pod_exec,
            helper,
            namespace,
            container="worker",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        while exec_stream.is_open():
            exec_stream.update(timeout=1)
            if exec_stream.peek_stdout():
                chunk = exec_stream.read_stdout()
                if capture_output:
                    stdout_parts.append(chunk)
            if exec_stream.peek_stderr():
                chunk = exec_stream.read_stderr()
                if capture_output:
                    stderr_parts.append(chunk)
            if exec_stream.returncode is not None:
                break
        exec_stream.close()
        rc = int(exec_stream.returncode if exec_stream.returncode is not None else 1)
        result = _HelperCommandResult(
            returncode=rc,
            stdout="".join(stdout_parts) if capture_output else "",
            stderr="".join(stderr_parts) if capture_output else "",
        )
        if rc != 0:
            msg = (result.stderr or result.stdout or "").strip() or "helper command failed"
            raise RuntimeError(msg)
        return result
    finally:
        try:
            core.delete_namespaced_pod(name=helper, namespace=namespace, grace_period_seconds=0)
        except Exception:
            pass


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
set -eu
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
            ttl_seconds_after_finished=TASK_RETENTION_SECONDS,
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


def _copy_pvc_path_to_pvc(
    *,
    source_claim: str,
    source_relative_path: str,
    target_claim: str,
    target_filename: str,
) -> None:
    source_rel = str(source_relative_path or "").strip().lstrip("/")
    target_name = Path(str(target_filename or "").strip()).name
    if not source_rel:
        raise RuntimeError("source relative path is required")
    if not target_name:
        raise RuntimeError("target filename is required")

    core = kube._client()
    batch = client.BatchV1Api()
    job_name = f"iso-copy-{uuid4().hex[:8]}"
    container = client.V1Container(
        name="copy",
        image=PVC_HELPER_IMAGE,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="SOURCE_RELATIVE_PATH", value=source_rel),
            client.V1EnvVar(name="TARGET_FILENAME", value=target_name),
        ],
        command=["/bin/sh", "-c"],
        args=[
            r"""
set -eu
src="/source/${SOURCE_RELATIVE_PATH}"
dst="/target/${TARGET_FILENAME}"
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
            ttl_seconds_after_finished=TASK_RETENTION_SECONDS,
            template=client.V1PodTemplateSpec(spec=spec),
        ),
    )
    batch.create_namespaced_job(namespace=settings.kube_namespace, body=body)

    deadline = time.time() + COPY_JOB_TIMEOUT_SECONDS
    while time.time() < deadline:
        job = batch.read_namespaced_job(name=job_name, namespace=settings.kube_namespace)
        phase = _job_phase(job)
        if phase == "succeeded":
            return
        if phase == "failed":
            break
        time.sleep(2)

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
    raise RuntimeError(f"copy job failed for {target_name}: {err.strip() or 'job failed'}")


def _normalize_pvc_relative_path(raw_path: str) -> str:
    normalized = str(raw_path or "").strip().lstrip("/").replace("\\", "/")
    if not normalized:
        raise RuntimeError("source relative path is required")
    if ".." in Path(normalized).parts:
        raise RuntimeError("source relative path contains invalid traversal segments")
    return normalized


def _materialize_installer_iso_for_image(*, image: Image, source_relative_path: str) -> str:
    source_pvc = str(getattr(image, "source_pvc", "") or "").strip()
    if not source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not ready for clone-based launch; re-import or re-upload the image",
        )
    if not settings.kube_image_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="BLABS_KUBE_IMAGE_PVC is required to attach installer ISO media",
        )
    normalized_relative_path = _normalize_pvc_relative_path(source_relative_path)
    source_name = Path(normalized_relative_path).name
    iso_id_prefix = str(getattr(image, "installer_iso_id", "") or "").strip()[:8]
    if not iso_id_prefix:
        iso_id_prefix = str(getattr(image, "id", "") or uuid4().hex)[:8]
    target_filename = f"installer-{iso_id_prefix}-{source_name}"
    _copy_pvc_path_to_pvc(
        source_claim=settings.kube_image_pvc,
        source_relative_path=normalized_relative_path,
        target_claim=source_pvc,
        target_filename=target_filename,
    )
    return target_filename


def _create_blank_disk_on_source_pvc(
    *,
    source_pvc: str,
    filename: str,
    disk_size_gib: int,
) -> None:
    safe_filename = Path(filename).name
    size_gib = max(1, int(disk_size_gib or 1))
    cmd = f"qemu-img create -f qcow2 /images/{safe_filename} {size_gib}G && sync"
    _with_pvc_helper(
        ["/bin/sh", "-c", cmd],
        claim_name=source_pvc,
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
    requested_gi = _requested_upload_pvc_gi(size_bytes)
    requested_bytes = requested_gi * (1024**3)
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
        if existing_bytes < requested_bytes:
            logger.warning(
                "Recreating source PVC %s with larger capacity (current=%s bytes, required=%s bytes)",
                claim_name,
                existing_bytes,
                requested_bytes,
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


def _validate_file_on_pvc(filename: str, *, claim_name: str | None = None) -> None:
    """
    Validate the image on the PVC using qemu-img check. Raises if invalid.
    """
    safe_filename = Path(filename).name
    result = _with_pvc_helper(
        ["/bin/sh", "-c", f"qemu-img check /images/{safe_filename}"],
        image=settings.runner_image,
        claim_name=claim_name,
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
    try:
        root = _image_dir()
    except OSError:
        logger.warning("Unable to initialize image storage root %s", settings.storage_root, exc_info=True)
        return items
    try:
        for path in root.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            st = path.stat()
            items.append({"name": path.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        logger.warning("Unable to list image storage root %s", root, exc_info=True)
    return items


class ImageImport(BaseModel):
    filename: str
    name: str | None = None
    shared_catalog: bool = False
    skip_validation: bool = False


class ImageCreateFromIso(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    iso_image_id: str = Field(min_length=1, max_length=64)
    os_type: str = Field(default="windows", pattern="^(windows|linux)$")
    drive_size_gib: int = Field(default=64, ge=10, le=1024)
    default_cpu_cores: int = Field(default=2, ge=1, le=16)
    default_ram_mb: int = Field(default=4096, ge=512, le=65536)
    shared_catalog: bool = False
    skip_validation: bool = False


class ImageRename(BaseModel):
    name: str | None = None
    filename: str | None = None
    shared_catalog: bool | None = None
    update_cpu_cores_default: int | None = Field(default=None, ge=1, le=16)
    update_ram_mb_default: int | None = Field(default=None, ge=512, le=65536)
    update_iso_image_id: str | None = Field(default=None, max_length=64)
    skip_validation: bool = False


class IsoImageRename(BaseModel):
    name: str | None = None
    description: str | None = Field(default=None, max_length=1024)
    shared_catalog: bool | None = None


class ImageLaunchUpdateRequest(BaseModel):
    cpu_cores: int | None = Field(default=None, ge=1, le=16)
    ram_mb: int | None = Field(default=None, ge=512, le=65536)
    os_type: str | None = Field(default=None, pattern="^(windows|linux)$")
    console_provider: str | None = Field(default=None, pattern="^(spice|guacamole|guacamole_rdp)$")


class ImageSaveUpdateRequest(BaseModel):
    instance_id: str | None = Field(default=None, min_length=1, max_length=64)


class DirectUploadStart(BaseModel):
    filename: str
    size_bytes: int


class DirectUploadSession(BaseModel):
    task: ImageUploadTaskStatus
    upload_url: str
    upload_token: str


def _role_catalog_rows() -> list[RoleCatalogOut]:
    rows: list[RoleCatalogOut] = []
    for role in roles_catalog():
        rows.append(
            RoleCatalogOut(
                role=role,
                label=role_label(role),
                description=role_description(role),
                permissions=list_permissions_for_role(role),
                editable=role_is_editable(role),
                deletable=role_is_deletable(role),
            )
        )
    return rows


def _save_role_definitions_config(session: Session) -> None:
    cfg = _get_or_create_config(session)
    cfg.rbac_roles_json = json.dumps(role_config_payload(), sort_keys=True, separators=(",", ":"))
    session.add(cfg)


def _actor_can_assign_role(actor: User, target_role: str) -> bool:
    if is_platform_admin(actor):
        return True
    return can_non_platform_assign_role(target_role)


def _role_supports_namespace_scopes(role: str | None) -> bool:
    resolved_role = str(role or "").strip().lower()
    return bool(resolved_role) and resolved_role != Role.PLATFORM_ADMIN


def _assert_actor_can_assign_namespace_scopes(actor: User, namespace_scopes: list[str]) -> None:
    if is_platform_admin(actor):
        return
    requested = [normalize_namespace(item) for item in namespace_scopes if normalize_namespace(item)]
    if not requested:
        return
    actor_scope = _namespace_scope_for_actor(actor) or set()
    if not actor_scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient namespace scope")
    denied = sorted({item for item in requested if item not in actor_scope})
    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"cannot assign namespace scopes outside actor scope: {', '.join(denied)}",
        )


def _effective_user_namespace_scopes(user: User, *, role: str | None = None) -> list[str]:
    resolved_role = str(role or role_for_user(user)).strip().lower()
    if not _role_supports_namespace_scopes(resolved_role):
        return []
    return user_namespace_scopes(user)


def _normalize_user_namespace_scopes_payload(values: list[str] | None) -> list[str]:
    try:
        return normalize_namespace_scopes(values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _user_out(user: User) -> UserOut:
    role = role_for_user(user)
    return UserOut(
        username=user.username,
        role=role,
        team=normalize_team("default"),
        namespace_scopes=_effective_user_namespace_scopes(user, role=role),
        is_admin=can_access_admin(role),
        force_password_change=user.force_password_change,
        permissions=list_permissions_for_role(role),
        can_access_admin=can_access_admin(role),
    )


def _admin_audit_event_out(record: AdminAuditEvent) -> AdminAuditEventOut:
    return AdminAuditEventOut(
        id=record.id,
        actor=record.actor,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=normalize_namespace(getattr(record, "namespace", None)) or "labs",
        action=record.action,
        target_type=record.target_type,
        target_id=record.target_id,
        detail=record.detail or "",
        created_at=record.created_at,
    )


def _team_quota_out(record: TeamQuota) -> TeamQuotaOut:
    return TeamQuotaOut(
        id=record.id,
        # Quotas are namespace-scoped; keep team field for API compatibility only.
        team=normalize_team("default"),
        namespace=normalize_namespace(record.namespace),
        max_concurrent_labs=normalize_optional_limit(getattr(record, "max_concurrent_labs", None)),
        max_cpu_millicores=normalize_optional_limit(getattr(record, "max_cpu_millicores", None)),
        max_memory_mb=normalize_optional_limit(getattr(record, "max_memory_mb", None)),
        max_storage_gib=normalize_optional_limit(getattr(record, "max_storage_gib", None)),
        idle_timeout_minutes_cap=normalize_optional_limit(getattr(record, "idle_timeout_minutes_cap", None)),
        enabled=bool(getattr(record, "enabled", True)),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _is_default_quota_team(team: str | None) -> bool:
    return normalize_team(team) == normalize_team("default")


def _canonical_namespace_quota_rows(rows: list[TeamQuota]) -> list[TeamQuota]:
    selected: dict[str, TeamQuota] = {}
    for row in rows:
        namespace = normalize_namespace(getattr(row, "namespace", None))
        current = selected.get(namespace)
        if current is None:
            selected[namespace] = row
            continue
        if _is_default_quota_team(getattr(row, "team", None)) and not _is_default_quota_team(
            getattr(current, "team", None)
        ):
            selected[namespace] = row
            continue
        row_updated = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
        current_updated = getattr(current, "updated_at", None) or getattr(current, "created_at", None)
        if row_updated and (current_updated is None or row_updated >= current_updated):
            selected[namespace] = row
    return [selected[name] for name in sorted(selected)]


def _collect_known_lab_namespaces(session: Session) -> list[str]:
    available: set[str] = set()
    configured = normalize_namespace(settings.kube_namespace)
    if configured:
        available.add(configured)
    namespace_queries = (
        select(TeamQuota.namespace),
        select(ManagedNamespace.namespace),
        select(Template.namespace),
        select(ContainerTemplate.namespace),
        select(Image.namespace),
        select(IsoImage.namespace),
        select(ContainerImage.namespace),
        select(Instance.namespace),
        select(ContainerInstanceTable.namespace),
        select(ImageUploadTask.namespace),
    )
    for stmt in namespace_queries:
        for raw in session.exec(stmt).all():
            normalized = normalize_namespace(raw)
            if normalized:
                available.add(normalized)
    return sorted(available)


def _filter_enabled_managed_namespaces(session: Session, namespaces: list[str]) -> list[str]:
    normalized = [normalize_namespace(item) for item in namespaces]
    candidates = [item for item in normalized if item]
    if not candidates:
        return []
    rows = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace.in_(candidates))).all()
    enabled_by_namespace = {normalize_namespace(row.namespace): bool(row.enabled) for row in rows}
    return [namespace for namespace in candidates if enabled_by_namespace.get(namespace, True)]


@router.get(
    "/quota-namespaces",
    response_model=list[str],
    dependencies=[Depends(require_permission(Permission.USERS_READ))],
)
def list_quota_namespaces(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[str]:
    if not is_platform_admin(actor):
        scope = sorted(_namespace_scope_for_actor(actor) or [])
        if scope:
            return _filter_enabled_managed_namespaces(session, scope)
        return _filter_enabled_managed_namespaces(session, [normalize_namespace(tenant_namespace_for_team(actor.team))])
    return _filter_enabled_managed_namespaces(session, _collect_known_lab_namespaces(session))


@router.get(
    "/template-namespaces",
    response_model=list[str],
    dependencies=[Depends(require_permission(Permission.TEMPLATES_READ))],
)
def list_template_namespaces(
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[str]:
    scope = _namespace_scope_for_actor(actor)
    if scope is not None:
        return _filter_enabled_managed_namespaces(session, sorted(scope))
    return _filter_enabled_managed_namespaces(session, _collect_known_lab_namespaces(session))


@router.get(
    "/quota-teams",
    response_model=list[str],
    dependencies=[Depends(require_permission(Permission.USERS_READ))],
)
def list_quota_teams(_session: Session = Depends(get_session), _actor: User = Depends(require_user)) -> list[str]:
    return [normalize_team("default")]


@router.get(
    "/users/roles",
    response_model=list[RoleCatalogOut],
    dependencies=[Depends(require_permission(Permission.USERS_READ))],
)
def list_user_roles_catalog(_user: User = Depends(require_user)) -> list[RoleCatalogOut]:
    return _role_catalog_rows()


@router.get(
    "/settings/roles",
    response_model=RoleManagementCatalogOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_role_management_catalog(_user: User = Depends(require_user)) -> RoleManagementCatalogOut:
    return RoleManagementCatalogOut(roles=_role_catalog_rows(), permission_catalog=permission_catalog())


@router.post(
    "/settings/roles",
    response_model=RoleCatalogOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def create_role_definition(
    payload: RoleDefinitionCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> RoleCatalogOut:
    if not is_platform_admin(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required")
    role_id = str(payload.role or "").strip().lower()
    if not role_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="role id is required")
    existing = {row.role for row in _role_catalog_rows()}
    if role_id in existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="role already exists")
    try:
        set_role_definition(
            role=role_id,
            label=str(payload.label or "").strip(),
            description=str(payload.description or "").strip(),
            permissions=list(payload.permissions or []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    _save_role_definitions_config(session)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="create",
        target_type="role",
        target_id=role_id,
        detail=f"permissions={','.join(list_permissions_for_role(role_id))}",
    )
    session.commit()
    for row in _role_catalog_rows():
        if row.role == role_id:
            return row
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to create role")


@router.patch(
    "/settings/roles/{role_id}",
    response_model=RoleCatalogOut,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_role_definition(
    role_id: str,
    payload: RoleDefinitionUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> RoleCatalogOut:
    if not is_platform_admin(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required")
    try:
        normalized_role = normalize_requested_role(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not role_is_editable(normalized_role):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role is not editable")

    role_rows = {row.role: row for row in _role_catalog_rows()}
    current = role_rows.get(normalized_role)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="role not found")

    next_label = current.label if payload.label is None else str(payload.label or "").strip()
    next_description = current.description if payload.description is None else str(payload.description or "").strip()
    next_permissions = current.permissions if payload.permissions is None else list(payload.permissions or [])
    try:
        set_role_definition(
            role=normalized_role,
            label=next_label,
            description=next_description,
            permissions=next_permissions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    _save_role_definitions_config(session)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="role",
        target_id=normalized_role,
        detail=f"permissions={','.join(list_permissions_for_role(normalized_role))}",
    )
    session.commit()
    for row in _role_catalog_rows():
        if row.role == normalized_role:
            return row
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="failed to update role")


@router.delete(
    "/settings/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def remove_role_definition(
    role_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    if not is_platform_admin(actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="platform admin required")
    try:
        normalized_role = normalize_requested_role(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if not role_is_deletable(normalized_role):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role cannot be deleted")
    assigned_users = session.exec(select(User).where(User.role == normalized_role)).all()
    if assigned_users:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"role is in use by {len(assigned_users)} user(s); reassign users before deleting role",
        )
    try:
        delete_role_definition(normalized_role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    _save_role_definitions_config(session)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="delete",
        target_type="role",
        target_id=normalized_role,
    )
    session.commit()


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def add_user(
    payload: UserCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> UserOut:
    existing = session.get(User, payload.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user exists")
    try:
        role = normalize_requested_role(payload.role, payload.is_admin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    team = normalize_team("default")
    if not _actor_can_assign_role(actor, role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role assignment scope")
    namespace_scopes = _normalize_user_namespace_scopes_payload(payload.namespace_scopes)
    if not _role_supports_namespace_scopes(role):
        namespace_scopes = []
    _assert_actor_can_assign_namespace_scopes(actor, namespace_scopes)
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=role,
        team=team,
        is_admin=can_access_admin(role),
        force_password_change=False,
    )
    set_user_namespace_scopes(user, namespace_scopes)
    session.add(user)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=team,
        action="create",
        target_type="user",
        target_id=user.username,
        detail=f"role={role} team={user.team} namespace_scopes={','.join(namespace_scopes) if namespace_scopes else '-'}",
    )
    session.commit()
    session.refresh(user)
    return _user_out(user)


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_permission(Permission.USERS_READ))])
def list_users(session: Session = Depends(get_session), actor: User = Depends(require_user)) -> list[UserOut]:
    stmt = select(User)
    users = session.exec(stmt).all()
    mutated = False
    for user in users:
        if ensure_user_role_fields(user):
            session.add(user)
            mutated = True
        normalized_team = normalize_team("default")
        if getattr(user, "team", None) != normalized_team:
            user.team = normalized_team
            session.add(user)
            mutated = True
        role = role_for_user(user)
        scopes = user_namespace_scopes(user)
        if not _role_supports_namespace_scopes(role) and scopes:
            set_user_namespace_scopes(user, [])
            session.add(user)
            mutated = True
    if mutated:
        session.commit()
        for user in users:
            session.refresh(user)
    return [_user_out(u) for u in users]


@router.patch(
    "/users/{username}", response_model=UserOut, dependencies=[Depends(require_permission(Permission.USERS_WRITE))]
)
def update_user(
    username: str,
    payload: UserUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> UserOut:
    user = session.get(User, username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not _actor_can_assign_role(actor, role_for_user(user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient user scope")
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
    resulting_role = role_for_user(user)
    role_changed = False
    if payload.role is not None or payload.is_admin is not None:
        try:
            role = normalize_requested_role(payload.role, payload.is_admin)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if not _actor_can_assign_role(actor, role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role assignment scope")
        role_changed = role != resulting_role
        user.role = role
        user.is_admin = can_access_admin(role)
        resulting_role = role
    if payload.namespace_scopes is None:
        namespace_scopes = [] if role_changed else user_namespace_scopes(user)
    else:
        namespace_scopes = _normalize_user_namespace_scopes_payload(payload.namespace_scopes)
    if not _role_supports_namespace_scopes(resulting_role):
        namespace_scopes = []
    _assert_actor_can_assign_namespace_scopes(actor, namespace_scopes)
    set_user_namespace_scopes(user, namespace_scopes)
    if payload.team is not None:
        _ = payload.team
    user.team = normalize_team("default")
    user.username = new_username
    session.add(user)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_team(getattr(user, "team", None)),
        action="update",
        target_type="user",
        target_id=user.username,
        detail=(
            f"role={user.role} team={user.team} "
            f"namespace_scopes={','.join(namespace_scopes) if namespace_scopes else '-'}"
        ),
    )
    session.commit()
    session.refresh(user)
    return _user_out(user)


@router.delete(
    "/users/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def remove_user(
    username: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    user = session.get(User, username)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not _actor_can_assign_role(actor, role_for_user(user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient user scope")
    if role_for_user(user) == Role.PLATFORM_ADMIN and username == settings.admin_default_username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot delete default admin")
    revoke_tokens(session, username)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_team(getattr(user, "team", None)),
        action="delete",
        target_type="user",
        target_id=username,
    )
    session.delete(user)
    session.commit()


@router.get(
    "/team-quotas",
    response_model=list[TeamQuotaOut],
    dependencies=[Depends(require_permission(Permission.USERS_READ))],
)
def list_team_quotas(
    session: Session = Depends(get_session), actor: User = Depends(require_user)
) -> list[TeamQuotaOut]:
    rows = session.exec(select(TeamQuota)).all()
    if not is_platform_admin(actor):
        expected_namespace = normalize_namespace(tenant_namespace_for_team(actor.team))
        rows = [row for row in rows if normalize_namespace(row.namespace) == expected_namespace]
    rows = _canonical_namespace_quota_rows(list(rows))
    return [_team_quota_out(row) for row in rows]


@router.post(
    "/team-quotas",
    response_model=TeamQuotaOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def create_team_quota(
    payload: TeamQuotaCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> TeamQuotaOut:
    namespace = normalize_namespace(payload.namespace)
    if not is_platform_admin(actor):
        expected_namespace = normalize_namespace(tenant_namespace_for_team(actor.team))
        if namespace != expected_namespace:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"namespace quotas must target namespace {expected_namespace}",
            )
    existing = session.exec(select(TeamQuota).where(TeamQuota.namespace == namespace)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"quota already exists for namespace '{namespace}'",
        )
    now = utc_now()
    row = TeamQuota(
        id=str(uuid4()),
        team=normalize_team("default"),
        namespace=namespace,
        max_concurrent_labs=normalize_optional_limit(payload.max_concurrent_labs),
        max_cpu_millicores=normalize_optional_limit(payload.max_cpu_millicores),
        max_memory_mb=normalize_optional_limit(payload.max_memory_mb),
        max_storage_gib=normalize_optional_limit(payload.max_storage_gib),
        idle_timeout_minutes_cap=normalize_optional_limit(payload.idle_timeout_minutes_cap),
        enabled=bool(payload.enabled),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=(GLOBAL_TENANT if is_platform_admin(actor) else actor_tenant(actor)),
        action="create",
        target_type="team_quota",
        target_id=row.id,
        detail=f"namespace={namespace}",
    )
    session.commit()
    session.refresh(row)
    return _team_quota_out(row)


@router.patch(
    "/team-quotas/{quota_id}",
    response_model=TeamQuotaOut,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def update_team_quota(
    quota_id: str,
    payload: TeamQuotaUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> TeamQuotaOut:
    row = session.get(TeamQuota, quota_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team quota not found")
    if not is_platform_admin(actor):
        expected_namespace = normalize_namespace(tenant_namespace_for_team(actor.team))
        if normalize_namespace(row.namespace) != expected_namespace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team quota not found")

    namespace = (
        normalize_namespace(payload.namespace) if payload.namespace is not None else normalize_namespace(row.namespace)
    )
    if not is_platform_admin(actor):
        expected_namespace = normalize_namespace(tenant_namespace_for_team(actor.team))
        if namespace != expected_namespace:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"namespace quotas must target namespace {expected_namespace}",
            )
    conflict = session.exec(
        select(TeamQuota).where(TeamQuota.namespace == namespace).where(TeamQuota.id != row.id)
    ).first()
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"quota already exists for namespace '{namespace}'",
        )

    row.team = normalize_team("default")
    row.namespace = namespace
    if payload.clear_max_concurrent_labs:
        row.max_concurrent_labs = None
    elif payload.max_concurrent_labs is not None:
        row.max_concurrent_labs = normalize_optional_limit(payload.max_concurrent_labs)

    if payload.clear_max_cpu_millicores:
        row.max_cpu_millicores = None
    elif payload.max_cpu_millicores is not None:
        row.max_cpu_millicores = normalize_optional_limit(payload.max_cpu_millicores)

    if payload.clear_max_memory_mb:
        row.max_memory_mb = None
    elif payload.max_memory_mb is not None:
        row.max_memory_mb = normalize_optional_limit(payload.max_memory_mb)

    if payload.clear_max_storage_gib:
        row.max_storage_gib = None
    elif payload.max_storage_gib is not None:
        row.max_storage_gib = normalize_optional_limit(payload.max_storage_gib)

    if payload.clear_idle_timeout_minutes_cap:
        row.idle_timeout_minutes_cap = None
    elif payload.idle_timeout_minutes_cap is not None:
        row.idle_timeout_minutes_cap = normalize_optional_limit(payload.idle_timeout_minutes_cap)

    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)

    row.updated_at = utc_now()
    session.add(row)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=(GLOBAL_TENANT if is_platform_admin(actor) else actor_tenant(actor)),
        action="update",
        target_type="team_quota",
        target_id=row.id,
        detail=f"namespace={row.namespace}",
    )
    session.commit()
    session.refresh(row)
    return _team_quota_out(row)


@router.delete(
    "/team-quotas/{quota_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
def delete_team_quota(
    quota_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    row = session.get(TeamQuota, quota_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team quota not found")
    if not is_platform_admin(actor):
        expected_namespace = normalize_namespace(tenant_namespace_for_team(actor.team))
        if normalize_namespace(row.namespace) != expected_namespace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team quota not found")
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=(GLOBAL_TENANT if is_platform_admin(actor) else actor_tenant(actor)),
        action="delete",
        target_type="team_quota",
        target_id=row.id,
        detail=f"namespace={row.namespace}",
    )
    session.delete(row)
    session.commit()


def _iso_to_model(record: IsoImage) -> IsoImageMeta:
    return IsoImageMeta(
        id=record.id,
        name=record.name,
        description=str(getattr(record, "description", "") or ""),
        filename=record.filename,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=_record_namespace(record),
        shared_catalog=_record_shared_catalog(record),
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.get(
    "/iso-images",
    response_model=list[IsoImageMeta],
    dependencies=[Depends(require_permission(Permission.IMAGES_READ))],
)
def list_iso_images(
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[IsoImageMeta]:
    requested_namespace = _requested_namespace_hint(request)
    if requested_namespace:
        assert_actor_can_access_namespace(actor, requested_namespace)
    rows = session.exec(select(IsoImage)).all()
    visible = [row for row in rows if _record_visible_for_actor(row, actor, requested_namespace=requested_namespace)]
    return [_iso_to_model(row) for row in visible]


@router.post(
    "/iso-images",
    response_model=IsoImageMeta,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def upload_iso_image(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Query(default=None),
    description: str | None = Query(default=None, max_length=1024),
    shared_catalog: bool = Query(default=False),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> IsoImageMeta:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="filename required")
    if shared_catalog and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can publish shared catalogs",
        )
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_ISO_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid iso type")
    resource_tenant = resolve_resource_tenant(actor)
    resource_namespace = resolve_resource_namespace(actor, request=request, fallback_namespace=settings.kube_namespace)
    namespace_policy = get_namespace_runtime_policy(session, resource_namespace)
    upload_max_bytes = max(1, int(getattr(namespace_policy, "upload_max_bytes", MAX_UPLOAD_BYTES) or MAX_UPLOAD_BYTES))

    original_name = Path(file.filename).name
    stored_filename = f"{uuid4().hex[:8]}-{original_name}"
    dest_path = _iso_dir() / stored_filename
    size_bytes = 0
    sha256 = hashlib.sha256()
    try:
        with dest_path.open("wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                _ensure_free_space(MIN_FREE_UPLOAD_BYTES + len(chunk), context="iso upload")
                size_bytes += len(chunk)
                if size_bytes > upload_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"ISO too large for namespace {resource_namespace} (max {upload_max_bytes} bytes)",
                    )
                sha256.update(chunk)
                buffer.write(chunk)
    except HTTPException:
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"iso upload failed: {exc}"
        ) from exc

    if size_bytes <= 0:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded ISO is empty")

    record = IsoImage(
        id=str(uuid4()),
        name=str(name or original_name).strip() or original_name,
        description=str(description or "").strip(),
        filename=stored_filename,
        tenant=resource_tenant,
        namespace=resource_namespace,
        shared_catalog=bool(shared_catalog),
        checksum=sha256.hexdigest(),
        size_bytes=size_bytes,
        created_at=utc_now(),
    )
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=resource_tenant,
        action="create",
        target_type="iso_image",
        target_id=record.id,
        detail=f"namespace={record.namespace} filename={record.filename} size_bytes={record.size_bytes}",
    )
    session.commit()
    session.refresh(record)
    return _iso_to_model(record)


@router.patch(
    "/iso-images/{iso_image_id}",
    response_model=IsoImageMeta,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def update_iso_image(
    iso_image_id: str,
    payload: IsoImageRename,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> IsoImageMeta:
    record = session.get(IsoImage, iso_image_id)
    if (
        not record
        or not _tenant_scoped_record(record, actor, include_global=True)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="iso image not found")
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    if payload.shared_catalog is not None and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can change shared catalog scope",
        )
    if payload.name is not None:
        trimmed_name = str(payload.name or "").strip()
        if not trimmed_name:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name cannot be empty")
        record.name = trimmed_name
    if payload.description is not None:
        record.description = str(payload.description or "").strip()
    if payload.shared_catalog is not None:
        record.shared_catalog = bool(payload.shared_catalog)
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="update",
        target_type="iso_image",
        target_id=record.id,
        detail=f"namespace={_record_namespace(record)} name={record.name}",
    )
    session.commit()
    session.refresh(record)
    return _iso_to_model(record)


@router.delete(
    "/iso-images/{iso_image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def delete_iso_image(
    iso_image_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(IsoImage, iso_image_id)
    if (
        not record
        or not _tenant_scoped_record(record, actor, include_global=True)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="iso image not found")
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    in_use = session.exec(
        select(Image)
        .where(Image.installer_iso_id == iso_image_id)
        .where(Image.tenant == normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT))
    ).all()
    if in_use:
        names = [str(row.name or row.id) for row in in_use[:3]]
        suffix = "" if len(in_use) <= 3 else ", ..."
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ISO is in use by images: {', '.join(names)}{suffix}",
        )
    iso_path = _iso_dir() / Path(record.filename).name
    if iso_path.exists():
        try:
            iso_path.unlink()
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail="failed to delete iso"
            ) from exc
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="delete",
        target_type="iso_image",
        target_id=record.id,
        detail=f"namespace={_record_namespace(record)} filename={record.filename}",
    )
    session.delete(record)
    session.commit()


@router.post(
    "/images",
    response_model=ImageUploadTaskStatus,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def upload_image(
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageUploadTaskStatus:
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
    resource_namespace = resolve_resource_namespace(actor, request=request, fallback_namespace=settings.kube_namespace)
    namespace_policy = get_namespace_runtime_policy(session, resource_namespace)
    upload_max_bytes = max(1, int(getattr(namespace_policy, "upload_max_bytes", MAX_UPLOAD_BYTES) or MAX_UPLOAD_BYTES))
    size_bytes = 0
    filename = Path(file.filename).name
    task_id = str(uuid4())
    image_id = str(uuid4())

    def _write_upload_to_path(dest_path: Path) -> int:
        written = 0
        with dest_path.open("wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                _ensure_free_space(MIN_FREE_UPLOAD_BYTES + len(chunk), context="upload")
                written += len(chunk)
                if written > upload_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"image too large for namespace {resource_namespace} (max {upload_max_bytes} bytes)",
                    )
                buffer.write(chunk)
        return written

    try:
        dest_path = _image_dir() / filename
        size_bytes = _write_upload_to_path(dest_path)
        if size_bytes == 0:
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
    except PermissionError:
        logger.warning("Upload permission denied for %s; attempting storage permission repair", filename, exc_info=True)
        _repair_image_dir_permissions()
        try:
            file.file.seek(0)
        except Exception:
            pass
        try:
            dest_path = _image_dir() / filename
            size_bytes = _write_upload_to_path(dest_path)
            if size_bytes == 0:
                dest_path.unlink(missing_ok=True)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to upload %s after permission repair: %s", filename, exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"upload failed: {exc}"
            ) from exc
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
        tenant=resolve_resource_tenant(actor),
        namespace=resource_namespace,
        cluster_id=local_cluster_id(),
        size_bytes=size_bytes,
        status="finalizing",
        stage=UPLOAD_STAGE_UPLOADED,
        progress_percent=0,
        detail="Upload complete; queued for normalization",
        error_message=None,
        retry_count=0,
        max_retries=FINALIZE_MAX_RETRIES,
        next_retry_at=None,
        last_retry_error=None,
        finalize_started_at=utc_now(),
        image_id=image_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(task)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        action="create",
        target_type="image_upload_task",
        target_id=task.id,
        detail=f"filename={task.filename} size_bytes={task.size_bytes}",
    )
    session.commit()
    session.refresh(task)

    try:
        _ensure_upload_task_finalize_job(task)
        session.add(task)
        session.commit()
        session.refresh(task)
    except Exception as exc:
        task.status = "failed"
        task.stage = UPLOAD_STAGE_FAILED
        task.detail = "Failed to submit finalize job"
        task.error_message = str(exc)
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)

    _sync_labimageimport_crd(task, create_if_missing=True)
    return _upload_task_out(task)


@router.post(
    "/images/direct-upload/start",
    response_model=DirectUploadSession,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def start_direct_upload(
    payload: DirectUploadStart,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> DirectUploadSession:
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
        tenant=resolve_resource_tenant(actor),
        namespace=resolve_resource_namespace(actor, request=request, fallback_namespace=settings.kube_namespace),
        cluster_id=local_cluster_id(),
        size_bytes=payload.size_bytes,
        status="uploading",
        stage=UPLOAD_STAGE_UPLOADED,
        progress_percent=0,
        detail="Upload session initialized",
        error_message=None,
        retry_count=0,
        max_retries=FINALIZE_MAX_RETRIES,
        image_id=str(uuid4()),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(task)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        action="create",
        target_type="image_upload_task",
        target_id=task.id,
        detail=f"direct=true filename={task.filename} size_bytes={task.size_bytes}",
    )
    session.commit()
    session.refresh(task)

    try:
        task.upload_pvc = _create_direct_upload_datavolume(task)
        token = _request_direct_upload_token(task.upload_pvc)
        task.detail = "Uploading image directly to CDI DataVolume"
        task.stage = UPLOAD_STAGE_UPLOADED
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
    except Exception as exc:
        task.status = "failed"
        task.stage = UPLOAD_STAGE_FAILED
        task.detail = "Failed to initialize direct CDI upload"
        task.error_message = str(exc)
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    _sync_labimageimport_crd(task, create_if_missing=True)
    return DirectUploadSession(task=_upload_task_out(task), upload_url=upload_url, upload_token=token)


@router.get(
    "/images/upload-tasks/{task_id}",
    response_model=ImageUploadTaskStatus,
    dependencies=[Depends(require_permission(Permission.IMAGES_READ))],
)
def get_upload_task(
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageUploadTaskStatus:
    task = session.get(ImageUploadTask, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")
    if not _tenant_scoped_record(task, actor, include_global=False) or not _namespace_scoped_record(task, actor):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")
    try:
        task = _refresh_upload_task(task, session)
    except Exception as exc:
        logger.error("Failed to refresh upload task %s: %s", task_id, exc, exc_info=True)
        task.status = "failed"
        task.stage = UPLOAD_STAGE_FAILED
        task.detail = "Internal error while refreshing upload task"
        task.error_message = str(exc)
        task.updated_at = utc_now()
        session.add(task)
        session.commit()
        session.refresh(task)
    _sync_labimageimport_crd(task, create_if_missing=False)
    return _upload_task_out(task)


@router.get(
    "/operations/upload-tasks",
    response_model=list[ImageUploadTaskStatus],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_operation_upload_tasks(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ImageUploadTaskStatus]:
    requested_namespace = _requested_namespace_hint(request)
    if requested_namespace:
        assert_actor_can_access_namespace(actor, requested_namespace)
    rows = session.exec(
        select(ImageUploadTask)
        .where(ImageUploadTask.status.in_(sorted(UPLOAD_ACTIVE_STATUSES | UPLOAD_FAILED_STATUSES)))
        .order_by(ImageUploadTask.updated_at.desc())
    ).all()
    out: list[ImageUploadTaskStatus] = []
    for row in rows:
        if not _record_visible_for_actor(row, actor, requested_namespace=requested_namespace):
            continue
        if not _namespace_scoped_record(row, actor):
            continue
        try:
            row = _refresh_upload_task(row, session)
        except Exception as exc:
            logger.warning("Failed to refresh operation upload task %s: %s", row.id, exc, exc_info=True)
        out.append(_upload_task_out(row))
        if len(out) >= limit:
            break
    return out


@router.post(
    "/operations/upload-tasks/{task_id}/retry",
    response_model=ImageUploadTaskStatus,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def retry_operation_upload_task(
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageUploadTaskStatus:
    task = session.get(ImageUploadTask, task_id)
    if (
        not task
        or not _record_visible_for_actor(task, actor, requested_namespace=None)
        or not _namespace_scoped_record(task, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")

    status_name = str(getattr(task, "status", "") or "").strip().lower()
    if status_name == "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="upload task is already completed")
    if status_name == "uploading":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="upload task is still uploading; cancel before retrying",
        )

    task.retry_count = 0
    task.next_retry_at = None
    task.last_retry_error = None
    task.error_message = None
    task.updated_at = utc_now()
    if status_name == "importing":
        task.status = "importing"
        task.stage = UPLOAD_STAGE_SEEDED
        task.copy_job = None
        task.detail = "Retry requested by admin; re-queueing clone source import"
    else:
        task.status = "finalizing"
        task.stage = UPLOAD_STAGE_UPLOADED
        task.progress_percent = 0
        task.detail = "Retry requested by admin; re-queueing image finalization"
        try:
            _ensure_upload_task_finalize_job(task, force_recreate=True)
        except Exception as exc:
            task.status = "failed"
            task.stage = UPLOAD_STAGE_FAILED
            task.error_message = str(exc)
            task.detail = "Failed to re-queue finalization"
    session.add(task)
    session.commit()
    session.refresh(task)
    try:
        task = _refresh_upload_task(task, session)
    except Exception as exc:
        logger.warning("Retry refresh failed for upload task %s: %s", task.id, exc, exc_info=True)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        action="retry",
        target_type="image_upload_task",
        target_id=task.id,
        detail=f"namespace={_record_namespace(task)} status={task.status}",
    )
    session.commit()
    session.refresh(task)
    _sync_labimageimport_crd(task, create_if_missing=False)
    return _upload_task_out(task)


@router.post(
    "/operations/upload-tasks/{task_id}/cancel",
    response_model=ImageUploadTaskStatus,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def cancel_operation_upload_task(
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageUploadTaskStatus:
    task = session.get(ImageUploadTask, task_id)
    if (
        not task
        or not _record_visible_for_actor(task, actor, requested_namespace=None)
        or not _namespace_scoped_record(task, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")

    status_name = str(getattr(task, "status", "") or "").strip().lower()
    if status_name == "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="completed upload task cannot be canceled")
    _cleanup_task_jobs(task)
    task.status = "failed"
    task.stage = UPLOAD_STAGE_FAILED
    task.detail = "Canceled by admin"
    task.error_message = "canceled by admin"
    task.last_retry_error = "canceled by admin"
    task.next_retry_at = None
    task.updated_at = utc_now()
    session.add(task)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        action="cancel",
        target_type="image_upload_task",
        target_id=task.id,
        detail=f"namespace={_record_namespace(task)}",
    )
    session.commit()
    session.refresh(task)
    _sync_labimageimport_crd(task, create_if_missing=False)
    return _upload_task_out(task)


@router.delete(
    "/operations/upload-tasks/{task_id}",
    response_model=AdminOperationActionResult,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def cleanup_operation_upload_task(
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> AdminOperationActionResult:
    task = session.get(ImageUploadTask, task_id)
    if (
        not task
        or not _record_visible_for_actor(task, actor, requested_namespace=None)
        or not _namespace_scoped_record(task, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload task not found")

    cleaned_file = False
    task_namespace = _record_namespace(task)
    filename = Path(str(getattr(task, "filename", "") or "")).name
    if filename and str(getattr(task, "status", "") or "").strip().lower() != "completed":
        image_record = session.exec(
            select(Image).where(Image.namespace == task_namespace).where(Image.filename == filename)
        ).first()
        if not image_record:
            candidate = _image_dir() / filename
            try:
                if candidate.exists() and candidate.is_file():
                    candidate.unlink()
                    cleaned_file = True
            except Exception as exc:
                logger.warning("Failed to remove upload artifact %s for task %s: %s", candidate, task.id, exc)

    crd_task_id = str(task.id)
    _cleanup_task_jobs(task)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(task, "tenant", None), default=GLOBAL_TENANT),
        action="cleanup",
        target_type="image_upload_task",
        target_id=task.id,
        detail=f"namespace={task_namespace} file_removed={int(cleaned_file)}",
    )
    session.delete(task)
    session.commit()
    # Remove the CRD shadow object too; otherwise a stale LabImageImport can keep
    # recreating CDI importer resources even after the operation task is removed.
    delete_labimageimport_best_effort(crd_task_id)
    return AdminOperationActionResult(
        ok=True,
        detail="Upload task cleanup completed.",
    )


@router.get(
    "/operations/launch-tasks",
    response_model=list[AdminLaunchTaskOut],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_operation_launch_tasks(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[AdminLaunchTaskOut]:
    requested_namespace = _requested_namespace_hint(request)
    if requested_namespace:
        assert_actor_can_access_namespace(actor, requested_namespace)

    vm_rows = session.exec(
        select(Instance).where(Instance.status.in_(sorted(LAUNCH_ACTIVE_STATUSES))).order_by(Instance.started_at.desc())
    ).all()
    container_rows = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.status.in_(sorted(LAUNCH_ACTIVE_STATUSES)))
        .order_by(ContainerInstanceTable.started_at.desc())
    ).all()

    out: list[AdminLaunchTaskOut] = []
    for row in vm_rows:
        if not _record_visible_for_actor(row, actor, requested_namespace=requested_namespace):
            continue
        if not _namespace_scoped_record(row, actor):
            continue
        out.append(_launch_task_out_from_vm(row))
    for row in container_rows:
        if not _record_visible_for_actor(row, actor, requested_namespace=requested_namespace):
            continue
        if not _namespace_scoped_record(row, actor):
            continue
        out.append(_launch_task_out_from_container(row))
    out.sort(key=lambda item: item.started_at, reverse=True)
    return out[:limit]


@router.post(
    "/operations/launch-tasks/{kind}/{task_id}/retry",
    response_model=AdminOperationActionResult,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def retry_operation_launch_task(
    kind: str,
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> AdminOperationActionResult:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"vm", "container"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="kind must be vm or container")

    if normalized_kind == "vm":
        record = session.get(Instance, task_id)
    else:
        record = session.get(ContainerInstanceTable, task_id)
    if (
        not record
        or not _record_visible_for_actor(record, actor, requested_namespace=None)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="launch task not found")

    status_name = str(getattr(record, "status", "") or "").strip().lower()
    if status_name not in LAUNCH_ACTIVE_STATUSES and status_name not in {"stopped"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="launch task is not retryable")
    owner = session.get(User, str(getattr(record, "owner", "") or ""))
    if not owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="launch task owner not found")
    internal_request = _operation_request_for_namespace(_record_namespace(record))
    if normalized_kind == "vm":
        from .user import restart_vm as user_restart_vm

        user_restart_vm(task_id, request=internal_request, user=owner, session=session)
    else:
        from .user_containers import restart_container as user_restart_container

        user_restart_container(task_id, request=internal_request, user=owner, session=session)

    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        action="retry",
        target_type=f"{normalized_kind}_launch_task",
        target_id=task_id,
        detail=f"namespace={_record_namespace(record)} owner={record.owner}",
    )
    session.commit()
    return AdminOperationActionResult(ok=True, detail=f"{normalized_kind.upper()} launch retry submitted.")


@router.post(
    "/operations/launch-tasks/{kind}/{task_id}/cancel",
    response_model=AdminOperationActionResult,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def cancel_operation_launch_task(
    kind: str,
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> AdminOperationActionResult:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "vm":
        record = session.get(Instance, task_id)
    elif normalized_kind == "container":
        record = session.get(ContainerInstanceTable, task_id)
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="kind must be vm or container")
    if (
        not record
        or not _record_visible_for_actor(record, actor, requested_namespace=None)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="launch task not found")

    namespace = _record_namespace(record)
    if normalized_kind == "vm":
        try:
            kube.stop_pod(task_id, record.owner, namespace=namespace)
        except Exception:
            pass
        record.status = "stopped"
        record.last_active_at = utc_now()
        session.add(record)
    else:
        if str(getattr(record, "status", "") or "").strip().lower() != "queued":
            try:
                kube.stop_container_pod(task_id, record.owner, namespace=namespace)
            except Exception:
                pass
            try:
                kube.delete_container_service(task_id, namespace=namespace)
            except Exception:
                pass
        record.status = "stopped"
        record.queue_not_before = None
        record.queue_reason = None
        record.last_active_at = utc_now()
        session.add(record)

    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        action="cancel",
        target_type=f"{normalized_kind}_launch_task",
        target_id=task_id,
        detail=f"namespace={namespace} owner={record.owner}",
    )
    session.commit()
    return AdminOperationActionResult(ok=True, detail=f"{normalized_kind.upper()} launch task canceled.")


@router.delete(
    "/operations/launch-tasks/{kind}/{task_id}",
    response_model=AdminOperationActionResult,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def cleanup_operation_launch_task(
    kind: str,
    task_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> AdminOperationActionResult:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "vm":
        record = session.get(Instance, task_id)
    elif normalized_kind == "container":
        record = session.get(ContainerInstanceTable, task_id)
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="kind must be vm or container")
    if (
        not record
        or not _record_visible_for_actor(record, actor, requested_namespace=None)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="launch task not found")

    namespace = _record_namespace(record)
    if normalized_kind == "vm":
        try:
            kube.delete_pod(task_id, record.owner, disk_pvc=getattr(record, "disk_pvc", None), namespace=namespace)
        except Exception:
            pass
        session.delete(record)
    else:
        try:
            kube.delete_container_pod(task_id, record.owner, namespace=namespace)
        except Exception:
            pass
        try:
            kube.delete_container_service(task_id, namespace=namespace)
        except Exception:
            pass
        session.delete(record)

    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        action="cleanup",
        target_type=f"{normalized_kind}_launch_task",
        target_id=task_id,
        detail=f"namespace={namespace} owner={record.owner}",
    )
    session.commit()
    return AdminOperationActionResult(ok=True, detail=f"{normalized_kind.upper()} launch task cleaned up.")


@router.post(
    "/images/import",
    response_model=ImageCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def import_image(
    payload: ImageImport,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageCreateResponse:
    resource_tenant = resolve_resource_tenant(actor)
    resource_namespace = resolve_resource_namespace(actor, request=request, fallback_namespace=settings.kube_namespace)
    shared_catalog = bool(payload.shared_catalog)
    if shared_catalog and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can publish shared catalogs",
        )
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM storage is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    dest_path = _image_dir() / Path(payload.filename).name
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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"image conversion failed: {exc}"
            ) from exc
        dest_path = _image_dir() / converted_name
        if not dest_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"converted image missing on storage: {converted_name}",
            )
    existing = session.exec(
        select(Image)
        .where(Image.filename == dest_path.name)
        .where(Image.tenant == resource_tenant)
        .where(Image.namespace == resource_namespace)
    ).first()
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"source pvc provision failed: {exc}"
        ) from exc

    record = Image(
        id=image_id,
        name=payload.name or dest_path.name,
        filename=dest_path.name,
        tenant=resource_tenant,
        namespace=resource_namespace,
        shared_catalog=shared_catalog,
        cluster_id=local_cluster_id(),
        source_kind="uploaded",
        installer_iso_id=None,
        installer_iso_filename=None,
        installer_os_type=None,
        installer_disk_size_gib=None,
        update_cpu_cores_default=2,
        update_ram_mb_default=4096,
        source_pvc=source_pvc,
        checksum=sha256.hexdigest(),
        size_bytes=size_bytes,
        created_at=utc_now(),
    )
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=resource_tenant,
        action="create",
        target_type="image",
        target_id=record.id,
        detail=f"namespace={record.namespace} filename={record.filename} source_pvc={record.source_pvc or ''}",
    )
    session.commit()
    return ImageCreateResponse(
        id=record.id,
        name=record.name,
        filename=record.filename,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=_record_namespace(record),
        shared_catalog=_record_shared_catalog(record),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        source_kind=str(getattr(record, "source_kind", "") or "uploaded"),
        installer_iso_id=(str(getattr(record, "installer_iso_id", "") or "").strip() or None),
        installer_iso_filename=(str(getattr(record, "installer_iso_filename", "") or "").strip() or None),
        installer_os_type=(str(getattr(record, "installer_os_type", "") or "").strip() or None),
        installer_disk_size_gib=(int(getattr(record, "installer_disk_size_gib", 0) or 0) or None),
        update_cpu_cores_default=int(getattr(record, "update_cpu_cores_default", 0) or 2),
        update_ram_mb_default=int(getattr(record, "update_ram_mb_default", 0) or 4096),
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.get(
    "/images", response_model=list[ImageMeta], dependencies=[Depends(require_permission(Permission.IMAGES_READ))]
)
def list_images(
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[ImageMeta]:
    requested_namespace = _requested_namespace_hint(request)
    if requested_namespace:
        assert_actor_can_access_namespace(actor, requested_namespace)
    pvc_files = {item["name"]: item for item in _list_pvc_files()}
    existing_records = session.exec(select(Image)).all()
    if is_platform_admin(actor):
        namespace_for_auto_rows = requested_namespace or normalize_namespace(settings.kube_namespace) or "labs"
        for fname, info in pvc_files.items():
            if any(
                r.filename == fname
                and _record_namespace(r) == namespace_for_auto_rows
                and normalize_tenant(getattr(r, "tenant", None), default=GLOBAL_TENANT) == GLOBAL_TENANT
                for r in existing_records
            ):
                continue
            record = Image(
                id=str(uuid4()),
                name=fname,
                filename=fname,
                tenant=GLOBAL_TENANT,
                namespace=namespace_for_auto_rows,
                shared_catalog=False,
                cluster_id=local_cluster_id(),
                source_kind="uploaded",
                installer_iso_id=None,
                installer_iso_filename=None,
                installer_os_type=None,
                installer_disk_size_gib=None,
                update_cpu_cores_default=2,
                update_ram_mb_default=4096,
                source_pvc=None,
                checksum="",
                size_bytes=info.get("size", 0),
                created_at=utc_now(),
            )
            session.add(record)
            existing_records.append(record)
        session.commit()
    images = [
        record
        for record in existing_records
        if _record_visible_for_actor(record, actor, requested_namespace=requested_namespace)
    ]
    return [
        ImageMeta(
            id=record.id,
            name=record.name,
            filename=record.filename,
            tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
            namespace=_record_namespace(record),
            shared_catalog=_record_shared_catalog(record),
            cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
            source_kind=str(getattr(record, "source_kind", "") or "uploaded"),
            installer_iso_id=(str(getattr(record, "installer_iso_id", "") or "").strip() or None),
            installer_iso_filename=(str(getattr(record, "installer_iso_filename", "") or "").strip() or None),
            installer_os_type=(str(getattr(record, "installer_os_type", "") or "").strip() or None),
            installer_disk_size_gib=(int(getattr(record, "installer_disk_size_gib", 0) or 0) or None),
            update_cpu_cores_default=int(getattr(record, "update_cpu_cores_default", 0) or 2),
            update_ram_mb_default=int(getattr(record, "update_ram_mb_default", 0) or 4096),
            checksum=record.checksum,
            size_bytes=record.size_bytes,
            created_at=record.created_at,
        )
        for record in images
    ]


def _image_filename_from_name(name: str, *, suffix: str = ".qcow2") -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", str(name or "").strip().lower())
    cleaned = cleaned.strip(".-")
    if not cleaned:
        cleaned = f"image-{uuid4().hex[:8]}"
    if not cleaned.endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    return cleaned


@router.post(
    "/images/create-from-iso",
    response_model=ImageCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def create_image_from_iso(
    payload: ImageCreateFromIso,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageCreateResponse:
    resource_tenant = resolve_resource_tenant(actor)
    resource_namespace = resolve_resource_namespace(actor, request=request, fallback_namespace=settings.kube_namespace)
    requested_shared_catalog = bool(payload.shared_catalog)
    if requested_shared_catalog and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can publish shared catalogs",
        )
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM storage is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )

    iso_record = session.get(IsoImage, payload.iso_image_id)
    if not iso_record or not _record_visible_for_actor(iso_record, actor, requested_namespace=resource_namespace):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ISO image not found")

    filename = _image_filename_from_name(payload.name, suffix=".qcow2")
    existing = session.exec(
        select(Image)
        .where(Image.filename == filename)
        .where(Image.tenant == resource_tenant)
        .where(Image.namespace == resource_namespace)
    ).first()
    if existing:
        filename = _image_filename_from_name(f"{payload.name}-{uuid4().hex[:6]}", suffix=".qcow2")

    image_id = str(uuid4())
    source_pvc = _ensure_image_source_pvc_claim(image_id, int(payload.drive_size_gib) * (1024**3))
    _create_blank_disk_on_source_pvc(
        source_pvc=source_pvc,
        filename=filename,
        disk_size_gib=int(payload.drive_size_gib),
    )

    try:
        iso_rel_path = str((_iso_dir().resolve().relative_to(_image_dir().resolve()) / iso_record.filename).as_posix())
    except Exception:
        iso_rel_path = f"{Path(settings.iso_storage_root).name}/{iso_record.filename}"
    installer_iso_filename = f"installer-{iso_record.id[:8]}-{Path(iso_record.filename).name}"
    _copy_pvc_path_to_pvc(
        source_claim=settings.kube_image_pvc,
        source_relative_path=iso_rel_path,
        target_claim=source_pvc,
        target_filename=installer_iso_filename,
    )
    if not payload.skip_validation:
        try:
            _validate_file_on_pvc(filename, claim_name=source_pvc)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"validation failed: {exc}") from exc

    checksum = hashlib.sha256(f"scratch:{image_id}:{filename}:{payload.drive_size_gib}".encode("utf-8")).hexdigest()
    size_bytes = int(payload.drive_size_gib) * (1024**3)
    record = Image(
        id=image_id,
        name=str(payload.name).strip(),
        filename=filename,
        tenant=resource_tenant,
        namespace=resource_namespace,
        shared_catalog=requested_shared_catalog,
        cluster_id=local_cluster_id(),
        source_kind="scratch",
        installer_iso_id=iso_record.id,
        installer_iso_filename=installer_iso_filename,
        installer_os_type=str(payload.os_type).strip().lower(),
        installer_disk_size_gib=int(payload.drive_size_gib),
        update_cpu_cores_default=int(payload.default_cpu_cores),
        update_ram_mb_default=int(payload.default_ram_mb),
        source_pvc=source_pvc,
        checksum=checksum,
        size_bytes=size_bytes,
        created_at=utc_now(),
    )
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=resource_tenant,
        action="create",
        target_type="image",
        target_id=record.id,
        detail=(
            f"namespace={record.namespace} source_kind=scratch filename={record.filename} "
            f"installer_iso_id={record.installer_iso_id} drive_size_gib={record.installer_disk_size_gib}"
        ),
    )
    session.commit()
    session.refresh(record)
    return ImageCreateResponse(
        id=record.id,
        name=record.name,
        filename=record.filename,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=_record_namespace(record),
        shared_catalog=_record_shared_catalog(record),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        source_kind=str(getattr(record, "source_kind", "") or "uploaded"),
        installer_iso_id=(str(getattr(record, "installer_iso_id", "") or "").strip() or None),
        installer_iso_filename=(str(getattr(record, "installer_iso_filename", "") or "").strip() or None),
        installer_os_type=(str(getattr(record, "installer_os_type", "") or "").strip() or None),
        installer_disk_size_gib=(int(getattr(record, "installer_disk_size_gib", 0) or 0) or None),
        update_cpu_cores_default=int(getattr(record, "update_cpu_cores_default", 0) or 2),
        update_ram_mb_default=int(getattr(record, "update_ram_mb_default", 0) or 4096),
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


@router.post(
    "/images/{image_id}/launch-update",
    response_model=VMInstance,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def launch_image_update_vm(
    image_id: str,
    payload: ImageLaunchUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> VMInstance:
    image = session.get(Image, image_id)
    if (
        not image
        or not _tenant_scoped_record(image, actor, include_global=True)
        or not _namespace_scoped_record(image, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    if not image.source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not ready for clone-based launch; re-import or re-upload the image",
        )
    current_installer_iso_filename = str(getattr(image, "installer_iso_filename", "") or "").strip()
    if current_installer_iso_filename and "/" in current_installer_iso_filename:
        # Backward-compatible migration path: older records can store ISO library-relative paths.
        # Materialize media into the image source PVC so CD attach works in any runtime namespace.
        try:
            image.installer_iso_filename = _materialize_installer_iso_for_image(
                image=image,
                source_relative_path=current_installer_iso_filename,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to materialize installer ISO: {exc}",
            ) from exc
        session.add(image)
        session.commit()
        session.refresh(image)

    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(image, "tenant", None))
    image_namespace = _record_namespace(image)
    template_id = f"img-update-{image.id}"
    template = session.get(Template, template_id)
    desired_os_type = str(payload.os_type or getattr(image, "installer_os_type", "") or "windows").strip().lower()
    image_default_cpu = int(getattr(image, "update_cpu_cores_default", 0) or 2)
    image_default_ram = int(getattr(image, "update_ram_mb_default", 0) or 4096)
    launch_cpu = int(payload.cpu_cores or image_default_cpu)
    launch_ram = int(payload.ram_mb or image_default_ram)
    # Always use Guacamole VNC for image update launch flows.
    console_provider = normalize_vm_console_provider("guacamole")
    if desired_os_type not in {"windows", "linux"}:
        desired_os_type = "windows"

    enabled_namespaces = [image_namespace]
    if template is None:
        template = Template(
            id=template_id,
            name=f"Image Update: {image.name}",
            tenant=managed_tenant,
            namespace=image_namespace,
            shared_catalog=False,
            enabled_namespaces_json=_template_enabled_namespaces_json(enabled_namespaces),
            cluster_id=str(getattr(image, "cluster_id", "") or local_cluster_id()),
            description=f"System-managed template for updating image {image.name}",
            os_type=desired_os_type,
            image_id=image.id,
            cpu_cores=launch_cpu,
            ram_mb=launch_ram,
            auto_delete_minutes=30,
            idle_timeout_minutes=120,
            preclone_pool_size=0,
            preclone_pool_max=0,
            max_active_instances=1,
            enabled=True,
            network_mode="bridge",
            console_provider=console_provider,
            created_at=utc_now(),
        )
    else:
        template.name = f"Image Update: {image.name}"
        template.tenant = managed_tenant
        template.namespace = image_namespace
        template.shared_catalog = False
        template.enabled_namespaces_json = _template_enabled_namespaces_json(enabled_namespaces)
        template.cluster_id = str(getattr(image, "cluster_id", "") or local_cluster_id())
        template.description = f"System-managed template for updating image {image.name}"
        template.os_type = desired_os_type
        template.image_id = image.id
        template.cpu_cores = launch_cpu
        template.ram_mb = launch_ram
        template.auto_delete_minutes = 30
        template.idle_timeout_minutes = 120
        template.preclone_pool_size = 0
        template.preclone_pool_max = 0
        template.max_active_instances = 1
        template.network_mode = "bridge"
        template.console_provider = console_provider
        template.enabled = True

    session.add(template)
    session.commit()
    session.refresh(template)

    # start_vm enforces user/team visibility; for platform admins launching scoped resources,
    # align synthetic launch actor team to the image tenant.
    launch_actor = actor
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if is_platform_admin(actor) and image_tenant not in {GLOBAL_TENANT, actor_tenant(actor)}:
        launch_actor = User(**actor.model_dump())
        launch_actor.team = image_tenant

    from .user import start_vm as user_start_vm

    try:
        instance = user_start_vm(template.id, request=request, user=launch_actor, session=session)
    finally:
        persisted_template = session.get(Template, template_id)
        if persisted_template:
            persisted_template.enabled = False
            session.add(persisted_template)
            session.commit()

    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="launch_update",
        target_type="image",
        target_id=image.id,
        detail=f"namespace={image_namespace} template_id={template_id}",
    )
    session.commit()
    return instance


@router.post(
    "/images/{image_id}/save-update",
    response_model=AdminOperationActionResult,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def save_image_update_vm(
    image_id: str,
    payload: ImageSaveUpdateRequest | None = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> AdminOperationActionResult:
    image = session.get(Image, image_id)
    if (
        not image
        or not _tenant_scoped_record(image, actor, include_global=True)
        or not _namespace_scoped_record(image, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")

    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(image, "tenant", None))
    image_namespace = _record_namespace(image)
    source_pvc = str(getattr(image, "source_pvc", "") or "").strip()
    template_id = f"img-update-{image.id}"
    requested_instance_id = str(getattr(payload, "instance_id", "") or "").strip()

    update_instances = session.exec(
        select(Instance).where(Instance.template_id == template_id).order_by(Instance.started_at.desc())
    ).all()
    if requested_instance_id:
        update_instances = [
            record for record in update_instances if str(getattr(record, "id", "") or "") == requested_instance_id
        ]
        if not update_instances:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="update VM instance not found")

    active_statuses = {"queued", "pending", "running", "unknown"}
    active_instance = next(
        (
            record
            for record in update_instances
            if str(getattr(record, "status", "") or "").strip().lower() in active_statuses
        ),
        None,
    )

    stopped_instance_id = ""
    if active_instance is not None:
        instance_namespace = str(getattr(active_instance, "namespace", "") or settings.kube_namespace).strip()
        instance_cluster_id = str(getattr(active_instance, "cluster_id", "") or local_cluster_id()).strip()
        if not instance_cluster_id:
            instance_cluster_id = local_cluster_id()
        try:
            runtime_kube = kube_service_for_cluster(
                session,
                instance_cluster_id,
                require_runtime_enabled=False,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"failed to prepare runtime client: {exc}",
            ) from exc

        use_legacy_orchestration = vm_orchestration_uses_legacy_path()
        write_crd_shadow = vm_orchestration_writes_crd()
        if use_legacy_orchestration:
            try:
                runtime_kube.stop_pod(
                    active_instance.id,
                    str(getattr(active_instance, "owner", "") or ""),
                    namespace=instance_namespace,
                )
            except ApiException as exc:
                if exc.status not in {404, 409, 422}:
                    logger.warning(
                        "Failed to stop update VM pod %s before delete: %s",
                        active_instance.id,
                        exc,
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    "Failed to stop update VM pod %s before delete.",
                    active_instance.id,
                    exc_info=True,
                )
            try:
                runtime_kube.delete_pod(
                    active_instance.id,
                    str(getattr(active_instance, "owner", "") or ""),
                    disk_pvc=getattr(active_instance, "disk_pvc", None),
                    namespace=instance_namespace,
                    delete_disk_pvc=False,
                )
            except ApiException as exc:
                if exc.status not in {404, 409, 422}:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"failed to stop update VM pod: {exc.reason or exc.status}",
                    ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"failed to stop update VM pod: {exc}",
                ) from exc
        if write_crd_shadow:
            try:
                if use_legacy_orchestration:
                    delete_vm_labinstance_best_effort(active_instance.id, namespace=instance_namespace)
                else:
                    delete_vm_labinstance(active_instance.id, namespace=instance_namespace, missing_ok=True)
            except Exception:
                logger.warning(
                    "Failed to clean up LabInstance shadow for saved image update VM %s",
                    active_instance.id,
                    exc_info=True,
                )
        active_instance.status = "stopped"
        active_instance.last_active_at = utc_now()
        session.add(active_instance)
        stopped_instance_id = active_instance.id

    refreshed_template_count = 0
    if source_pvc:
        dependent_templates = session.exec(select(Template).where(Template.image_id == image.id)).all()
        for record in dependent_templates:
            if _is_system_image_update_template(record):
                continue
            template_cluster_id = (
                str(getattr(record, "cluster_id", "") or local_cluster_id()).strip() or local_cluster_id()
            )
            try:
                runtime_kube = kube_service_for_cluster(
                    session,
                    template_cluster_id,
                    require_runtime_enabled=False,
                )
            except Exception:
                logger.warning(
                    "Failed to prepare runtime client for warm-pool refresh template=%s cluster=%s",
                    record.id,
                    template_cluster_id,
                    exc_info=True,
                )
                continue
            desired_pool = max(0, int(getattr(record, "preclone_pool_size", 0) or 0))
            try:
                runtime_kube.ensure_warm_pool(record.id, source_pvc, 0)
                if desired_pool > 0:
                    runtime_kube.ensure_warm_pool(record.id, source_pvc, desired_pool)
                refreshed_template_count += 1
            except Exception:
                logger.warning(
                    "Failed to refresh warm-pool clones for template=%s image=%s",
                    record.id,
                    image.id,
                    exc_info=True,
                )

    detail_parts = []
    if stopped_instance_id:
        detail_parts.append(f"Stopped update VM {stopped_instance_id[:8]}.")
    else:
        detail_parts.append("No active update VM was running.")
    detail_parts.append(f"Refreshed clone pools for {refreshed_template_count} template(s).")
    detail = " ".join(detail_parts)

    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="save_update",
        target_type="image",
        target_id=image.id,
        detail=(
            f"namespace={image_namespace} template_id={template_id} "
            f"stopped_instance_id={stopped_instance_id or '-'} refreshed_templates={refreshed_template_count}"
        ),
    )
    session.commit()
    return AdminOperationActionResult(ok=True, detail=detail)


@router.delete(
    "/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def delete_image(
    image_id: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(Image, image_id)
    if (
        not record
        or not _tenant_scoped_record(record, actor, include_global=True)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    referenced_templates = session.exec(select(Template).where(Template.image_id == image_id)).all()
    system_update_templates = [
        template for template in referenced_templates if _is_system_image_update_template(template)
    ]
    in_use_by_templates = [
        template for template in referenced_templates if not _is_system_image_update_template(template)
    ]
    if in_use_by_templates:
        names = [str(template.name or template.id) for template in in_use_by_templates[:3]]
        suffix = "" if len(in_use_by_templates) <= 3 else ", ..."
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"image is in use by templates: {', '.join(names)}{suffix}",
        )
    if system_update_templates:
        system_template_ids = [template.id for template in system_update_templates]
        referenced_instances = session.exec(select(Instance).where(Instance.template_id.in_(system_template_ids))).all()
        terminal_statuses = {"stopped", "completed", "failed", "error"}
        active_instances = [
            instance
            for instance in referenced_instances
            if str(getattr(instance, "status", "") or "").strip().lower() not in terminal_statuses
        ]
        if active_instances:
            ids = [str(instance.id) for instance in active_instances[:3]]
            suffix = "" if len(active_instances) <= 3 else ", ..."
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"image update vm is still active for this image: {', '.join(ids)}{suffix}",
            )
        for instance in referenced_instances:
            session.delete(instance)
        for template in system_update_templates:
            session.delete(template)
    safe_filename = Path(record.filename).name
    dest_path = _image_dir() / safe_filename
    if dest_path.exists():
        try:
            dest_path.unlink()
        except OSError as local_exc:
            # In hardened deployments backend may run as non-root while image files are root-owned.
            # Fall back to PVC helper deletion so users can still remove images.
            try:
                quoted_name = shlex.quote(safe_filename)
                _with_pvc_helper(
                    ["/bin/sh", "-c", f"rm -f /images/{quoted_name}"],
                    capture_output=False,
                )
            except Exception as helper_exc:  # pragma: no cover
                logger.warning(
                    "Failed to delete image file via local unlink and helper fallback: %s",
                    safe_filename,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail="failed to delete image"
                ) from helper_exc
            logger.info(
                "Deleted image %s via helper fallback after local unlink failed: %s",
                safe_filename,
                local_exc,
            )
    if record.source_pvc:
        try:
            kube._client().delete_namespaced_persistent_volume_claim(
                name=record.source_pvc,
                namespace=settings.kube_namespace,
            )
        except ApiException as exc:
            if exc.status not in {404, 409, 422}:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"failed to delete source pvc: {exc.reason}",
                ) from exc
            logger.info("Source PVC delete skipped for %s: status=%s", record.source_pvc, exc.status)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="delete",
        target_type="image",
        target_id=record.id,
        detail=f"namespace={_record_namespace(record)} filename={record.filename}",
    )
    session.delete(record)
    session.commit()


@router.patch(
    "/images/{image_id}",
    response_model=ImageMeta,
    dependencies=[Depends(require_permission(Permission.IMAGES_WRITE))],
)
def rename_image(
    image_id: str,
    payload: ImageRename,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ImageMeta:
    record = session.get(Image, image_id)
    if (
        not record
        or not _tenant_scoped_record(record, actor, include_global=True)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    if payload.shared_catalog is not None and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can change shared catalog scope",
        )
    new_name = payload.name or record.name
    new_filename = payload.filename or record.filename
    if Path(new_filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image type")
    # Ensure no conflict
    existing = session.exec(
        select(Image)
        .where(Image.filename == new_filename)
        .where(Image.namespace == _record_namespace(record))
        .where(Image.id != image_id)
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="filename already exists")
    current_installer_iso_id = str(getattr(record, "installer_iso_id", "") or "").strip()
    current_installer_iso_filename = str(getattr(record, "installer_iso_filename", "") or "").strip()
    if payload.update_iso_image_id is not None:
        requested_iso_image_id = str(payload.update_iso_image_id).strip()
        if not requested_iso_image_id:
            record.installer_iso_id = None
            record.installer_iso_filename = None
        elif (
            requested_iso_image_id == current_installer_iso_id
            and current_installer_iso_id
            and current_installer_iso_filename
        ):
            # No ISO change requested; keep existing metadata/path.
            pass
        else:
            image_namespace = _record_namespace(record)
            iso_record = session.get(IsoImage, requested_iso_image_id)
            if not iso_record or not _record_visible_for_actor(iso_record, actor, requested_namespace=image_namespace):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ISO image not found")
            if not record.source_pvc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="image is not ready for clone-based launch; re-import or re-upload the image",
                )
            try:
                iso_rel_path = str(
                    (_iso_dir().resolve().relative_to(_image_dir().resolve()) / iso_record.filename).as_posix()
                )
            except Exception:
                iso_rel_path = f"{Path(settings.iso_storage_root).name}/{iso_record.filename}"
            record.installer_iso_id = iso_record.id
            # Keep save/edit latency low by storing a library-relative path.
            # launch-update materializes this into the source PVC before pod create.
            record.installer_iso_filename = iso_rel_path

    if record.filename != new_filename:
        src_path = _image_dir() / record.filename
        dst_path = _image_dir() / new_filename
        try:
            if src_path.exists():
                src_path.replace(dst_path)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"rename failed: {exc}"
            ) from exc
        if record.source_pvc:
            try:
                _with_pvc_helper(
                    [
                        "/bin/sh",
                        "-c",
                        f"if [ -f /images/{record.filename} ]; then mv /images/{record.filename} /images/{new_filename}; fi",
                    ],
                    capture_output=False,
                    claim_name=record.source_pvc,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"source pvc rename failed: {exc}"
                ) from exc

    record.name = new_name
    record.filename = new_filename
    if payload.shared_catalog is not None:
        record.shared_catalog = bool(payload.shared_catalog)
    if payload.update_cpu_cores_default is not None:
        record.update_cpu_cores_default = int(payload.update_cpu_cores_default)
    if payload.update_ram_mb_default is not None:
        record.update_ram_mb_default = int(payload.update_ram_mb_default)
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="update",
        target_type="image",
        target_id=record.id,
        detail=(
            f"namespace={_record_namespace(record)} name={record.name} filename={record.filename} "
            f"installer_iso_id={str(getattr(record, 'installer_iso_id', '') or '').strip()}"
        ),
    )
    session.commit()
    session.refresh(record)
    return ImageMeta(
        id=record.id,
        name=record.name,
        filename=record.filename,
        tenant=managed_tenant,
        namespace=_record_namespace(record),
        shared_catalog=_record_shared_catalog(record),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        source_kind=str(getattr(record, "source_kind", "") or "uploaded"),
        installer_iso_id=(str(getattr(record, "installer_iso_id", "") or "").strip() or None),
        installer_iso_filename=(str(getattr(record, "installer_iso_filename", "") or "").strip() or None),
        installer_os_type=(str(getattr(record, "installer_os_type", "") or "").strip() or None),
        installer_disk_size_gib=(int(getattr(record, "installer_disk_size_gib", 0) or 0) or None),
        update_cpu_cores_default=int(getattr(record, "update_cpu_cores_default", 0) or 2),
        update_ram_mb_default=int(getattr(record, "update_ram_mb_default", 0) or 4096),
        checksum=record.checksum,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
    )


def _normalized_template_rdp_username(value: str | None) -> str:
    return str(value or "").strip()[:128]


def _template_enabled_namespaces(record: Template) -> list[str]:
    raw = getattr(record, "enabled_namespaces_json", "[]")
    payload: list[str] = []
    if isinstance(raw, list):
        payload = [str(item) for item in raw]
    else:
        try:
            decoded = json.loads(str(raw or "[]"))
            if isinstance(decoded, list):
                payload = [str(item) for item in decoded]
        except Exception:
            payload = []
    try:
        normalized = normalize_namespace_scopes(payload)
    except ValueError:
        normalized = []
    if normalized:
        return normalized
    fallback_namespace = _record_namespace(record)
    return [fallback_namespace] if fallback_namespace else []


def _template_enabled_namespaces_json(namespaces: list[str]) -> str:
    return json.dumps(normalize_namespace_scopes(namespaces), separators=(",", ":"))


_IMAGE_UPDATE_TEMPLATE_ID_PREFIX = "img-update-"


def _is_system_image_update_template(record: Template | None) -> bool:
    if record is None:
        return False
    template_id = str(getattr(record, "id", "") or "").strip().lower()
    return template_id.startswith(_IMAGE_UPDATE_TEMPLATE_ID_PREFIX)


def _assert_actor_can_manage_template_namespaces(actor: User, namespaces: list[str]) -> None:
    if is_platform_admin(actor):
        return
    scope = _namespace_scope_for_actor(actor) or set()
    denied = sorted({name for name in namespaces if name not in scope})
    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"namespace enablement access denied: {', '.join(denied)}",
        )


def _template_enabled_for_namespace(record: Template, namespace: str) -> bool:
    selected = normalize_namespace(namespace)
    if not selected:
        return False
    return selected in set(_template_enabled_namespaces(record))


def _template_shared_catalog(record: Template) -> bool:
    return _record_shared_catalog(record)


def _template_visible_for_actor(
    record: Template,
    actor: User,
    *,
    requested_namespace: str | None = None,
) -> bool:
    if not _record_visible_for_actor(record, actor, requested_namespace=requested_namespace):
        return False
    if is_platform_admin(actor):
        return requested_namespace is None or _template_enabled_for_namespace(record, requested_namespace)
    if not _template_shared_catalog(record):
        if requested_namespace:
            return _record_namespace(record) == requested_namespace
        return _namespace_scoped_record(record, actor)
    if requested_namespace:
        return _template_enabled_for_namespace(record, requested_namespace)
    scope = _namespace_scope_for_actor(actor)
    if scope is None:
        return True
    return any(_template_enabled_for_namespace(record, namespace) for namespace in scope)


def _template_to_model(record: Template) -> VMTemplate:
    return VMTemplate(
        id=record.id,
        name=record.name,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=_record_namespace(record),
        shared_catalog=_template_shared_catalog(record),
        enabled_namespaces=_template_enabled_namespaces(record),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        description=record.description,
        os_type=record.os_type,
        image_id=record.image_id,
        cpu_cores=record.cpu_cores,
        ram_mb=record.ram_mb,
        auto_delete_minutes=record.auto_delete_minutes,
        idle_timeout_minutes=record.idle_timeout_minutes,
        preclone_pool_size=record.preclone_pool_size,
        preclone_pool_max=record.preclone_pool_max,
        max_active_instances=max(0, int(getattr(record, "max_active_instances", 2) or 0)),
        enabled=record.enabled,
        network_mode=normalize_vm_network_mode(record.network_mode),
        console_provider=normalize_vm_console_provider(getattr(record, "console_provider", "spice")),
        rdp_default_username=_normalized_template_rdp_username(getattr(record, "rdp_default_username", "")) or None,
        rdp_default_password_configured=secret_is_configured(getattr(record, "rdp_default_password", "")),
        created_at=record.created_at,
    )


@router.post(
    "/templates",
    response_model=VMTemplate,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def create_template(
    payload: VMTemplateCreate,
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> VMTemplate:
    resource_tenant = resolve_resource_tenant(actor, payload.tenant)
    resource_namespace = resolve_resource_namespace(
        actor,
        request=request,
        requested_namespace=payload.namespace,
        fallback_namespace=settings.kube_namespace,
    )
    requested_shared_catalog = bool(payload.shared_catalog) if payload.shared_catalog is not None else False
    if requested_shared_catalog and not is_platform_admin(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can publish shared catalogs",
        )
    image = session.get(Image, payload.image_id)
    if not image or not _record_visible_for_actor(image, actor, requested_namespace=resource_namespace):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {resource_tenant, GLOBAL_TENANT}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"template tenant {resource_tenant} cannot use image tenant {image_tenant}",
        )
    image_namespace = _record_namespace(image)
    image_shared_catalog = _record_shared_catalog(image)
    if image_namespace != resource_namespace and not image_shared_catalog:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"template namespace {resource_namespace} cannot use namespace-owned image namespace "
                f"{image_namespace}; mark image as shared catalog or pick an image in {resource_namespace}"
            ),
        )
    if not image.source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not ready for clone-based launch; re-import or re-upload the image",
        )
    enabled_namespaces = normalize_namespace_scopes(payload.enabled_namespaces)
    if requested_shared_catalog:
        if not enabled_namespaces:
            enabled_namespaces = [resource_namespace]
    else:
        cross_namespace_targets = bool(enabled_namespaces and enabled_namespaces != [resource_namespace])
        if cross_namespace_targets and is_platform_admin(actor):
            # Platform admins can promote a namespace template to shared catalog scope
            # by selecting explicit enabled namespaces across namespaces.
            requested_shared_catalog = True
        elif cross_namespace_targets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="namespace-owned template can only target its own namespace",
            )
        if not requested_shared_catalog:
            enabled_namespaces = [resource_namespace]
    _assert_actor_can_manage_template_namespaces(actor, enabled_namespaces)
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
        tenant=resource_tenant,
        namespace=resource_namespace,
        shared_catalog=requested_shared_catalog,
        enabled_namespaces_json=_template_enabled_namespaces_json(enabled_namespaces),
        cluster_id=str(payload.cluster_id or getattr(image, "cluster_id", "") or local_cluster_id()),
        description=payload.description or "",
        os_type=payload.os_type or "windows",
        image_id=payload.image_id,
        cpu_cores=payload.cpu_cores,
        ram_mb=payload.ram_mb,
        auto_delete_minutes=payload.auto_delete_minutes,
        idle_timeout_minutes=payload.idle_timeout_minutes or settings.idle_timeout_minutes,
        preclone_pool_size=pool_min,
        preclone_pool_max=pool_max,
        max_active_instances=max(0, int(payload.max_active_instances or 0)),
        enabled=payload.enabled,
        network_mode=normalize_vm_network_mode(payload.network_mode),
        console_provider=normalize_vm_console_provider(payload.console_provider),
        rdp_default_username=_normalized_template_rdp_username(payload.rdp_default_username),
        rdp_default_password=(
            encrypt_secret(str(payload.rdp_default_password).strip())
            if payload.rdp_default_password is not None and str(payload.rdp_default_password).strip()
            else ""
        ),
        created_at=utc_now(),
    )
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=resource_tenant,
        action="create",
        target_type="template",
        target_id=record.id,
        detail=(
            f"namespace={record.namespace} shared_catalog={int(record.shared_catalog)} "
            f"enabled_namespaces={','.join(enabled_namespaces)} "
            f"name={record.name} image_id={record.image_id}"
        ),
    )
    session.commit()
    session.refresh(record)
    return _template_to_model(record)


@router.get(
    "/templates",
    response_model=list[VMTemplate],
    dependencies=[Depends(require_permission(Permission.TEMPLATES_READ))],
)
def list_templates(
    request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> list[VMTemplate]:
    requested_namespace = _requested_namespace_hint(request)
    if requested_namespace:
        assert_actor_can_access_namespace(actor, requested_namespace)
    stmt = select(Template)
    scope = _tenant_scope_for_actor(actor, include_global=True)
    if scope is not None:
        stmt = stmt.where(Template.tenant.in_(scope))
    templates = session.exec(stmt).all()
    visible = [
        record
        for record in templates
        if _template_visible_for_actor(record, actor, requested_namespace=requested_namespace)
        and not _is_system_image_update_template(record)
    ]
    return [_template_to_model(record) for record in visible]


@router.patch(
    "/templates/{template_id}",
    response_model=VMTemplate,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def update_template(
    template_id: str,
    payload: VMTemplateUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> VMTemplate:
    updates = payload.model_dump(exclude_unset=True)
    namespace_enable_only = set(updates).issubset({"enabled_namespaces", "shared_catalog"})

    record = session.get(Template, template_id)
    if not record or not _tenant_scoped_record(record, actor, include_global=True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    if _is_system_image_update_template(record):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system-managed image update templates can only be launched from admin/images",
        )
    if not _namespace_scoped_record(record, actor) and not namespace_enable_only:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    next_tenant = managed_tenant
    next_namespace = _record_namespace(record)
    next_shared_catalog = bool(getattr(record, "shared_catalog", False))
    enabled_namespaces = _template_enabled_namespaces(record)
    has_explicit_enabled_namespaces = bool(str(getattr(record, "enabled_namespaces_json", "") or "").strip())

    if payload.enabled is not None and not is_platform_admin(actor) and bool(getattr(record, "shared_catalog", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only platform admins can change shared template enabled state",
        )
    if payload.shared_catalog is not None:
        requested_shared_catalog = bool(payload.shared_catalog)
        current_shared_catalog = bool(getattr(record, "shared_catalog", False))
        if not is_platform_admin(actor):
            if requested_shared_catalog != current_shared_catalog:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="only platform admins can change shared catalog scope",
                )
        else:
            next_shared_catalog = requested_shared_catalog

    if payload.tenant is not None:
        next_tenant = assert_actor_can_manage_tenant(actor, payload.tenant)
    if payload.namespace is not None:
        next_namespace = assert_actor_can_access_namespace(actor, payload.namespace)
    if payload.enabled_namespaces is not None:
        enabled_namespaces = normalize_namespace_scopes(payload.enabled_namespaces)
        _assert_actor_can_manage_template_namespaces(actor, enabled_namespaces)
    elif payload.namespace is not None and not has_explicit_enabled_namespaces:
        enabled_namespaces = [next_namespace]
        _assert_actor_can_manage_template_namespaces(actor, enabled_namespaces)

    if not next_shared_catalog:
        cross_namespace_targets = bool(enabled_namespaces and enabled_namespaces != [next_namespace])
        if cross_namespace_targets and is_platform_admin(actor):
            # Allow platform admins to broaden scope without forcing a separate
            # shared_catalog toggle step in the UI.
            next_shared_catalog = True
        elif cross_namespace_targets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="namespace-owned template can only target its own namespace",
            )
        if not next_shared_catalog:
            enabled_namespaces = [next_namespace]

    if payload.enabled is True and not enabled_namespaces:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="enabled template must include at least one enabled namespace",
        )

    if payload.name is not None:
        record.name = payload.name
    if payload.cluster_id is not None:
        record.cluster_id = str(payload.cluster_id or "").strip() or local_cluster_id()
    if payload.description is not None:
        record.description = payload.description
    if payload.os_type is not None:
        record.os_type = payload.os_type
    if payload.image_id is not None:
        image = session.get(Image, payload.image_id)
        if not image or not _record_visible_for_actor(image, actor, requested_namespace=next_namespace):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
        if not image.source_pvc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="image is not ready for clone-based launch; re-import or re-upload the image",
            )
        image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
        if image_tenant not in {next_tenant, GLOBAL_TENANT}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"template tenant {next_tenant} cannot use image tenant {image_tenant}",
            )
        image_namespace = _record_namespace(image)
        if image_namespace != next_namespace and not _record_shared_catalog(image):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"template namespace {next_namespace} cannot use namespace-owned image namespace "
                    f"{image_namespace}; mark image as shared catalog or keep template in {image_namespace}"
                ),
            )
        record.image_id = payload.image_id
    elif payload.namespace is not None:
        existing_image = session.get(Image, record.image_id)
        if existing_image is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
        image_namespace = _record_namespace(existing_image)
        if image_namespace != next_namespace and not _record_shared_catalog(existing_image):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"template namespace {next_namespace} cannot use namespace-owned image namespace "
                    f"{image_namespace}; mark image as shared catalog or keep template in {image_namespace}"
                ),
            )
    record.tenant = next_tenant
    record.namespace = next_namespace
    record.shared_catalog = next_shared_catalog
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
    if payload.max_active_instances is not None:
        record.max_active_instances = max(0, int(payload.max_active_instances or 0))
    if payload.enabled is not None:
        record.enabled = payload.enabled
    if payload.network_mode is not None:
        record.network_mode = normalize_vm_network_mode(payload.network_mode)
    if payload.console_provider is not None:
        record.console_provider = normalize_vm_console_provider(payload.console_provider)
    if payload.rdp_default_username is not None:
        record.rdp_default_username = _normalized_template_rdp_username(payload.rdp_default_username)
    if payload.rdp_default_password is not None and str(payload.rdp_default_password).strip():
        record.rdp_default_password = encrypt_secret(str(payload.rdp_default_password).strip())
    record.enabled_namespaces_json = _template_enabled_namespaces_json(enabled_namespaces)
    session.add(record)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=next_tenant,
        action="update",
        target_type="template",
        target_id=record.id,
        detail=(
            f"namespace={record.namespace} shared_catalog={int(record.shared_catalog)} "
            f"enabled_namespaces={','.join(enabled_namespaces) if enabled_namespaces else '-'} "
            f"name={record.name} image_id={record.image_id} enabled={record.enabled}"
        ),
    )
    session.commit()
    session.refresh(record)
    return _template_to_model(record)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.TEMPLATES_WRITE))],
)
def delete_template(
    template_id: str,
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> None:
    record = session.get(Template, template_id)
    if (
        not record
        or not _tenant_scoped_record(record, actor, include_global=True)
        or not _namespace_scoped_record(record, actor)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found")
    if _is_system_image_update_template(record):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system-managed image update templates can only be launched from admin/images",
        )
    managed_tenant = assert_actor_can_manage_tenant(actor, getattr(record, "tenant", None))
    referenced_instances = session.exec(select(Instance).where(Instance.template_id == record.id)).all()
    if referenced_instances:
        terminal_statuses = {"stopped", "completed", "failed", "error"}
        active_instances = [
            instance
            for instance in referenced_instances
            if str(getattr(instance, "status", "") or "").strip().lower() not in terminal_statuses
        ]
        if active_instances:
            if force:
                if not is_platform_admin(actor):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="only platform admins can force delete templates with active instances",
                    )
                use_legacy_orchestration = vm_orchestration_uses_legacy_path()
                write_crd_shadow = vm_orchestration_writes_crd()
                cleanup_failures: list[str] = []
                for instance in active_instances:
                    instance_id = str(instance.id)
                    instance_namespace = str(getattr(instance, "namespace", "") or settings.kube_namespace).strip()
                    instance_cluster_id = str(getattr(instance, "cluster_id", "") or local_cluster_id()).strip()
                    try:
                        runtime_kube = kube_service_for_cluster(
                            session,
                            instance_cluster_id or local_cluster_id(),
                            require_runtime_enabled=False,
                        )
                    except Exception as exc:
                        cleanup_failures.append(f"{instance_id} (runtime client: {exc})")
                        continue
                    if use_legacy_orchestration:
                        try:
                            runtime_kube.delete_pod(
                                instance_id,
                                str(getattr(instance, "owner", "") or ""),
                                disk_pvc=getattr(instance, "disk_pvc", None),
                                namespace=instance_namespace,
                            )
                        except ApiException as exc:
                            if exc.status not in {404, 409, 422}:
                                cleanup_failures.append(f"{instance_id} (pod cleanup: {exc})")
                                continue
                        except Exception as exc:
                            cleanup_failures.append(f"{instance_id} (pod cleanup: {exc})")
                            continue
                    if write_crd_shadow:
                        try:
                            if use_legacy_orchestration:
                                delete_vm_labinstance_best_effort(instance_id, namespace=instance_namespace)
                            else:
                                delete_vm_labinstance(instance_id, namespace=instance_namespace, missing_ok=True)
                        except Exception as exc:
                            cleanup_failures.append(f"{instance_id} (LabInstance cleanup: {exc})")
                if cleanup_failures:
                    sample = ", ".join(cleanup_failures[:3])
                    suffix = ", ..." if len(cleanup_failures) > 3 else ""
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "force delete could not clean up some active instances: "
                            f"{sample}{suffix}. resolve them manually, then retry."
                        ),
                    )
            else:
                active_ids = [str(instance.id) for instance in active_instances[:3]]
                suffix = ", ..." if len(active_instances) > 3 else ""
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"template is in use by active instances ({len(active_instances)}): "
                        f"{', '.join(active_ids)}{suffix}. stop/delete those labs first."
                    ),
                )
        for instance in referenced_instances:
            session.delete(instance)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        tenant=managed_tenant,
        action="delete",
        target_type="template",
        target_id=record.id,
        detail=f"namespace={_record_namespace(record)} name={record.name}",
    )
    session.delete(record)
    session.commit()


def _parse_cpu_m(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(parse_quantity(str(value)) * 1000)
    except Exception:
        return 0


def _parse_bytes(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(parse_quantity(str(value)))
    except Exception:
        return 0


def _resource_pct(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((used / total) * 100.0, 1)


def _risk_level_for_pct(pct: float) -> str:
    if pct >= 95:
        return "critical"
    if pct >= 85:
        return "high"
    if pct >= 70:
        return "warning"
    return "healthy"


def _worst_risk(levels: list[str]) -> str:
    rank = {"healthy": 0, "info": 1, "warning": 2, "high": 3, "critical": 4}
    normalized = [lvl if lvl in rank else "healthy" for lvl in levels]
    return max(normalized, key=lambda lvl: rank[lvl]) if normalized else "healthy"


def _node_roles(node: client.V1Node) -> list[str]:
    labels = node.metadata.labels or {}
    roles = [key.split("/", 1)[1] for key in labels if key.startswith("node-role.kubernetes.io/")]
    return sorted({role for role in roles if role}) or ["worker"]


def _pending_reason_for_pod(pod: client.V1Pod) -> tuple[str, str]:
    for cond in pod.status.conditions or []:
        if _to_str(cond.type) == "PodScheduled" and _to_str(cond.status) == "False":
            reason = _to_str(cond.reason) or "Unschedulable"
            detail = _to_str(cond.message) or "Pod is pending scheduling."
            return reason, detail
    for status_obj in list(pod.status.init_container_statuses or []) + list(pod.status.container_statuses or []):
        waiting = status_obj.state.waiting if status_obj.state else None
        if waiting:
            reason = _to_str(waiting.reason) or "ContainerWaiting"
            detail = _to_str(waiting.message) or "Container is waiting to start."
            return reason, detail
    reason = _to_str(pod.status.reason) or "Pending"
    detail = _to_str(pod.status.message) or "Pod is pending."
    return reason, detail


def _collect_metrics_usage() -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], dict[str, int]], bool, str]:
    custom = client.CustomObjectsApi()
    node_usage: dict[str, dict[str, int]] = {}
    pod_usage: dict[tuple[str, str], dict[str, int]] = {}
    try:
        nodes_payload = custom.list_cluster_custom_object(group="metrics.k8s.io", version="v1beta1", plural="nodes")
        for item in nodes_payload.get("items", []):
            metadata = item.get("metadata") or {}
            name = _to_str(metadata.get("name"))
            if not name:
                continue
            usage = item.get("usage") or {}
            node_usage[name] = {
                "cpu_m": _parse_cpu_m(usage.get("cpu")),
                "memory_bytes": _parse_bytes(usage.get("memory")),
            }
        pods_payload = custom.list_cluster_custom_object(group="metrics.k8s.io", version="v1beta1", plural="pods")
        for item in pods_payload.get("items", []):
            metadata = item.get("metadata") or {}
            namespace = _to_str(metadata.get("namespace"))
            name = _to_str(metadata.get("name"))
            if not namespace or not name:
                continue
            cpu_m = 0
            memory_bytes = 0
            for container_metrics in item.get("containers", []):
                usage = (container_metrics or {}).get("usage") or {}
                cpu_m += _parse_cpu_m(usage.get("cpu"))
                memory_bytes += _parse_bytes(usage.get("memory"))
            pod_usage[(namespace, name)] = {"cpu_m": cpu_m, "memory_bytes": memory_bytes}
        return node_usage, pod_usage, True, ""
    except ApiException as exc:
        detail = f"{exc.status} {exc.reason}".strip()
        if not detail:
            detail = "unreachable"
        return {}, {}, False, f"metrics-server unavailable: {detail}"
    except Exception as exc:
        return {}, {}, False, f"metrics-server unavailable: {exc}"


def _collect_longhorn_storage_summary() -> dict:
    summary = {
        "available": False,
        "detail": "Longhorn data unavailable.",
        "version": "",
        "node_count": 0,
        "volume_count": 0,
        "capacity_bytes": 0,
        "free_bytes": 0,
        "used_bytes": 0,
        "utilization_pct": 0.0,
        "risk": "healthy",
        "degraded_nodes": 0,
        "unschedulable_nodes": 0,
        "detached_volumes": 0,
        "volume_robustness": {},
        "volume_states": {},
        "nodes": [],
    }
    custom = client.CustomObjectsApi()
    last_error = ""
    for version in ("v1beta2", "v1beta1"):
        try:
            nodes_payload = custom.list_cluster_custom_object(group="longhorn.io", version=version, plural="nodes")
            volumes_payload = custom.list_cluster_custom_object(group="longhorn.io", version=version, plural="volumes")
            nodes_items = nodes_payload.get("items", [])
            volume_items = volumes_payload.get("items", [])
            total_capacity = 0
            total_free = 0
            degraded_nodes = 0
            unschedulable_nodes = 0
            longhorn_nodes: list[dict] = []

            def condition_status(raw_conditions: object, target_type: str) -> str:
                if isinstance(raw_conditions, dict):
                    value = raw_conditions.get(target_type)
                    if isinstance(value, dict):
                        return _to_str(value.get("status"))
                    return ""
                if isinstance(raw_conditions, list):
                    for item in raw_conditions:
                        if not isinstance(item, dict):
                            continue
                        if _to_str(item.get("type")) == target_type:
                            return _to_str(item.get("status"))
                return ""

            for item in nodes_items:
                metadata = item.get("metadata") or {}
                spec = item.get("spec") or {}
                status_obj = item.get("status") or {}
                name = _to_str(metadata.get("name")) or "unknown"
                disk_status = status_obj.get("diskStatus") or {}
                node_capacity = 0
                node_free = 0
                if isinstance(disk_status, dict):
                    for disk in disk_status.values():
                        if not isinstance(disk, dict):
                            continue
                        node_capacity += int(disk.get("storageMaximum") or 0)
                        node_free += int(disk.get("storageAvailable") or 0)
                conditions = status_obj.get("conditions") or []
                ready = condition_status(conditions, "Ready") == "True"
                schedulable_status = condition_status(conditions, "Schedulable")
                allow_scheduling = bool(spec.get("allowScheduling", True))
                schedulable = allow_scheduling and schedulable_status != "False"
                if not ready:
                    degraded_nodes += 1
                if not schedulable:
                    unschedulable_nodes += 1
                total_capacity += node_capacity
                total_free += node_free
                longhorn_nodes.append(
                    {
                        "name": name,
                        "ready": ready,
                        "schedulable": schedulable,
                        "capacity_bytes": node_capacity,
                        "free_bytes": node_free,
                        "utilization_pct": _resource_pct(max(node_capacity - node_free, 0), node_capacity),
                    }
                )
            robustness = Counter()
            states = Counter()
            detached_unknown = 0
            for item in volume_items:
                status_obj = item.get("status") or {}
                state = (_to_str(status_obj.get("state")) or "unknown").lower()
                robustness_state = (_to_str(status_obj.get("robustness")) or "unknown").lower()
                states[state] += 1
                if robustness_state == "unknown" and state == "detached":
                    robustness["detached"] += 1
                    detached_unknown += 1
                else:
                    robustness[robustness_state] += 1
            used_bytes = max(total_capacity - total_free, 0)
            utilization_pct = _resource_pct(used_bytes, total_capacity)
            risk = _risk_level_for_pct(utilization_pct)
            if robustness.get("faulted", 0) > 0:
                risk = _worst_risk([risk, "critical"])
            elif robustness.get("degraded", 0) > 0:
                risk = _worst_risk([risk, "warning"])
            if degraded_nodes > 0:
                risk = _worst_risk([risk, "warning"])
            summary.update(
                {
                    "available": True,
                    "detail": f"Longhorn {version} metrics loaded.",
                    "version": version,
                    "node_count": len(nodes_items),
                    "volume_count": len(volume_items),
                    "capacity_bytes": total_capacity,
                    "free_bytes": total_free,
                    "used_bytes": used_bytes,
                    "utilization_pct": utilization_pct,
                    "risk": risk,
                    "degraded_nodes": degraded_nodes,
                    "unschedulable_nodes": unschedulable_nodes,
                    "detached_volumes": detached_unknown,
                    "volume_robustness": dict(robustness),
                    "volume_states": dict(states),
                    "nodes": longhorn_nodes,
                }
            )
            return summary
        except Exception as exc:
            last_error = str(exc)
            continue
    if last_error:
        summary["detail"] = f"Longhorn metrics unavailable: {last_error}"
    return summary


def _build_resource_recommendations(utilization: dict, pending: dict, nodes: list[dict], longhorn: dict) -> list[dict]:
    recs: list[dict] = []
    for key, label in (("cpu_pct", "CPU"), ("memory_pct", "Memory"), ("disk_pct", "Disk")):
        pct = float(utilization.get(key, 0))
        risk = _risk_level_for_pct(pct)
        if risk in {"warning", "high", "critical"}:
            recs.append(
                {
                    "severity": risk,
                    "title": f"{label} headroom is low ({pct:.1f}%)",
                    "detail": f"{label} requested resources are at {pct:.1f}% of allocatable capacity.",
                    "action": "Scale nodes/resources or reduce per-VM requests and warm pool pressure.",
                }
            )
    not_ready = [n["name"] for n in nodes if n.get("conditions", {}).get("Ready") != "True"]
    if not_ready:
        recs.append(
            {
                "severity": "critical",
                "title": "One or more nodes are NotReady",
                "detail": f"NotReady nodes: {', '.join(not_ready)}.",
                "action": "Check kubelet/runtime health and node connectivity before scheduling additional labs.",
            }
        )
    pressure_nodes = [n["name"] for n in nodes if n.get("pressures")]
    if pressure_nodes:
        recs.append(
            {
                "severity": "high",
                "title": "Node pressure detected",
                "detail": f"Pressure conditions present on: {', '.join(pressure_nodes)}.",
                "action": "Run cleanup, free disk/memory, and verify Longhorn/PVC usage on affected nodes.",
            }
        )
    unschedulable = [n["name"] for n in nodes if not n.get("schedulable", True)]
    if unschedulable:
        recs.append(
            {
                "severity": "warning",
                "title": "Unschedulable nodes present",
                "detail": f"Unschedulable nodes: {', '.join(unschedulable)}.",
                "action": "Uncordon nodes when ready or adjust placement constraints.",
            }
        )
    pending_count = int(pending.get("count") or 0)
    top_reasons = pending.get("top_reasons") or []
    if pending_count > 0:
        top_reason = top_reasons[0]["reason"] if top_reasons else "pending workload"
        recs.append(
            {
                "severity": "high",
                "title": f"{pending_count} pods are pending",
                "detail": f"Top blocker: {top_reason}.",
                "action": "Resolve pending blockers first to avoid delayed lab start times.",
            }
        )
    if longhorn.get("available"):
        lh_risk = longhorn.get("risk", "healthy")
        if lh_risk in {"warning", "high", "critical"}:
            recs.append(
                {
                    "severity": lh_risk,
                    "title": "Longhorn capacity/health risk",
                    "detail": f"Longhorn utilization is {longhorn.get('utilization_pct', 0):.1f}% (risk: {lh_risk}).",
                    "action": "Increase storage headroom, trim stale volumes, and keep replica count low for ephemeral labs.",
                }
            )
        faulted = int((longhorn.get("volume_robustness") or {}).get("faulted", 0))
        degraded = int((longhorn.get("volume_robustness") or {}).get("degraded", 0))
        if faulted > 0 or degraded > 0:
            recs.append(
                {
                    "severity": "critical" if faulted > 0 else "warning",
                    "title": "Longhorn volume robustness issues",
                    "detail": f"Faulted volumes: {faulted}, degraded volumes: {degraded}.",
                    "action": "Repair affected volumes and verify replica/node health before heavy lab activity.",
                }
            )
    if not recs:
        recs.append(
            {
                "severity": "info",
                "title": "Cluster looks healthy",
                "detail": "No immediate capacity or scheduling risks detected.",
                "action": "Keep monitoring pending reasons and pressure thresholds during peak usage.",
            }
        )
    return recs[:10]


@router.get("/resources", dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))])
def cluster_resources() -> dict:
    core = kube._client()
    nodes = core.list_node().items
    pods = core.list_pod_for_all_namespaces().items
    node_usage, pod_usage, metrics_available, metrics_error = _collect_metrics_usage()
    longhorn = _collect_longhorn_storage_summary()

    total_capacity_cpu = 0
    total_capacity_mem = 0
    total_capacity_disk = 0
    total_allocatable_cpu = 0
    total_allocatable_mem = 0
    total_allocatable_disk = 0
    node_requested: dict[str, dict[str, int]] = defaultdict(lambda: {"cpu_m": 0, "memory_bytes": 0, "disk_bytes": 0})

    for node in nodes:
        cap = node.status.capacity or {}
        alloc = node.status.allocatable or {}
        total_capacity_cpu += _parse_cpu_m(cap.get("cpu"))
        total_capacity_mem += _parse_bytes(cap.get("memory"))
        total_capacity_disk += _parse_bytes(cap.get("ephemeral-storage"))
        total_allocatable_cpu += _parse_cpu_m(alloc.get("cpu"))
        total_allocatable_mem += _parse_bytes(alloc.get("memory"))
        total_allocatable_disk += _parse_bytes(alloc.get("ephemeral-storage"))

    requested_cpu = 0
    requested_mem = 0
    requested_disk = 0
    pending_reasons: Counter = Counter()
    pending_reason_examples: dict[str, list[str]] = defaultdict(list)
    pending_pods: list[dict] = []
    pod_consumers: list[dict] = []
    now = datetime.now(timezone.utc)

    for pod in pods:
        pod_cpu = 0
        pod_mem = 0
        pod_disk = 0
        for container in pod.spec.containers or []:
            req = (container.resources and container.resources.requests) or {}
            if "cpu" in req:
                pod_cpu += _parse_cpu_m(req["cpu"])
            if "memory" in req:
                pod_mem += _parse_bytes(req["memory"])
            if "ephemeral-storage" in req:
                pod_disk += _parse_bytes(req["ephemeral-storage"])

        requested_cpu += pod_cpu
        requested_mem += pod_mem
        requested_disk += pod_disk

        namespace = _to_str(pod.metadata.namespace)
        pod_name = _to_str(pod.metadata.name)
        node_name = _to_str(pod.spec.node_name)
        phase = _to_str(pod.status.phase)

        if node_name:
            node_requested[node_name]["cpu_m"] += pod_cpu
            node_requested[node_name]["memory_bytes"] += pod_mem
            node_requested[node_name]["disk_bytes"] += pod_disk

        owner_refs = pod.metadata.owner_references or []
        owner = f"{owner_refs[0].kind}/{owner_refs[0].name}" if owner_refs else ""
        consumer = {
            "namespace": namespace,
            "name": pod_name,
            "owner": owner,
            "phase": phase,
            "node": node_name,
            "requested": {"cpu_m": pod_cpu, "memory_bytes": pod_mem, "disk_bytes": pod_disk},
        }
        if metrics_available:
            consumer["usage"] = pod_usage.get((namespace, pod_name), {"cpu_m": 0, "memory_bytes": 0})
        pod_consumers.append(consumer)

        if phase.lower() == "pending":
            reason, detail = _pending_reason_for_pod(pod)
            pending_reasons[reason] += 1
            detail_trimmed = (detail[:220] + "...") if len(detail) > 220 else detail
            if (
                detail_trimmed
                and detail_trimmed not in pending_reason_examples[reason]
                and len(pending_reason_examples[reason]) < 3
            ):
                pending_reason_examples[reason].append(detail_trimmed)
            created = pod.metadata.creation_timestamp
            age_seconds = int((now - created).total_seconds()) if created else 0
            pending_pods.append(
                {
                    "namespace": namespace,
                    "name": pod_name,
                    "node": node_name,
                    "reason": reason,
                    "detail": detail_trimmed,
                    "age_seconds": max(0, age_seconds),
                }
            )

    node_list = []
    for node in nodes:
        name = _to_str(node.metadata.name)
        internal_ip = ""
        for addr in node.status.addresses or []:
            if addr.type == "InternalIP":
                internal_ip = addr.address
        cap = node.status.capacity or {}
        alloc = node.status.allocatable or {}
        req = node_requested.get(name, {"cpu_m": 0, "memory_bytes": 0, "disk_bytes": 0})
        alloc_cpu = _parse_cpu_m(alloc.get("cpu"))
        alloc_mem = _parse_bytes(alloc.get("memory"))
        alloc_disk = _parse_bytes(alloc.get("ephemeral-storage"))
        cpu_pct = _resource_pct(req["cpu_m"], alloc_cpu)
        mem_pct = _resource_pct(req["memory_bytes"], alloc_mem)
        disk_pct = _resource_pct(req["disk_bytes"], alloc_disk)
        conditions: dict[str, str] = {}
        pressures: list[str] = []
        for cond in node.status.conditions or []:
            ctype = _to_str(cond.type)
            cstatus = _to_str(cond.status)
            if ctype:
                conditions[ctype] = cstatus
            if ctype.endswith("Pressure") and cstatus == "True":
                pressures.append(ctype)
        node_risk = _worst_risk(
            [_risk_level_for_pct(cpu_pct), _risk_level_for_pct(mem_pct), _risk_level_for_pct(disk_pct)]
        )
        if conditions.get("Ready") != "True":
            node_risk = _worst_risk([node_risk, "critical"])
        elif pressures:
            node_risk = _worst_risk([node_risk, "high"])
        if bool(node.spec.unschedulable):
            node_risk = _worst_risk([node_risk, "warning"])
        taints = [f"{t.key}={t.value}:{t.effect}" for t in (node.spec.taints or [])]
        node_entry = {
            "name": name,
            "ip": internal_ip,
            "roles": _node_roles(node),
            "taints": taints,
            "schedulable": not bool(node.spec.unschedulable),
            "conditions": conditions,
            "pressures": pressures,
            "risk": node_risk,
            "capacity": {
                "cpu_m": _parse_cpu_m(cap.get("cpu")),
                "memory_bytes": _parse_bytes(cap.get("memory")),
                "disk_bytes": _parse_bytes(cap.get("ephemeral-storage")),
            },
            "allocatable": {"cpu_m": alloc_cpu, "memory_bytes": alloc_mem, "disk_bytes": alloc_disk},
            "requested": req,
            "utilization_pct": {"cpu": cpu_pct, "memory": mem_pct, "disk": disk_pct},
            # Backward-compatible aliases used by older UI builds.
            "capacity_cpu_m": alloc_cpu,
            "capacity_mem_bytes": alloc_mem,
            "capacity_disk_bytes": alloc_disk,
            "usage": {"cpu_m": req["cpu_m"], "mem_bytes": req["memory_bytes"], "disk_bytes": req["disk_bytes"]},
        }
        if metrics_available:
            node_entry["usage"] = {
                "cpu_m": node_usage.get(name, {}).get("cpu_m", 0),
                "memory_bytes": node_usage.get(name, {}).get("memory_bytes", 0),
                "disk_bytes": req["disk_bytes"],
            }
        node_list.append(node_entry)

    utilization = {
        "cpu_pct": _resource_pct(requested_cpu, total_allocatable_cpu),
        "memory_pct": _resource_pct(requested_mem, total_allocatable_mem),
        "disk_pct": _resource_pct(requested_disk, total_allocatable_disk),
    }
    risk = {
        "cpu": _risk_level_for_pct(utilization["cpu_pct"]),
        "memory": _risk_level_for_pct(utilization["memory_pct"]),
        "disk": _risk_level_for_pct(utilization["disk_pct"]),
    }
    risk["overall"] = _worst_risk([risk["cpu"], risk["memory"], risk["disk"]])

    pending = {
        "count": len(pending_pods),
        "top_reasons": [
            {"reason": reason, "count": count, "examples": pending_reason_examples.get(reason, [])}
            for reason, count in pending_reasons.most_common(8)
        ],
        "pods": sorted(pending_pods, key=lambda item: item.get("age_seconds", 0), reverse=True)[:30],
    }
    top_consumers = {
        "cpu": sorted(
            [row for row in pod_consumers if row["requested"]["cpu_m"] > 0],
            key=lambda row: row["requested"]["cpu_m"],
            reverse=True,
        )[:10],
        "memory": sorted(
            [row for row in pod_consumers if row["requested"]["memory_bytes"] > 0],
            key=lambda row: row["requested"]["memory_bytes"],
            reverse=True,
        )[:10],
        "disk": sorted(
            [row for row in pod_consumers if row["requested"]["disk_bytes"] > 0],
            key=lambda row: row["requested"]["disk_bytes"],
            reverse=True,
        )[:10],
        "metrics_available": metrics_available,
        "metrics_error": metrics_error,
    }
    summary = {
        "total_nodes": len(node_list),
        "ready_nodes": sum(1 for item in node_list if item.get("conditions", {}).get("Ready") == "True"),
        "unschedulable_nodes": sum(1 for item in node_list if not item.get("schedulable", True)),
        "pressure_nodes": sum(1 for item in node_list if item.get("pressures")),
    }
    recommendations = _build_resource_recommendations(
        utilization=utilization,
        pending=pending,
        nodes=node_list,
        longhorn=longhorn,
    )
    headroom = {
        "cpu_m": total_allocatable_cpu - requested_cpu,
        "memory_bytes": total_allocatable_mem - requested_mem,
        "disk_bytes": total_allocatable_disk - requested_disk,
    }

    return {
        "fetched_at": datetime.now(timezone.utc),
        "capacity": {
            "cpu_m": total_capacity_cpu,
            "memory_bytes": total_capacity_mem,
            "disk_bytes": total_capacity_disk,
        },
        "allocatable": {
            "cpu_m": total_allocatable_cpu,
            "memory_bytes": total_allocatable_mem,
            "disk_bytes": total_allocatable_disk,
        },
        "requested": {"cpu_m": requested_cpu, "memory_bytes": requested_mem, "disk_bytes": requested_disk},
        "headroom": headroom,
        "utilization_pct": utilization,
        "risk": risk,
        "summary": summary,
        "nodes": node_list,
        "pending": pending,
        "top_consumers": top_consumers,
        "storage": {"longhorn": longhorn},
        "recommendations": recommendations,
    }


@router.get(
    "/alerts-errors",
    response_model=AlertsAndErrorsView,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def alerts_and_errors(page: int = Query(1, ge=1), session: Session = Depends(get_session)) -> AlertsAndErrorsView:
    max_bytes = min(max(1024, int(settings.error_log_max_bytes)), ALERTS_ERRORS_MAX_LOG_BYTES)
    per_page = max(1, int(ERROR_LOG_PAGE_SIZE))
    alerts, alertmanager_error = _fetch_alertmanager_alerts()
    rdp_readiness = _collect_rdp_readiness_telemetry(session, alerts=alerts)
    log_file_path = _to_str(settings.error_log_file_path)
    if log_file_path:
        error_log = _read_error_log_file(Path(log_file_path), max_bytes=max_bytes, page=page, per_page=per_page)
        if error_log.content.startswith("Log file not found.") or error_log.content.startswith(
            "Failed to read log file:"
        ):
            # Fall back to Kubernetes logs if file logging is not available.
            error_log = _collect_k8s_error_logs(max_bytes=max_bytes, page=page, per_page=per_page)
    else:
        error_log = _collect_k8s_error_logs(max_bytes=max_bytes, page=page, per_page=per_page)

    clear_supported = bool(log_file_path)
    clear_reason = ""
    if not clear_supported:
        clear_reason = "Clear Error Log is unavailable because BLABS_ERROR_LOG_FILE_PATH is not configured."

    return AlertsAndErrorsView(
        fetched_at=datetime.now(timezone.utc),
        alertmanager_url=_to_str(settings.alertmanager_api_url),
        alertmanager_error=alertmanager_error,
        alerts=alerts,
        rdp_readiness=rdp_readiness,
        error_log=error_log,
        error_log_clear_supported=clear_supported,
        error_log_clear_reason=clear_reason,
    )


@router.post(
    "/alerts-errors/clear",
    response_model=ErrorLogClearResult,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def clear_alerts_error_log(
    actor: User = Depends(require_user), session: Session = Depends(get_session)
) -> ErrorLogClearResult:
    log_file_path = _to_str(settings.error_log_file_path)
    if not log_file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clear Error Log is unavailable because BLABS_ERROR_LOG_FILE_PATH is not configured.",
        )
    try:
        result = _clear_backend_error_logs(Path(log_file_path))
        _record_admin_audit_event(
            session,
            actor=actor.username,
            action="clear",
            target_type="error_logs",
            target_id="backend",
            detail=result.detail,
        )
        session.commit()
        return result
    except Exception as exc:
        logger.warning("Failed clearing error log file %s: %s", log_file_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clear error log: {exc}"
        )


@router.get(
    "/audit-events",
    response_model=list[AdminAuditEventOut],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_admin_audit_events(
    limit: int = Query(50, ge=1, le=50),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource: str | None = Query(default=None),
    target: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> list[AdminAuditEventOut]:
    stmt = select(AdminAuditEvent)
    actor_filter = str(actor or "").strip()
    action_filter = str(action or "").strip()
    resource_filter = str(resource or "").strip()
    target_filter = str(target or "").strip()
    namespace_raw = str(namespace or "").strip()
    namespace_filter = normalize_namespace(namespace_raw) if namespace_raw else ""
    if namespace_raw and not namespace_filter:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid namespace filter")

    namespace_scope = _namespace_scope_for_actor(user)
    if not is_platform_admin(user):
        stmt = stmt.where(AdminAuditEvent.tenant == actor_tenant(user))
        scope = sorted(namespace_scope or [])
        if not scope:
            return []
        stmt = stmt.where(AdminAuditEvent.namespace.in_(scope))

    if namespace_filter:
        if not is_platform_admin(user):
            assert_actor_can_access_namespace(user, namespace_filter)
        stmt = stmt.where(AdminAuditEvent.namespace == namespace_filter)
    if actor_filter:
        stmt = stmt.where(AdminAuditEvent.actor == actor_filter)
    if action_filter:
        stmt = stmt.where(AdminAuditEvent.action == action_filter)
    if resource_filter:
        stmt = stmt.where(AdminAuditEvent.target_type == resource_filter)
    if target_filter:
        stmt = stmt.where(AdminAuditEvent.target_id == target_filter)
    rows = session.exec(stmt.order_by(AdminAuditEvent.created_at.desc())).all()[:limit]
    return [_admin_audit_event_out(row) for row in rows]


@router.get(
    "/audit-events/export",
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def export_admin_audit_events(
    limit: int = Query(50, ge=1, le=2000),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource: str | None = Query(default=None),
    target: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    rows = list_admin_audit_events(
        limit=limit,
        actor=actor,
        action=action,
        resource=resource,
        target=target,
        namespace=namespace,
        session=session,
        user=user,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "created_at", "actor", "namespace", "action", "resource", "target", "detail"])
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.created_at.isoformat(),
                row.actor,
                row.namespace,
                row.action,
                row.target_type,
                row.target_id,
                row.detail,
            ]
        )
    filename = f"admin-audit-events-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete(
    "/audit-events",
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def clear_admin_audit_events(
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> dict[str, int]:
    stmt = select(AdminAuditEvent)
    if not is_platform_admin(user):
        stmt = stmt.where(AdminAuditEvent.tenant == actor_tenant(user))
        scope = sorted(_namespace_scope_for_actor(user) or [])
        if not scope:
            return {"deleted": 0}
        stmt = stmt.where(AdminAuditEvent.namespace.in_(scope))
    rows = session.exec(stmt).all()
    deleted = 0
    for row in rows:
        session.delete(row)
        deleted += 1
    session.commit()
    return {"deleted": deleted}


@router.get(
    "/settings/concurrency",
    response_model=ConcurrencySettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_concurrency_settings(session: Session = Depends(get_session)) -> ConcurrencySettings:
    cfg = session.get(Config, 1)
    max_concurrent_vms = int(
        getattr(cfg, "max_concurrent_vms", settings.max_concurrent_vms) or settings.max_concurrent_vms
    )
    per_user_vm_limit = int(getattr(cfg, "per_user_vm_limit", settings.per_user_vm_limit) or settings.per_user_vm_limit)
    return ConcurrencySettings(
        max_concurrent_vms=max(1, max_concurrent_vms),
        per_user_vm_limit=max(1, per_user_vm_limit),
    )


@router.get(
    "/settings/idle-timeout",
    response_model=IdleTimeoutSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_idle_timeout_settings(session: Session = Depends(get_session)) -> IdleTimeoutSettings:
    cfg = session.get(Config, 1)
    idle_timeout_minutes = int(
        getattr(cfg, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes
    )
    return IdleTimeoutSettings(idle_timeout_minutes=max(1, idle_timeout_minutes))


@router.post(
    "/settings/concurrency",
    response_model=ConcurrencySettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_concurrency(
    settings_payload: ConcurrencySettings,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> ConcurrencySettings:
    config = session.get(Config, 1) or Config(id=1)
    config.max_concurrent_vms = settings_payload.max_concurrent_vms
    config.per_user_vm_limit = settings_payload.per_user_vm_limit
    session.add(config)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_concurrency",
        target_id="global",
        detail=(
            f"max_concurrent_vms={settings_payload.max_concurrent_vms} "
            f"per_user_vm_limit={settings_payload.per_user_vm_limit}"
        ),
    )
    session.commit()
    return settings_payload


@router.post(
    "/settings/idle-timeout",
    response_model=IdleTimeoutSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_idle_timeout(
    settings_payload: IdleTimeoutSettings,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> IdleTimeoutSettings:
    config = session.get(Config, 1) or Config(id=1)
    config.idle_timeout_minutes = settings_payload.idle_timeout_minutes
    session.add(config)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_idle_timeout",
        target_id="global",
        detail=f"idle_timeout_minutes={settings_payload.idle_timeout_minutes}",
    )
    session.commit()
    return settings_payload


@router.get(
    "/settings/storage",
    response_model=StorageSettingsRead,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_storage_settings(session: Session = Depends(get_session)) -> StorageSettingsRead:
    cfg = session.get(Config, 1)
    return _storage_settings_view(cfg)


@router.patch(
    "/settings/storage",
    response_model=StorageSettingsRead,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_storage_settings(
    payload: StorageSettingsUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> StorageSettingsRead:
    cfg = _get_or_create_config(session)
    if payload.clear_overrides:
        cfg.storage_root_override = None
        cfg.kube_image_pvc_override = None
        cfg.kube_vm_storage_class_override = None
    else:
        storage_root = _to_str(payload.storage_root)
        kube_image_pvc = _to_str(payload.kube_image_pvc)
        if not storage_root:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="storage_root is required.")
        if not kube_image_pvc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="kube_image_pvc is required.")
        cfg.storage_root_override = storage_root
        cfg.kube_image_pvc_override = kube_image_pvc
        if payload.kube_vm_storage_class is not None:
            cfg.kube_vm_storage_class_override = _to_str(payload.kube_vm_storage_class)
    session.add(cfg)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_storage",
        target_id="global",
        detail=(
            "clear_overrides=true"
            if payload.clear_overrides
            else (
                f"storage_root={_to_str(cfg.storage_root_override)} "
                f"kube_image_pvc={_to_str(cfg.kube_image_pvc_override)} "
                f"kube_vm_storage_class={_to_str(cfg.kube_vm_storage_class_override)}"
            )
        ),
    )
    session.commit()
    session.refresh(cfg)

    storage_root, kube_image_pvc, kube_vm_storage_class, _ = _effective_storage_values(cfg)
    _apply_runtime_storage_settings(
        storage_root=storage_root,
        kube_image_pvc=kube_image_pvc,
        kube_vm_storage_class=kube_vm_storage_class,
    )
    return _storage_settings_view(cfg)


@router.get(
    "/settings/runtime",
    response_model=RuntimeSettingsRead,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_runtime_settings(session: Session = Depends(get_session)) -> RuntimeSettingsRead:
    cfg = session.get(Config, 1)
    return _runtime_settings_view(cfg)


@router.get(
    "/settings/runtime/orchestration-parity",
    response_model=OrchestrationParityReport,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_runtime_orchestration_parity(session: Session = Depends(get_session)) -> OrchestrationParityReport:
    return _runtime_orchestration_parity_report(session)


@router.patch(
    "/settings/runtime",
    response_model=RuntimeSettingsRead,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_runtime_storage_settings(
    payload: StorageSettingsUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> RuntimeSettingsRead:
    # Backward-compatible path used by older frontend builds.
    update_storage_settings(payload=payload, session=session, actor=actor)
    return get_runtime_settings(session=session)


@router.post(
    "/settings/site/background",
    response_model=SiteBackgroundAsset,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def upload_site_background(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> SiteBackgroundAsset:
    original_name = Path(str(file.filename or "")).name
    suffix = Path(original_name).suffix.lower()
    if not suffix and file.content_type:
        guessed = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
            "image/svg+xml": ".svg",
        }.get(str(file.content_type).lower())
        suffix = guessed or ""
    if suffix not in SITE_BACKGROUND_ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported background image type (allowed: png, jpg, jpeg, webp, gif, svg)",
        )

    filename = f"site-bg-{uuid4().hex[:24]}{suffix}"
    target = _site_assets_dir() / filename
    total = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > SITE_BACKGROUND_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="background image exceeds 20 MB limit",
                    )
                out.write(chunk)
    except HTTPException:
        if target.exists():
            target.unlink()
        raise
    except Exception as exc:
        if target.exists():
            target.unlink()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"upload failed: {exc}") from exc
    finally:
        try:
            file.file.close()
        except Exception:
            pass

    if total <= 0:
        if target.exists():
            target.unlink()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploaded file is empty")

    cfg = _get_or_create_config(session)
    old_path = str(cfg.theme_bg_image or "").strip()
    public_path = _site_background_public_path(filename)
    cfg.theme_bg_image = public_path
    session.add(cfg)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_site_background",
        target_id="global",
        detail=f"filename={filename} size_bytes={total}",
    )
    session.commit()
    if old_path and old_path != public_path:
        _delete_local_site_background(old_path)
    return SiteBackgroundAsset(theme_bg_image=public_path, filename=filename, size_bytes=total)


@router.get(
    "/settings/site",
    response_model=SiteSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
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


@router.patch(
    "/settings/site",
    response_model=SiteSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_site_settings(
    payload: SiteSettings,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> SiteSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    old_bg_image = str(cfg.theme_bg_image or "").strip()
    cfg.site_title = payload.site_title
    cfg.site_tagline = payload.site_tagline
    cfg.theme_bg_color = payload.theme_bg_color
    cfg.theme_text_color = payload.theme_text_color
    cfg.theme_button_color = payload.theme_button_color
    cfg.theme_button_text_color = payload.theme_button_text_color
    theme_bg_image = str(payload.theme_bg_image or "").strip()
    if theme_bg_image and not theme_bg_image.startswith(SITE_BACKGROUND_PUBLIC_PREFIX):
        theme_bg_image = ""
    cfg.theme_bg_image = theme_bg_image
    cfg.theme_bg_image_overlay_opacity = payload.theme_bg_image_overlay_opacity
    cfg.theme_contrast_body = payload.theme_contrast_body
    cfg.theme_contrast_button = payload.theme_contrast_button
    cfg.theme_contrast_tile = payload.theme_contrast_tile
    cfg.theme_contrast_tile_border = payload.theme_contrast_tile_border
    cfg.theme_font_family = payload.theme_font_family
    cfg.theme_font_size_base = payload.theme_font_size_base
    cfg.theme_font_size_h1 = payload.theme_font_size_h1
    cfg.theme_font_size_h2 = payload.theme_font_size_h2
    cfg.theme_tile_bg = payload.theme_tile_bg
    cfg.theme_tile_border = payload.theme_tile_border
    cfg.theme_tile_opacity = payload.theme_tile_opacity
    cfg.theme_tile_border_opacity = payload.theme_tile_border_opacity
    session.add(cfg)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_site",
        target_id="global",
        detail=f"site_title={cfg.site_title}",
    )
    session.commit()
    session.refresh(cfg)
    if old_bg_image and old_bg_image != cfg.theme_bg_image:
        _delete_local_site_background(old_bg_image)
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


def _normalize_sso_role_mappings(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for claim_value, role_value in raw.items():
        claim_key = str(claim_value or "").strip().lower()
        if not claim_key:
            continue
        normalized_role = normalize_requested_role(str(role_value or "").strip())
        normalized[claim_key] = normalized_role
    return normalized


def _read_sso_role_mappings(cfg: Config) -> dict[str, str]:
    raw = str(getattr(cfg, "sso_role_mappings_json", "") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    normalized: dict[str, str] = {}
    for claim_value, role_value in parsed.items():
        claim_key = str(claim_value or "").strip().lower()
        role_key = str(role_value or "").strip().lower()
        if not claim_key or not role_key:
            continue
        try:
            normalized[claim_key] = normalize_requested_role(role_key)
        except ValueError:
            continue
    return normalized


@router.get(
    "/settings/sso",
    response_model=SSOSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_sso_settings(session: Session = Depends(get_session)) -> SSOSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    role_mappings = _read_sso_role_mappings(cfg)
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret_configured=secret_is_configured(cfg.sso_client_secret),
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
        sso_role_claim=str(cfg.sso_role_claim or "groups").strip() or "groups",
        sso_default_role=str(cfg.sso_default_role or Role.USER).strip() or Role.USER,
        sso_role_mappings=role_mappings,
        sso_auto_create_users=bool(cfg.sso_auto_create_users),
        sso_sync_roles_on_login=bool(cfg.sso_sync_roles_on_login),
    )


@router.patch(
    "/settings/sso",
    response_model=SSOSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_sso_settings(
    payload: SSOSettingsUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> SSOSettings:
    try:
        default_role = normalize_requested_role(payload.sso_default_role)
        role_mappings = _normalize_sso_role_mappings(payload.sso_role_mappings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    role_claim = str(payload.sso_role_claim or "").strip() or "groups"
    cfg = session.get(Config, 1) or Config(id=1)
    cfg.sso_enabled = payload.sso_enabled
    cfg.sso_provider = str(payload.sso_provider or "").strip()
    cfg.sso_client_id = str(payload.sso_client_id or "").strip()
    if "sso_client_secret" in payload.model_fields_set:
        cfg.sso_client_secret = encrypt_secret(payload.sso_client_secret)
    cfg.sso_authorize_url = str(payload.sso_authorize_url or "").strip()
    cfg.sso_token_url = str(payload.sso_token_url or "").strip()
    cfg.sso_userinfo_url = str(payload.sso_userinfo_url or "").strip()
    cfg.sso_redirect_url = str(payload.sso_redirect_url or "").strip()
    cfg.sso_role_claim = role_claim
    cfg.sso_default_role = default_role
    cfg.sso_role_mappings_json = json.dumps(role_mappings, sort_keys=True, separators=(",", ":"))
    cfg.sso_auto_create_users = bool(payload.sso_auto_create_users)
    cfg.sso_sync_roles_on_login = bool(payload.sso_sync_roles_on_login)
    session.add(cfg)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_sso",
        target_id="global",
        detail=(
            f"sso_enabled={cfg.sso_enabled} provider={cfg.sso_provider} "
            f"default_role={cfg.sso_default_role} mappings={len(role_mappings)}"
        ),
    )
    session.commit()
    session.refresh(cfg)
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret_configured=secret_is_configured(cfg.sso_client_secret),
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
        sso_role_claim=str(cfg.sso_role_claim or "groups").strip() or "groups",
        sso_default_role=str(cfg.sso_default_role or Role.USER).strip() or Role.USER,
        sso_role_mappings=_read_sso_role_mappings(cfg),
        sso_auto_create_users=bool(cfg.sso_auto_create_users),
        sso_sync_roles_on_login=bool(cfg.sso_sync_roles_on_login),
    )


@router.get(
    "/settings/ldap",
    response_model=LDAPSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_READ))],
)
def get_ldap_settings(session: Session = Depends(get_session)) -> LDAPSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return LDAPSettings(
        ldap_enabled=cfg.ldap_enabled,
        ldap_server_uri=cfg.ldap_server_uri,
        ldap_bind_dn=cfg.ldap_bind_dn,
        ldap_bind_password_configured=secret_is_configured(cfg.ldap_bind_password),
        ldap_user_base_dn=cfg.ldap_user_base_dn,
        ldap_user_filter=cfg.ldap_user_filter,
        ldap_start_tls=cfg.ldap_start_tls,
        ldap_insecure_skip_verify=cfg.ldap_insecure_skip_verify,
        ldap_timeout_seconds=max(3, min(60, int(cfg.ldap_timeout_seconds or 10))),
        ldap_auto_create_users=cfg.ldap_auto_create_users,
    )


@router.patch(
    "/settings/ldap",
    response_model=LDAPSettings,
    dependencies=[Depends(require_permission(Permission.SETTINGS_WRITE))],
)
def update_ldap_settings(
    payload: LDAPSettingsUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
) -> LDAPSettings:
    user_filter = str(payload.ldap_user_filter or "").strip() or "(uid={username})"
    if "{username}" not in user_filter:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ldap_user_filter must include {username} placeholder.",
        )
    cfg = session.get(Config, 1) or Config(id=1)
    cfg.ldap_enabled = bool(payload.ldap_enabled)
    cfg.ldap_server_uri = str(payload.ldap_server_uri or "").strip()
    cfg.ldap_bind_dn = str(payload.ldap_bind_dn or "").strip()
    if "ldap_bind_password" in payload.model_fields_set:
        cfg.ldap_bind_password = encrypt_secret(payload.ldap_bind_password)
    cfg.ldap_user_base_dn = str(payload.ldap_user_base_dn or "").strip()
    cfg.ldap_user_filter = user_filter
    cfg.ldap_start_tls = bool(payload.ldap_start_tls)
    cfg.ldap_insecure_skip_verify = bool(payload.ldap_insecure_skip_verify)
    cfg.ldap_timeout_seconds = max(3, min(60, int(payload.ldap_timeout_seconds or 10)))
    cfg.ldap_auto_create_users = bool(payload.ldap_auto_create_users)
    session.add(cfg)
    _record_admin_audit_event(
        session,
        actor=actor.username,
        action="update",
        target_type="settings_ldap",
        target_id="global",
        detail=f"ldap_enabled={cfg.ldap_enabled} ldap_server_uri={cfg.ldap_server_uri}",
    )
    session.commit()
    session.refresh(cfg)
    return LDAPSettings(
        ldap_enabled=cfg.ldap_enabled,
        ldap_server_uri=cfg.ldap_server_uri,
        ldap_bind_dn=cfg.ldap_bind_dn,
        ldap_bind_password_configured=secret_is_configured(cfg.ldap_bind_password),
        ldap_user_base_dn=cfg.ldap_user_base_dn,
        ldap_user_filter=cfg.ldap_user_filter,
        ldap_start_tls=cfg.ldap_start_tls,
        ldap_insecure_skip_verify=cfg.ldap_insecure_skip_verify,
        ldap_timeout_seconds=max(3, min(60, int(cfg.ldap_timeout_seconds or 10))),
        ldap_auto_create_users=cfg.ldap_auto_create_users,
    )


@router.get(
    "/pods",
    response_model=list[VMInstance],
    dependencies=[Depends(require_permission(Permission.OPERATIONS_READ))],
)
def list_running_pods(session: Session = Depends(get_session)) -> list[VMInstance]:
    instances = session.exec(select(Instance)).all()
    return [
        VMInstance(
            id=record.id,
            template_id=record.template_id,
            owner=record.owner,
            tenant=normalize_tenant(getattr(record, "tenant", None), default="default"),
            namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
            cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
            status=record.status,
            started_at=record.started_at,
            last_active_at=record.last_active_at,
            console_url=record.console_url,
        )
        for record in instances
    ]


@router.post(
    "/pods/{instance_id}/stop",
    response_model=VMInstance,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def stop_pod(instance_id: str, session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.stop_pod(instance_id, record.owner)
    record.status = "stopped"
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        tenant=normalize_tenant(getattr(record, "tenant", None), default="default"),
        namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        status=record.status,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.delete(
    "/pods/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.OPERATIONS_WRITE))],
)
def delete_pod(instance_id: str, session: Session = Depends(get_session)) -> None:
    record = session.get(Instance, instance_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.delete_pod(instance_id, record.owner, disk_pvc=record.disk_pvc)
    session.delete(record)
    session.commit()
