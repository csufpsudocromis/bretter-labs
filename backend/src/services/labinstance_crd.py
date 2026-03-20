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


def _api_exception_message(exc: ApiException) -> str:
    body = str(getattr(exc, "body", "") or "")
    reason = str(getattr(exc, "reason", "") or "")
    return f"{body} {reason}".strip().lower()


def _is_legacy_desired_state_schema_error(exc: ApiException) -> bool:
    if int(getattr(exc, "status", 0) or 0) not in {400, 422}:
        return False
    message = _api_exception_message(exc)
    return "spec.lifecycle" in message and "unknown field" in message


def _as_legacy_desired_state_spec(spec: dict[str, Any]) -> dict[str, Any]:
    copied = dict(spec)
    lifecycle = copied.pop("lifecycle", {})
    if isinstance(lifecycle, dict):
        desired_state = str(lifecycle.get("desiredState") or "").strip().lower()
        if desired_state:
            copied["desiredState"] = desired_state
    return copied


def _fallback_body_for_legacy_desired_state(body: dict[str, Any]) -> dict[str, Any]:
    updated = dict(body)
    updated_spec = _as_legacy_desired_state_spec(dict(body.get("spec", {})))
    updated_status = dict(body.get("status", {}))
    desired_state = str(updated_spec.get("desiredState") or "running").strip().lower() or "running"
    conditions = updated_status.get("conditions")
    if isinstance(conditions, list):
        rewritten: list[dict[str, Any]] = []
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            copy_cond = dict(condition)
            if copy_cond.get("type") == "DesiredStateAccepted":
                copy_cond["message"] = f"API accepted desired state {desired_state}."
            rewritten.append(copy_cond)
        updated_status["conditions"] = rewritten
    updated["spec"] = updated_spec
    updated["status"] = updated_status
    return updated


def _labinstance_body(
    *,
    instance_id: str,
    owner: str,
    template: Template,
    image: Image,
    namespace: str,
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

    desired_state_value = str(desired_state or "running").strip().lower() or "running"

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
        "lifecycle": {"desiredState": desired_state_value},
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
                message=f"API accepted desired state {desired_state_value}.",
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
        "namespace": namespace,
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
    namespace: str | None = None,
    desired_state: str = "running",
    status_phase: str | None = None,
    status_message: str | None = None,
) -> None:
    body = _labinstance_body(
        instance_id=instance_id,
        owner=owner,
        template=template,
        image=image,
        namespace=str(namespace or settings.kube_namespace),
        desired_state=desired_state,
        status_phase=status_phase,
        status_message=status_message,
    )
    custom = _custom_objects()
    target_namespace = str(namespace or settings.kube_namespace)
    create_body = body
    try:
        custom.create_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=target_namespace,
            plural=_plural(),
            body=create_body,
        )
        return
    except ApiException as exc:
        if _is_legacy_desired_state_schema_error(exc):
            create_body = _fallback_body_for_legacy_desired_state(body)
            try:
                custom.create_namespaced_custom_object(
                    group=_group(),
                    version=_version(),
                    namespace=target_namespace,
                    plural=_plural(),
                    body=create_body,
                )
                logger.info(
                    "Detected legacy LabInstance desiredState schema; using spec.desiredState compatibility mode."
                )
                return
            except ApiException as legacy_exc:
                if legacy_exc.status != 409:
                    raise
        elif exc.status != 409:
            raise
        else:
            create_body = body

    custom.patch_namespaced_custom_object(
        group=_group(),
        version=_version(),
        namespace=target_namespace,
        plural=_plural(),
        name=instance_id,
        body={"spec": create_body["spec"], "metadata": {"labels": create_body["metadata"].get("labels", {})}},
    )
    custom.patch_namespaced_custom_object_status(
        group=_group(),
        version=_version(),
        namespace=target_namespace,
        plural=_plural(),
        name=instance_id,
        body={"status": create_body["status"]},
    )


def patch_vm_labinstance_desired_state(
    instance_id: str,
    desired_state: str,
    *,
    namespace: str | None = None,
) -> None:
    custom = _custom_objects()
    desired_state_value = str(desired_state or "running").strip().lower() or "running"
    target_namespace = str(namespace or settings.kube_namespace)
    try:
        custom.patch_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=target_namespace,
            plural=_plural(),
            name=instance_id,
            body={"spec": {"lifecycle": {"desiredState": desired_state_value}}},
        )
    except ApiException as exc:
        if not _is_legacy_desired_state_schema_error(exc):
            raise
        custom.patch_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=target_namespace,
            plural=_plural(),
            name=instance_id,
            body={"spec": {"desiredState": desired_state_value}},
        )


def delete_vm_labinstance(instance_id: str, *, namespace: str | None = None, missing_ok: bool = True) -> None:
    custom = _custom_objects()
    try:
        custom.delete_namespaced_custom_object(
            group=_group(),
            version=_version(),
            namespace=str(namespace or settings.kube_namespace),
            plural=_plural(),
            name=instance_id,
        )
    except ApiException as exc:
        if missing_ok and exc.status == 404:
            return
        raise


def delete_vm_labinstance_best_effort(instance_id: str, *, namespace: str | None = None) -> None:
    try:
        delete_vm_labinstance(instance_id, namespace=namespace, missing_ok=True)
    except Exception:
        logger.warning("Failed to delete LabInstance CRD for %s", instance_id, exc_info=True)
