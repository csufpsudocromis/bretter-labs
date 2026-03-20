from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException

from ..config import settings
from ..console_providers import normalize_vm_console_provider
from ..network_modes import normalize_vm_network_mode
from ..tables import Image, Template

logger = logging.getLogger(__name__)

_ALLOWED_BACKENDS = {"db", "dual", "crd"}


def normalized_orchestration_backend() -> str:
    raw = str(getattr(settings, "orchestration_backend", "db") or "db").strip().lower()
    return raw if raw in _ALLOWED_BACKENDS else "db"


def vm_orchestration_uses_legacy_path() -> bool:
    return normalized_orchestration_backend() in {"db", "dual"}


def vm_orchestration_writes_crd() -> bool:
    return normalized_orchestration_backend() in {"dual", "crd"}


def _load_kube_config_once() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _custom_objects() -> client.CustomObjectsApi:
    _load_kube_config_once()
    return client.CustomObjectsApi()


def _group() -> str:
    return str(getattr(settings, "labinstance_crd_group", "labs.bretter.io") or "labs.bretter.io").strip()


def _version() -> str:
    return str(getattr(settings, "labinstance_crd_version", "v1alpha1") or "v1alpha1").strip()


def _plural() -> str:
    return str(getattr(settings, "labinstance_crd_plural", "labinstances") or "labinstances").strip()


def _finalizer() -> str:
    return str(getattr(settings, "labinstance_crd_finalizer", "labs.bretter.io/finalizer") or "").strip()


def _ts() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _phase_for_status(raw_status: str) -> str:
    normalized = str(raw_status or "").strip().lower()
    if normalized == "running":
        return "Running"
    if normalized == "pending":
        return "Pending"
    if normalized == "stopped":
        return "Stopped"
    if normalized == "completed":
        return "Completed"
    if normalized == "failed":
        return "Failed"
    return "Unknown"


def _condition(
    *,
    condition_type: str,
    condition_status: str,
    reason: str,
    message: str,
) -> dict[str, str]:
    return {
        "type": condition_type,
        "status": condition_status,
        "reason": reason,
        "message": message,
        "lastTransitionTime": _ts(),
    }


def _labinstance_body(
    *,
    instance_id: str,
    owner: str,
    template: Template,
    image: Image,
    desired_state: str,
    status_phase: str | None,
    status_message: str | None,
) -> dict[str, Any]:
    provider = normalize_vm_console_provider(getattr(template, "console_provider", "spice"))
    if provider == "guacamole":
        provider_value = "guacamole_vnc"
    elif provider == "guacamole_rdp":
        provider_value = "guacamole_rdp"
    else:
        provider_value = "spice"

    spec: dict[str, Any] = {
        "owner": {"username": owner},
        "templateRef": {"name": str(template.id)},
        "workload": {"kind": "vm", "consoleProvider": provider_value},
        "resources": {
            "cpuMillicores": max(100, int(getattr(template, "cpu_cores", 1) or 1) * 1000),
            "memoryMiB": max(128, int(getattr(template, "ram_mb", 512) or 512)),
        },
        "network": {"mode": normalize_vm_network_mode(getattr(template, "network_mode", "bridge"))},
        "idleTimeoutMinutes": max(1, int(getattr(template, "idle_timeout_minutes", 30) or 30)),
        "lifecycle": {"desiredState": str(desired_state or "running").strip().lower() or "running"},
        "image": {
            "id": str(image.id),
            "filename": str(image.filename),
            "sourcePvc": str(getattr(image, "source_pvc", "") or ""),
            "osType": str(getattr(template, "os_type", "windows") or "windows"),
        },
    }

    status_phase_value = status_phase or _phase_for_status("pending")
    message = str(status_message or "Queued for operator reconciliation.").strip()
    status = {
        "phase": status_phase_value,
        "conditions": [
            _condition(
                condition_type="DesiredStateAccepted",
                condition_status="True",
                reason="ApiAccepted",
                message=f"API accepted desired state {spec['lifecycle']['desiredState']}.",
            ),
            _condition(
                condition_type="ReconcileReady",
                condition_status="False",
                reason="AwaitingController",
                message=message,
            ),
        ],
    }

    metadata: dict[str, Any] = {
        "name": instance_id,
        "namespace": settings.kube_namespace,
        "labels": {
            "labs.bretter.io/owner": owner,
            "labs.bretter.io/template-id": str(template.id),
            "labs.bretter.io/workload-kind": "vm",
        },
    }
    finalizer = _finalizer()
    if finalizer:
        metadata["finalizers"] = [finalizer]

    return {
        "apiVersion": f"{_group()}/{_version()}",
        "kind": "LabInstance",
        "metadata": metadata,
        "spec": spec,
        "status": status,
    }


def upsert_vm_labinstance(
    *,
    instance_id: str,
    owner: str,
    template: Template,
    image: Image,
    desired_state: str = "running",
    status_phase: str | None = None,
    status_message: str | None = None,
) -> None:
    body = _labinstance_body(
        instance_id=instance_id,
        owner=owner,
        template=template,
        image=image,
        desired_state=desired_state,
        status_phase=status_phase,
        status_message=status_message,
    )
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
        name=instance_id,
        body={"spec": body["spec"], "metadata": {"labels": body["metadata"].get("labels", {})}},
    )
    custom.patch_namespaced_custom_object_status(
        group=_group(),
        version=_version(),
        namespace=namespace,
        plural=_plural(),
        name=instance_id,
        body={"status": body["status"]},
    )


def patch_vm_labinstance_desired_state(instance_id: str, desired_state: str) -> None:
    custom = _custom_objects()
    custom.patch_namespaced_custom_object(
        group=_group(),
        version=_version(),
        namespace=settings.kube_namespace,
        plural=_plural(),
        name=instance_id,
        body={"spec": {"lifecycle": {"desiredState": str(desired_state or "running").strip().lower()}}},
    )


def delete_vm_labinstance(instance_id: str, *, missing_ok: bool = True) -> None:
    custom = _custom_objects()
    try:
        custom.delete_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=settings.kube_namespace,
            plural=_plural(),
            name=instance_id,
        )
    except ApiException as exc:
        if missing_ok and exc.status == 404:
            return
        raise


def delete_vm_labinstance_best_effort(instance_id: str) -> None:
    try:
        delete_vm_labinstance(instance_id, missing_ok=True)
    except Exception:
        logger.warning("Failed to delete LabInstance CRD for %s", instance_id, exc_info=True)
