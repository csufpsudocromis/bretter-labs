"""
Kubernetes integration helpers.

Creates/stops/deletes VM pods, applies egress-only NetworkPolicies, and generates console URLs.
"""

import logging
import math
import json
import hashlib
import ipaddress
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from kubernetes import client, config
from kubernetes.stream import stream
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..config import settings
from ..console_providers import normalize_vm_console_provider
from ..network_modes import normalize_vm_network_mode
from ..tables import Config, ContainerInstance, ContainerTemplate, Image, Instance, Template
from ..time_utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class PodRequest:
    instance_id: str
    template_id: str
    image_path: str
    image_source_pvc: Optional[str]
    os_type: str
    cpu_cores: int
    ram_mb: int
    owner: str
    network_mode: str = "bridge"
    namespace: str | None = None
    instance_disk_pvc: Optional[str] = None
    console_provider: str = "spice"
    spice_password: Optional[str] = None
    rdp_default_username: Optional[str] = None
    rdp_default_password: Optional[str] = None
    installer_iso_filename: Optional[str] = None
    boot_order: Optional[str] = None


@dataclass
class ContainerPodRequest:
    instance_id: str
    owner: str
    image_ref: str
    cpu_millicores: int
    memory_mb: int
    namespace: str | None = None
    container_port: int = 80
    healthcheck_protocol: str = "tcp"
    healthcheck_path: str = "/"
    readiness_http_status: int = 200
    readiness_success_path: Optional[str] = None
    startup_timeout_seconds: int = 300
    dependency_checks: list[object] | None = None
    expose_strategy: str = "nodeport"
    network_mode: str = "bridge"
    run_as_non_root: bool = False
    read_only_root_filesystem: bool = False
    command: Optional[str] = None
    args: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass
class PodStatus:
    instance_id: str
    phase: str
    node: Optional[str] = None
    message: Optional[str] = None
    reason: Optional[str] = None
    waiting_reason: Optional[str] = None
    waiting_message: Optional[str] = None
    ready: bool = False
    console_endpoint: Optional[str] = None
    disk_pvc: Optional[str] = None


class KubernetesService:
    def __init__(
        self,
        *,
        core_api: client.CoreV1Api | None = None,
        networking_api: client.NetworkingV1Api | None = None,
        namespace_override: str | None = None,
    ) -> None:
        self._core = core_api
        self._networking = networking_api
        self._namespace_override = str(namespace_override or "").strip()
        self._cross_namespace_clone_support_cache: dict[tuple[str, str], bool] = {}

    def _client(self):
        if self._core is None:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            self._core = client.CoreV1Api()
            self._networking = client.NetworkingV1Api()
        return self._core

    def _networking_client(self):
        self._client()
        return self._networking

    def _namespace(self, namespace: str | None = None) -> str:
        resolved = str(namespace or "").strip()
        if resolved:
            return resolved
        if self._namespace_override:
            return self._namespace_override
        return str(settings.kube_namespace or "labs").strip() or "labs"

    def _control_namespace(self) -> str:
        return str(settings.kube_namespace or "labs").strip() or "labs"

    def _safe_owner(self, owner: str) -> str:
        safe_owner = re.sub(r"[^a-z0-9-]+", "-", (owner or "").lower()).strip("-")
        return safe_owner or "user"

    def _pod_name(self, req: PodRequest) -> str:
        return f"vm-{self._safe_owner(req.owner)}-{req.instance_id[:8]}"

    def _container_pod_name(self, instance_id: str, owner: str) -> str:
        return f"ct-{self._safe_owner(owner)}-{instance_id[:8]}"

    def container_pod_name(self, instance_id: str, owner: str) -> str:
        return self._container_pod_name(instance_id, owner)

    def _container_service_name(self, instance_id: str) -> str:
        return f"ctsvc-{instance_id[:8]}"

    def _container_ingress_name(self, instance_id: str) -> str:
        return f"cting-{instance_id[:8]}"

    def _container_netpol_name(self, instance_id: str) -> str:
        return f"ctnp-{instance_id[:8]}"

    def _instance_disk_pvc_name(self, instance_id: str, owner: str) -> str:
        safe_owner = self._safe_owner(owner)
        return f"vm-disk-{safe_owner[:20]}-{instance_id[:8]}"

    def _instance_service_name(self, instance_id: str) -> str:
        return f"svc-{instance_id[:8]}"

    def _instance_netpol_name(self, instance_id: str, owner: str) -> str:
        return f"{self._find_pod_name(instance_id, owner)}-egress-only"

    @staticmethod
    def _admin_helper_pod_name() -> str:
        return f"backend-admin-{uuid4().hex[:8]}"

    def _run_admin_helper_pod(
        self,
        *,
        command: list[str],
        timeout_seconds: int = 180,
        env: dict[str, str] | None = None,
        mount_signature_key: bool = False,
    ) -> tuple[int, str]:
        core = self._client()
        namespace = self._namespace()
        pod_name = self._admin_helper_pod_name()
        helper_image = (
            str(getattr(settings, "backend_admin_image", "") or "").strip()
            or str(getattr(settings, "backend_image", "") or "").strip()
        )
        if not helper_image:
            helper_image = "ghcr.io/csufpsudocromis/bretter-backend:v0.3.1"
        volumes: list[client.V1Volume] = []
        volume_mounts: list[client.V1VolumeMount] = []
        if mount_signature_key and str(settings.container_signature_key_secret_name or "").strip():
            volumes.append(
                client.V1Volume(
                    name="container-signature-key",
                    secret=client.V1SecretVolumeSource(
                        secret_name=str(settings.container_signature_key_secret_name).strip(),
                        optional=True,
                    ),
                )
            )
            volume_mounts.append(
                client.V1VolumeMount(name="container-signature-key", mount_path="/etc/bretter-signing", read_only=True)
            )

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=namespace,
                labels={"app.kubernetes.io/part-of": "bretter-labs", "job-type": "backend-admin-tool"},
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                image_pull_secrets=(
                    [client.V1LocalObjectReference(name=settings.image_pull_secret)]
                    if settings.image_pull_secret
                    else None
                ),
                containers=[
                    client.V1Container(
                        name="worker",
                        image=helper_image,
                        image_pull_policy="IfNotPresent",
                        command=command,
                        env=[client.V1EnvVar(name=key, value=value) for key, value in sorted((env or {}).items())],
                        volume_mounts=volume_mounts or None,
                    )
                ],
                volumes=volumes or None,
            ),
        )

        logs = ""
        try:
            core.create_namespaced_pod(namespace=namespace, body=pod)
            deadline = time.time() + max(30, int(timeout_seconds or 180))
            last_phase = ""
            while time.time() < deadline:
                snapshot = core.read_namespaced_pod(name=pod_name, namespace=namespace)
                last_phase = str(snapshot.status.phase or "").strip().lower()
                if last_phase in {"succeeded", "failed"}:
                    break
                time.sleep(2)
            else:
                raise TimeoutError(f"admin helper pod timed out after {timeout_seconds}s")

            try:
                logs = core.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container="worker",
                    tail_lines=2000,
                )
            except ApiException:
                logs = ""

            status_list = snapshot.status.container_statuses or []
            if status_list and status_list[0].state and status_list[0].state.terminated:
                code = int(status_list[0].state.terminated.exit_code or 0)
                return code, logs
            if last_phase == "succeeded":
                return 0, logs
            return 1, logs
        finally:
            try:
                core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    grace_period_seconds=0,
                    propagation_policy="Background",
                )
            except Exception:
                pass

    def verify_container_image_signature(self, image_ref: str) -> str | None:
        if not settings.container_signature_verification_enabled:
            return None
        key_ref = (settings.container_signature_key_ref or "").strip()
        if key_ref:
            cmd = ["cosign", "verify", "--key", key_ref, image_ref]
        else:
            cmd = [
                "cosign",
                "verify",
                "--certificate-identity-regexp",
                ".*",
                "--certificate-oidc-issuer-regexp",
                ".*",
                image_ref,
            ]
        mount_signature_key = key_ref.startswith("/etc/bretter-signing/")
        try:
            code, output = self._run_admin_helper_pod(
                command=cmd,
                timeout_seconds=120,
                mount_signature_key=mount_signature_key,
            )
        except TimeoutError as exc:
            raise RuntimeError("image signature verification timed out") from exc
        if code == 0:
            return None
        detail = str(output or "signature verification failed").strip()
        if "no signatures found" in detail.lower():
            return "Image has no signatures; continuing with warning-only policy."
        raise RuntimeError(detail[:500])

    def _pool_pvc_name(self, template_id: str) -> str:
        return f"pool-{template_id[:8]}-{uuid4().hex[:6]}"

    def _read_source_pvc_with_fallback(
        self,
        *,
        image_source_pvc: str,
        runtime_namespace: str,
    ) -> tuple[client.V1PersistentVolumeClaim, str]:
        core = self._client()
        source_name = str(image_source_pvc or "").strip()
        if not source_name:
            raise RuntimeError("image source PVC is required for clone-based VM launch")
        primary_namespace = self._control_namespace()
        candidates = [runtime_namespace]
        if runtime_namespace != primary_namespace:
            candidates.append(primary_namespace)
        last_exc: ApiException | None = None
        for candidate_ns in candidates:
            try:
                source = core.read_namespaced_persistent_volume_claim(name=source_name, namespace=candidate_ns)
                return source, candidate_ns
            except ApiException as exc:
                if exc.status != 404:
                    raise
                last_exc = exc
        if last_exc is not None:
            raise RuntimeError(
                f"source PVC {source_name} not found in runtime namespace {runtime_namespace} "
                f"or control namespace {primary_namespace}"
            ) from last_exc
        raise RuntimeError(f"source PVC {source_name} not found")

    @staticmethod
    def _clone_data_source_kwargs(
        *,
        source_name: str,
        source_namespace: str,
        target_namespace: str,
    ) -> dict[str, object]:
        if source_namespace == target_namespace:
            return {
                "data_source": client.V1TypedLocalObjectReference(
                    api_group="",
                    kind="PersistentVolumeClaim",
                    name=source_name,
                )
            }
        return {
            "data_source_ref": client.V1TypedObjectReference(
                api_group="",
                kind="PersistentVolumeClaim",
                name=source_name,
                namespace=source_namespace,
            )
        }

    def supports_cross_namespace_pvc_clone(
        self,
        *,
        source_pvc_name: str,
        source_namespace: str,
        target_namespace: str,
        storage_request: object | None = None,
        storage_class_name: str | None = None,
    ) -> bool:
        source_ns = str(source_namespace or "").strip()
        target_ns = str(target_namespace or "").strip()
        source_name = str(source_pvc_name or "").strip()
        if not source_name or not source_ns or not target_ns:
            return False
        if source_ns == target_ns:
            return True

        cache_key = (source_ns, target_ns)
        cached = self._cross_namespace_clone_support_cache.get(cache_key)
        if cached is not None:
            return cached

        core = self._client()
        probe_name = f"blabs-xns-clone-probe-{uuid4().hex[:8]}"
        request_size = storage_request
        sc_name = str(storage_class_name or "").strip() or None
        if request_size is None or not sc_name:
            try:
                source = core.read_namespaced_persistent_volume_claim(name=source_name, namespace=source_ns)
                if request_size is None and source.spec and source.spec.resources and source.spec.resources.requests:
                    request_size = source.spec.resources.requests.get("storage")
                if not sc_name:
                    sc_name = str(source.spec.storage_class_name or "").strip() or None
            except ApiException:
                self._cross_namespace_clone_support_cache[cache_key] = False
                return False

        if request_size is None:
            self._cross_namespace_clone_support_cache[cache_key] = False
            return False

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(name=probe_name),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=sc_name,
                resources=client.V1ResourceRequirements(requests={"storage": request_size}),
                data_source_ref=client.V1TypedObjectReference(
                    api_group="",
                    kind="PersistentVolumeClaim",
                    name=source_name,
                    namespace=source_ns,
                ),
            ),
        )
        supported = False
        try:
            probe = core.create_namespaced_persistent_volume_claim(namespace=target_ns, body=body, dry_run="All")
            data_source_ref = getattr(getattr(probe, "spec", None), "data_source_ref", None)
            supported = (
                str(getattr(data_source_ref, "kind", "") or "").strip() == "PersistentVolumeClaim"
                and str(getattr(data_source_ref, "name", "") or "").strip() == source_name
                and str(getattr(data_source_ref, "namespace", "") or "").strip() == source_ns
            )
        except ApiException:
            supported = False

        self._cross_namespace_clone_support_cache[cache_key] = supported
        return supported

    def resolve_vm_source_pvc(
        self,
        *,
        image_source_pvc: str,
        runtime_namespace: str | None = None,
    ) -> tuple[client.V1PersistentVolumeClaim, str]:
        namespace = self._namespace(runtime_namespace)
        return self._read_source_pvc_with_fallback(image_source_pvc=image_source_pvc, runtime_namespace=namespace)

    def _ensure_instance_disk_pvc(self, req: PodRequest) -> str:
        core = self._client()
        namespace = self._namespace(req.namespace)
        if req.instance_disk_pvc:
            existing = core.read_namespaced_persistent_volume_claim(
                name=req.instance_disk_pvc,
                namespace=namespace,
            )
            phase = (existing.status.phase or "").lower()
            if phase == "lost":
                raise RuntimeError(f"instance PVC {req.instance_disk_pvc} entered Lost phase")
            return req.instance_disk_pvc
        if not req.image_source_pvc:
            raise RuntimeError("image source PVC is required for clone-based VM launch")

        pvc_name = self._instance_disk_pvc_name(req.instance_id, req.owner)
        try:
            existing = core.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
            # A restart can race with PVC deletion. If we reuse a claim that is terminating,
            # the pod references a missing claim and remains Pending indefinitely.
            if existing.metadata and existing.metadata.deletion_timestamp:
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        core.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=namespace)
                    except ApiException as check_exc:
                        if check_exc.status == 404:
                            break
                        raise
                    time.sleep(2)
                else:
                    raise RuntimeError(f"instance PVC {pvc_name} is still terminating")
                raise ApiException(status=404)
            phase = (existing.status.phase or "").lower()
            if phase == "lost":
                raise RuntimeError(f"instance PVC {pvc_name} entered Lost phase")
            return pvc_name
        except ApiException as exc:
            if exc.status != 404:
                raise

        source, source_namespace = self._read_source_pvc_with_fallback(
            image_source_pvc=req.image_source_pvc,
            runtime_namespace=namespace,
        )
        source_request = None
        if source.spec and source.spec.resources and source.spec.resources.requests:
            source_request = source.spec.resources.requests.get("storage")
        if not source_request:
            raise RuntimeError(f"source PVC {req.image_source_pvc} has no storage request")

        clone_data_source_kwargs = self._clone_data_source_kwargs(
            source_name=req.image_source_pvc,
            source_namespace=source_namespace,
            target_namespace=namespace,
        )

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels={"owner": req.owner, "instance": req.instance_id, "app.kubernetes.io/part-of": "bretter-labs"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=(settings.kube_vm_storage_class or source.spec.storage_class_name or None),
                resources=client.V1ResourceRequirements(requests={"storage": source_request}),
                **clone_data_source_kwargs,
            ),
        )
        core.create_namespaced_persistent_volume_claim(namespace=namespace, body=body)
        return pvc_name

    def reserve_warm_pool_pvc(
        self,
        template_id: str,
        instance_id: str,
        owner: str,
        namespace: str | None = None,
    ) -> Optional[str]:
        core = self._client()
        ns = self._namespace(namespace)
        selector = f"blabs-pool=true,template-id={template_id},pool-state=ready"
        items = core.list_namespaced_persistent_volume_claim(namespace=ns, label_selector=selector).items
        for pvc in items:
            if (pvc.status.phase or "").lower() != "bound":
                continue
            if pvc.metadata and pvc.metadata.deletion_timestamp:
                continue
            labels = dict(pvc.metadata.labels or {})
            labels["pool-state"] = "claimed"
            labels["pool-owner"] = owner
            labels["pool-instance"] = instance_id
            try:
                core.patch_namespaced_persistent_volume_claim(
                    name=pvc.metadata.name,
                    namespace=ns,
                    body={"metadata": {"labels": labels}},
                )
                return pvc.metadata.name
            except ApiException:
                continue
        return None

    def _autoscaled_warm_pool_target(self, min_pool: int, max_pool: int, recent_launches: int) -> int:
        if max_pool < min_pool:
            max_pool = min_pool
        if max_pool <= min_pool:
            return min_pool
        if not settings.warm_pool_autoscale_enabled:
            return min_pool
        window_minutes = max(1, int(settings.warm_pool_window_minutes))
        refill_minutes = max(1, int(settings.warm_pool_refill_minutes))
        safety_factor = max(1.0, float(settings.warm_pool_safety_factor))
        launches_per_minute = float(recent_launches) / float(window_minutes)
        demand_target = int(math.ceil(launches_per_minute * refill_minutes * safety_factor))
        return max(min_pool, min(max_pool, demand_target))

    def ensure_warm_pool(self, template_id: str, image_source_pvc: str, desired: int) -> None:
        core = self._client()
        namespace = self._namespace()
        selector = f"blabs-pool=true,template-id={template_id},pool-state=ready"
        ready_pool = core.list_namespaced_persistent_volume_claim(
            namespace=namespace,
            label_selector=selector,
        ).items
        # Include Pending/Bound "ready" clones in current so we don't over-provision while clones bind.
        current = len([pvc for pvc in ready_pool if not (pvc.metadata and pvc.metadata.deletion_timestamp)])
        if current > desired:
            # Trim oldest ready clones first; claimed clones are not selected by this label set.
            ordered = sorted(
                (pvc for pvc in ready_pool if not (pvc.metadata and pvc.metadata.deletion_timestamp)),
                key=lambda pvc: pvc.metadata.creation_timestamp.timestamp() if pvc.metadata.creation_timestamp else 0,
            )
            for pvc in ordered[: current - desired]:
                try:
                    core.delete_namespaced_persistent_volume_claim(
                        name=pvc.metadata.name,
                        namespace=namespace,
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        logger.warning("Failed to delete warm pool PVC %s", pvc.metadata.name, exc_info=True)
            return
        if current >= desired:
            return

        source, source_namespace = self._read_source_pvc_with_fallback(
            image_source_pvc=image_source_pvc,
            runtime_namespace=namespace,
        )
        source_request = None
        if source.spec and source.spec.resources and source.spec.resources.requests:
            source_request = source.spec.resources.requests.get("storage")
        if not source_request:
            raise RuntimeError(f"source PVC {image_source_pvc} has no storage request")

        storage_class = settings.kube_vm_storage_class or source.spec.storage_class_name or None
        for _ in range(desired - current):
            name = self._pool_pvc_name(template_id)
            body = client.V1PersistentVolumeClaim(
                metadata=client.V1ObjectMeta(
                    name=name,
                    labels={
                        "blabs-pool": "true",
                        "template-id": template_id,
                        "pool-state": "ready",
                        "app.kubernetes.io/part-of": "bretter-labs",
                    },
                ),
                spec=client.V1PersistentVolumeClaimSpec(
                    access_modes=["ReadWriteOnce"],
                    storage_class_name=storage_class,
                    resources=client.V1ResourceRequirements(requests={"storage": source_request}),
                    **self._clone_data_source_kwargs(
                        source_name=image_source_pvc,
                        source_namespace=source_namespace,
                        target_namespace=namespace,
                    ),
                ),
            )
            try:
                core.create_namespaced_persistent_volume_claim(namespace=namespace, body=body)
            except ApiException as exc:
                if exc.status != 409:
                    raise

    def check_vm_runner_image_pullability(
        self,
        *,
        namespace: str | None = None,
        timeout_seconds: int = 30,
    ) -> tuple[bool, str]:
        core = self._client()
        ns = self._namespace(namespace)
        timeout = max(10, int(timeout_seconds or 30))
        digest = hashlib.sha1(str(settings.runner_image).encode("utf-8")).hexdigest()[:10]
        pod_name = f"runner-preflight-{digest}-{uuid4().hex[:6]}"

        metadata = client.V1ObjectMeta(
            name=pod_name,
            labels={
                "app.kubernetes.io/component": "vm-runner-preflight",
                "app.kubernetes.io/part-of": "bretter-labs",
            },
        )
        container = client.V1Container(
            name="runner-preflight",
            image=settings.runner_image,
            image_pull_policy="IfNotPresent",
        )
        spec_kwargs: dict[str, object] = {
            "containers": [container],
            "restart_policy": "Never",
            "termination_grace_period_seconds": 0,
            "tolerations": [
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
        }
        node_selector_value = str(settings.kube_node_selector_value or "").strip()
        if node_selector_value:
            node_selector_key = str(settings.kube_node_selector_key or "kubernetes.io/hostname").strip()
            spec_kwargs["node_selector"] = {node_selector_key: node_selector_value}
        if settings.image_pull_secret:
            spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        pod = client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=client.V1PodSpec(**spec_kwargs))

        try:
            core.create_namespaced_pod(namespace=ns, body=pod)
        except ApiException as exc:
            detail = exc.reason or str(exc.status)
            return False, f"failed to create runner preflight pod: {detail}"

        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                try:
                    snapshot = core.read_namespaced_pod(name=pod_name, namespace=ns)
                except ApiException as exc:
                    if exc.status == 404:
                        return False, "runner preflight pod disappeared before completion"
                    return False, f"failed to poll runner preflight pod: {exc.reason or exc.status}"
                phase = str(snapshot.status.phase or "").strip().lower()
                wait_reason = ""
                wait_message = ""
                for status_row in list(snapshot.status.init_container_statuses or []) + list(
                    snapshot.status.container_statuses or []
                ):
                    state = status_row.state
                    if state and state.waiting:
                        wait_reason = str(state.waiting.reason or "").strip()
                        wait_message = str(state.waiting.message or "").strip()
                        break
                if wait_reason.lower() in {"imagepullbackoff", "errimagepull", "invalidimagename"}:
                    detail = wait_message or wait_reason
                    return False, f"runner image pull failed: {detail}"
                if phase in {"running", "succeeded", "failed"}:
                    # A failed phase can still mean image pull succeeded but command/process exited quickly.
                    return True, f"runner image pull check completed (phase={phase})."
                if phase == "pending":
                    reason = str(snapshot.status.reason or "").lower()
                    message = str(snapshot.status.message or "")
                    if "unschedulable" in reason or "unschedulable" in message.lower():
                        return False, message or "runner preflight pod is unschedulable."
                time.sleep(1)
            return False, f"runner image pull check timed out after {timeout}s"
        finally:
            try:
                core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=ns,
                    grace_period_seconds=0,
                    propagation_policy="Foreground",
                )
            except ApiException as exc:
                if exc.status != 404:
                    logger.debug("Failed to delete runner preflight pod %s: %s", pod_name, exc)

    def create_service_for_pod(
        self,
        pod_name: str,
        service_name: str,
        service_type: str = "NodePort",
        namespace: str | None = None,
    ) -> int | None:
        core = self._client()
        ns = self._namespace(namespace)
        normalized_service_type = "ClusterIP" if str(service_type).lower() == "clusterip" else "NodePort"
        external_traffic_policy = self._vm_console_external_traffic_policy()
        spec_kwargs: dict[str, object] = {
            "selector": {"app": pod_name},
            "type": normalized_service_type,
            "ports": [client.V1ServicePort(port=6080, target_port=6080, protocol="TCP")],
        }
        if normalized_service_type == "NodePort":
            spec_kwargs["external_traffic_policy"] = external_traffic_policy
        body = client.V1Service(
            metadata=client.V1ObjectMeta(name=service_name, labels={"app": pod_name}),
            spec=client.V1ServiceSpec(**spec_kwargs),
        )
        try:
            svc = core.create_namespaced_service(namespace=ns, body=body)
            if normalized_service_type == "NodePort":
                return svc.spec.ports[0].node_port
            return None
        except ApiException as exc:
            if exc.status != 409:
                logger.error("Failed to create service %s: %s", service_name, exc)
                raise
            # If already exists, fetch existing
            existing = core.read_namespaced_service(name=service_name, namespace=ns)
            existing_spec = existing.spec or client.V1ServiceSpec()
            desired_patch: dict[str, object] = {"selector": {"app": pod_name}, "type": normalized_service_type}
            needs_patch = str(existing_spec.type or "") != normalized_service_type
            if normalized_service_type == "NodePort":
                desired_patch["externalTrafficPolicy"] = external_traffic_policy
                if str(existing_spec.external_traffic_policy or "") != external_traffic_policy:
                    needs_patch = True
            if needs_patch:
                core.patch_namespaced_service(name=service_name, namespace=ns, body={"spec": desired_patch})
                existing = core.read_namespaced_service(name=service_name, namespace=ns)
            if normalized_service_type == "NodePort":
                return existing.spec.ports[0].node_port
            return None

    def ensure_container_service(
        self,
        instance_id: str,
        owner: str,
        container_port: int,
        service_type: str = "NodePort",
        namespace: str | None = None,
    ) -> int | None:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._container_pod_name(instance_id, owner)
        service_name = self._container_service_name(instance_id)
        tcp_port = max(1, min(65535, int(container_port or 80)))
        normalized_service_type = "ClusterIP" if str(service_type).lower() == "clusterip" else "NodePort"
        body = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=service_name,
                labels={
                    "app.kubernetes.io/component": "container-runner",
                    "app.kubernetes.io/part-of": "bretter-labs",
                },
            ),
            spec=client.V1ServiceSpec(
                selector={"app": pod_name},
                type=normalized_service_type,
                ports=[
                    client.V1ServicePort(
                        name="http",
                        port=tcp_port,
                        target_port=tcp_port,
                        protocol="TCP",
                    )
                ],
            ),
        )
        try:
            svc = core.create_namespaced_service(namespace=ns, body=body)
            if normalized_service_type == "ClusterIP":
                return None
            return svc.spec.ports[0].node_port
        except ApiException as exc:
            if exc.status != 409:
                logger.error("Failed to create container service %s: %s", service_name, exc)
                raise
            existing = core.read_namespaced_service(name=service_name, namespace=ns)
            existing_spec = existing.spec or client.V1ServiceSpec()
            existing_type = str(existing_spec.type or "NodePort")
            existing_port = existing_spec.ports[0].port if existing_spec.ports else tcp_port
            if int(existing_port or 0) != tcp_port or existing_type != normalized_service_type:
                patch = {
                    "spec": {
                        "type": normalized_service_type,
                        "selector": {"app": pod_name},
                        "ports": [{"name": "http", "port": tcp_port, "targetPort": tcp_port, "protocol": "TCP"}],
                    }
                }
                core.patch_namespaced_service(name=service_name, namespace=ns, body=patch)
                existing = core.read_namespaced_service(name=service_name, namespace=ns)
            if normalized_service_type == "ClusterIP":
                return None
            return existing.spec.ports[0].node_port

    def ensure_container_ingress(
        self,
        instance_id: str,
        service_name: str,
        service_port: int,
        namespace: str | None = None,
    ) -> str | None:
        if not settings.container_ingress_enabled:
            return None
        base_domain = (settings.container_ingress_base_domain or "").strip()
        if not base_domain:
            return None

        networking = self._networking_client()
        ns = self._namespace(namespace)
        ingress_name = self._container_ingress_name(instance_id)
        host = f"ct-{instance_id[:8]}.{base_domain}"
        annotations: dict[str, str] = {}
        try:
            raw = (settings.container_ingress_annotations_json or "").strip()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    annotations = {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            logger.warning("Invalid BLABS_CONTAINER_INGRESS_ANNOTATIONS_JSON value; ignoring annotations.")

        metadata = client.V1ObjectMeta(
            name=ingress_name,
            labels={
                "app.kubernetes.io/component": "container-runner",
                "app.kubernetes.io/part-of": "bretter-labs",
            },
            annotations=annotations or None,
        )
        spec = client.V1IngressSpec(
            ingress_class_name=(settings.container_ingress_class or None),
            rules=[
                client.V1IngressRule(
                    host=host,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=service_name,
                                        port=client.V1ServiceBackendPort(number=max(1, int(service_port))),
                                    )
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
        body = client.V1Ingress(api_version="networking.k8s.io/v1", kind="Ingress", metadata=metadata, spec=spec)
        try:
            networking.create_namespaced_ingress(namespace=ns, body=body)
        except ApiException as exc:
            if exc.status != 409:
                logger.warning("Failed to create container ingress %s: %s", ingress_name, exc)
                return None
            try:
                networking.patch_namespaced_ingress(name=ingress_name, namespace=ns, body=body)
            except ApiException as patch_exc:
                logger.warning("Failed to patch container ingress %s: %s", ingress_name, patch_exc)
                return None
        return host

    def delete_container_ingress(self, instance_id: str, namespace: str | None = None) -> None:
        networking = self._networking_client()
        ns = self._namespace(namespace)
        ingress_name = self._container_ingress_name(instance_id)
        try:
            networking.delete_namespaced_ingress(name=ingress_name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete container ingress %s: %s", ingress_name, exc)
                raise

    def delete_container_service(self, instance_id: str, namespace: str | None = None) -> None:
        core = self._client()
        networking = self._networking_client()
        ns = self._namespace(namespace)
        service_name = self._container_service_name(instance_id)
        netpol_name = self._container_netpol_name(instance_id)
        try:
            self.delete_container_ingress(instance_id, namespace=ns)
        except ApiException:
            pass
        try:
            networking.delete_namespaced_network_policy(name=netpol_name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete container network policy %s: %s", netpol_name, exc)
                raise
        try:
            core.delete_namespaced_service(name=service_name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete container service %s: %s", service_name, exc)
                raise

    def prepull_container_image(self, image_ref: str, timeout_seconds: int | None = None) -> None:
        if not settings.container_image_prepull_enabled:
            return
        core = self._client()
        namespace = self._namespace()
        timeout = max(10, int(timeout_seconds or settings.container_image_prepull_timeout_seconds))
        digest = hashlib.sha1(image_ref.encode("utf-8")).hexdigest()[:10]
        try:
            nodes = core.list_node().items
        except ApiException as exc:
            logger.warning("Failed to list nodes for container image pre-pull: %s", exc)
            return

        for node in nodes:
            node_name = (node.metadata.name or "").strip()
            if not node_name:
                continue
            node_ready = False
            for condition in node.status.conditions or []:
                if condition.type == "Ready" and condition.status == "True":
                    node_ready = True
                    break
            if not node_ready:
                continue

            pod_name = f"imgpull-{digest}-{self._safe_owner(node_name)[:20]}".strip("-")
            metadata = client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "app.kubernetes.io/component": "container-prepull",
                    "app.kubernetes.io/part-of": "bretter-labs",
                    "blabs-image-hash": digest,
                },
            )
            container = client.V1Container(
                name="prepull",
                image=image_ref,
                image_pull_policy="IfNotPresent",
                resources=client.V1ResourceRequirements(
                    requests={"cpu": "50m", "memory": "64Mi"},
                    limits={"cpu": "250m", "memory": "256Mi"},
                ),
                security_context=client.V1SecurityContext(
                    allow_privilege_escalation=False,
                    capabilities=client.V1Capabilities(drop=["ALL"]),
                    read_only_root_filesystem=False,
                ),
            )
            spec_kwargs: dict[str, object] = {
                "containers": [container],
                "node_name": node_name,
                "restart_policy": "Never",
                "termination_grace_period_seconds": 0,
                "tolerations": [
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
            }
            if settings.image_pull_secret:
                spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
            body = client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=client.V1PodSpec(**spec_kwargs))
            try:
                core.create_namespaced_pod(namespace=namespace, body=body)
            except ApiException as exc:
                if exc.status != 409:
                    logger.warning("Failed to create pre-pull pod %s: %s", pod_name, exc)
                    continue

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
                except ApiException as exc:
                    if exc.status == 404:
                        break
                    logger.debug("Failed to poll pre-pull pod %s: %s", pod_name, exc)
                    time.sleep(1)
                    continue

                phase = (pod.status.phase or "").lower()
                if phase in {"running", "succeeded", "failed"}:
                    break
                wait_reason = ""
                for status in list(pod.status.init_container_statuses or []) + list(
                    pod.status.container_statuses or []
                ):
                    state = status.state
                    if state and state.waiting:
                        wait_reason = (state.waiting.reason or "").lower()
                        break
                if wait_reason in {"imagepullbackoff", "errimagepull"}:
                    break
                time.sleep(1)

            try:
                core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    grace_period_seconds=0,
                    propagation_policy="Foreground",
                )
            except ApiException as exc:
                if exc.status != 404:
                    logger.debug("Failed to delete pre-pull pod %s: %s", pod_name, exc)

    def _console_url(self, req: PodRequest) -> str:
        return ""

    def create_pod(self, req: PodRequest) -> PodStatus:
        core = self._client()
        pod_name = self._pod_name(req)
        namespace = self._namespace(req.namespace)
        self.ensure_namespace(namespace)
        vm_network_mode = normalize_vm_network_mode(req.network_mode)
        console_provider = normalize_vm_console_provider(req.console_provider)
        instance_disk_pvc = self._ensure_instance_disk_pvc(req)
        guest_ram_mb = max(512, int(req.ram_mb))
        memory_overhead_mb = max(0, int(settings.vm_memory_overhead_mb))
        # Give QEMU headroom above guest RAM to avoid cgroup OOM kills from host overhead.
        pod_ram_mb = guest_ram_mb + memory_overhead_mb
        tls_secret_name = (settings.kube_tls_secret or "").strip()
        metadata = client.V1ObjectMeta(
            name=pod_name,
            labels={
                "app": pod_name,
                "owner": req.owner,
                "instance": req.instance_id,
                "app.kubernetes.io/component": "vm-runner",
                "app.kubernetes.io/part-of": "bretter-labs",
            },
        )
        cpu_value = str(max(1, int(req.cpu_cores)))
        if settings.vm_qos_guaranteed:
            # Guaranteed QoS reduces eviction risk and scheduler jitter for VM workloads.
            resources = client.V1ResourceRequirements(
                limits={"cpu": cpu_value, "memory": f"{pod_ram_mb}Mi"},
                requests={"cpu": cpu_value, "memory": f"{pod_ram_mb}Mi"},
            )
        else:
            resources = client.V1ResourceRequirements(
                limits={"cpu": cpu_value, "memory": f"{pod_ram_mb}Mi"},
                requests={"cpu": cpu_value, "memory": f"{guest_ram_mb}Mi"},
            )
        volume_mounts = [client.V1VolumeMount(name="data", mount_path="/data", read_only=False)]
        volumes = [
            client.V1Volume(
                name="data",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=instance_disk_pvc),
            )
        ]
        if tls_secret_name:
            volumes.append(
                client.V1Volume(
                    name="tls-cert",
                    secret=client.V1SecretVolumeSource(secret_name=tls_secret_name, optional=True),
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="tls-cert", mount_path="/tls", read_only=True))
        if settings.kube_spice_embed_configmap:
            volumes.append(
                client.V1Volume(
                    name="spice-embed",
                    config_map=client.V1ConfigMapVolumeSource(
                        name=settings.kube_spice_embed_configmap,
                        items=[client.V1KeyToPath(key="spice-embed.html", path="spice-embed.html")],
                    ),
                )
            )
        # Optional KVM passthrough if requested.
        if settings.kube_use_kvm:
            volumes.append(
                client.V1Volume(
                    name="kvm",
                    host_path=client.V1HostPathVolumeSource(path="/dev/kvm", type="CharDevice"),
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="kvm", mount_path="/dev/kvm"))
        if settings.vm_net_backend == "tap-nat":
            volumes.append(
                client.V1Volume(
                    name="tun",
                    host_path=client.V1HostPathVolumeSource(path="/dev/net/tun", type="CharDevice"),
                )
            )
            volume_mounts.append(client.V1VolumeMount(name="tun", mount_path="/dev/net/tun"))
            if settings.vm_vhost_net_enabled:
                volumes.append(
                    client.V1Volume(
                        name="vhost-net",
                        host_path=client.V1HostPathVolumeSource(path="/dev/vhost-net"),
                    )
                )
                volume_mounts.append(client.V1VolumeMount(name="vhost-net", mount_path="/dev/vhost-net"))
        os_type = req.os_type.lower()
        is_linux = os_type == "linux"
        # Clone-backed instance disks are mounted at /data; Linux defaults to virtio for faster IO.
        dest_disk = f"/data/{Path(req.image_path).name}"
        drive_if = "virtio" if is_linux else "ide"
        # SPICE works best with qxl on Windows images; keep std on Linux guests.
        vga = "std" if is_linux else "qxl"
        if console_provider in {"guacamole", "guacamole_rdp"} and vga == "qxl":
            vga = "std"
        machine_type = settings.linux_machine_type if is_linux else settings.windows_machine_type
        efi_enabled = settings.linux_efi_enabled if is_linux else settings.windows_efi_enabled
        cpu_model = settings.linux_cpu_model if is_linux else settings.windows_cpu_model
        env_vars = [
            client.V1EnvVar(name="CPU_CORES", value=str(req.cpu_cores)),
            client.V1EnvVar(name="RAM_MB", value=str(req.ram_mb)),
            client.V1EnvVar(name="OS_TYPE", value=os_type),
            client.V1EnvVar(name="DRIVE_IF", value=drive_if),
            client.V1EnvVar(name="VGA_TYPE", value=vga),
            client.V1EnvVar(name="MACHINE_TYPE", value=machine_type),
            client.V1EnvVar(name="EFI_ENABLED", value=str(efi_enabled).lower()),
            client.V1EnvVar(name="CPU_MODEL", value=cpu_model),
            client.V1EnvVar(name="VM_NET_BACKEND", value=settings.vm_net_backend),
            client.V1EnvVar(name="VM_VHOST_NET_ENABLED", value=str(settings.vm_vhost_net_enabled).lower()),
            client.V1EnvVar(name="VM_NET_MULTIQUEUE_ENABLED", value=str(settings.vm_net_multiqueue_enabled).lower()),
            client.V1EnvVar(name="VM_NET_QUEUES", value=str(max(1, int(req.cpu_cores)))),
            client.V1EnvVar(name="CONSOLE_PROVIDER", value=console_provider),
        ]
        installer_iso_filename = str(getattr(req, "installer_iso_filename", "") or "").strip()
        if installer_iso_filename:
            normalized_iso_path = installer_iso_filename.lstrip("/").replace("\\", "/")
            if ".." in Path(normalized_iso_path).parts:
                raise ValueError("installer_iso_filename contains invalid path traversal segments")
            if "/" in normalized_iso_path:
                if not settings.kube_image_pvc:
                    raise ValueError("BLABS_KUBE_IMAGE_PVC is required when installer ISO path includes a subdirectory")
                image_library_volume_name = "image-library"
                if not any(volume.name == image_library_volume_name for volume in volumes):
                    volumes.append(
                        client.V1Volume(
                            name=image_library_volume_name,
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name=settings.kube_image_pvc
                            ),
                        )
                    )
                    volume_mounts.append(
                        client.V1VolumeMount(
                            name=image_library_volume_name,
                            mount_path="/image-library",
                            read_only=True,
                        )
                    )
                env_vars.append(client.V1EnvVar(name="BOOT_ISO", value=f"/image-library/{normalized_iso_path}"))
            else:
                env_vars.append(client.V1EnvVar(name="BOOT_ISO", value=f"/data/{Path(normalized_iso_path).name}"))
        boot_order = str(getattr(req, "boot_order", "") or "").strip()
        if boot_order:
            env_vars.append(client.V1EnvVar(name="BOOT_ORDER", value=boot_order))
        if console_provider == "spice":
            env_vars.append(client.V1EnvVar(name="SPICE_TICKETING", value="true"))
            if req.spice_password:
                env_vars.append(client.V1EnvVar(name="SPICE_PASSWORD", value=req.spice_password))
        else:
            env_vars.append(client.V1EnvVar(name="SPICE_TICKETING", value="false"))
        if console_provider == "guacamole_rdp":
            if req.rdp_default_username:
                env_vars.append(client.V1EnvVar(name="RDP_DEFAULT_USERNAME", value=req.rdp_default_username))
            if req.rdp_default_password:
                env_vars.append(client.V1EnvVar(name="RDP_DEFAULT_PASSWORD", value=req.rdp_default_password))
        if tls_secret_name:
            env_vars.extend(
                [
                    client.V1EnvVar(name="TLS_CERT_FILE", value="/tls/tls.crt"),
                    client.V1EnvVar(name="TLS_KEY_FILE", value="/tls/tls.key"),
                ]
            )
        container = client.V1Container(
            name="vm-runner",
            image=settings.runner_image,
            args=["--disk", dest_disk, "--console", self._console_url(req)],
            env=env_vars,
            resources=resources,
            volume_mounts=volume_mounts,
            image_pull_policy="IfNotPresent",
            startup_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=6080),
                failure_threshold=60,
                period_seconds=5,
                timeout_seconds=2,
            ),
            readiness_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=6080),
                period_seconds=10,
                timeout_seconds=2,
                failure_threshold=3,
            ),
            liveness_probe=client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(port=6080),
                period_seconds=20,
                timeout_seconds=2,
                failure_threshold=3,
            ),
            security_context=client.V1SecurityContext(
                privileged=(
                    settings.vm_runner_privileged or settings.kube_use_kvm or settings.vm_net_backend == "tap-nat"
                )
            ),
        )
        if settings.kube_spice_embed_configmap:
            volume_mounts.append(
                client.V1VolumeMount(
                    name="spice-embed",
                    mount_path="/usr/share/spice-html5/spice-embed.html",
                    sub_path="spice-embed.html",
                    read_only=True,
                )
            )
        spec_kwargs = {
            "containers": [container],
            "restart_policy": "Never",
            "volumes": volumes,
            # Keep VM runners on pod networking; host networking causes fixed-port collisions.
            "host_network": False,
            "tolerations": [
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
                # Worker nodes can briefly taint themselves during image clone/copy spikes.
                # Allow VM pods to schedule so startup can complete instead of stalling Pending.
                client.V1Toleration(
                    key="node.kubernetes.io/disk-pressure",
                    operator="Exists",
                    effect="NoSchedule",
                ),
            ],
        }
        if settings.vm_runner_anti_affinity_enabled:
            spec_kwargs["affinity"] = client.V1Affinity(
                pod_anti_affinity=client.V1PodAntiAffinity(
                    preferred_during_scheduling_ignored_during_execution=[
                        client.V1WeightedPodAffinityTerm(
                            weight=100,
                            pod_affinity_term=client.V1PodAffinityTerm(
                                label_selector=client.V1LabelSelector(
                                    match_labels={"app.kubernetes.io/component": "vm-runner"}
                                ),
                                topology_key="kubernetes.io/hostname",
                            ),
                        )
                    ]
                )
            )
        if settings.vm_runner_topology_spread_enabled:
            spec_kwargs["topology_spread_constraints"] = [
                client.V1TopologySpreadConstraint(
                    max_skew=1,
                    topology_key="kubernetes.io/hostname",
                    when_unsatisfiable="ScheduleAnyway",
                    label_selector=client.V1LabelSelector(match_labels={"app.kubernetes.io/component": "vm-runner"}),
                )
            ]
        if settings.image_pull_secret:
            spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        if settings.kube_runtime_class:
            spec_kwargs["runtime_class_name"] = settings.kube_runtime_class
        if settings.image_pull_secret:
            spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        if settings.kube_node_selector_value:
            spec_kwargs["node_selector"] = {settings.kube_node_selector_key: settings.kube_node_selector_value}
        spec = client.V1PodSpec(**spec_kwargs)
        body = client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=spec)
        try:
            core.create_namespaced_pod(namespace=namespace, body=body)
            if vm_network_mode != "unrestricted":
                self.apply_network_policy(pod_name, mode=vm_network_mode, namespace=namespace)
            return PodStatus(
                instance_id=req.instance_id,
                phase="Pending",
                reason="Pending",
                console_endpoint=self._console_url(req),
                disk_pvc=instance_disk_pvc,
            )
        except ApiException as exc:
            logger.error("Failed to create pod: %s", exc)
            raise

    def create_container_pod(self, req: ContainerPodRequest) -> PodStatus:
        core = self._client()
        namespace = self._namespace(req.namespace)
        self.ensure_namespace(namespace)
        pod_name = self._container_pod_name(req.instance_id, req.owner)

        metadata = client.V1ObjectMeta(
            name=pod_name,
            labels={
                "app": pod_name,
                "owner": req.owner,
                "instance": req.instance_id,
                "app.kubernetes.io/component": "container-runner",
                "app.kubernetes.io/part-of": "bretter-labs",
            },
        )
        cpu_m = max(50, int(req.cpu_millicores))
        mem_mb = max(64, int(req.memory_mb))
        tcp_port = max(1, min(65535, int(req.container_port or 80)))
        protocol = "http" if str(req.healthcheck_protocol or "tcp").lower() == "http" else "tcp"
        healthcheck_path = str(req.healthcheck_path or "/").strip() or "/"
        if not healthcheck_path.startswith("/"):
            healthcheck_path = f"/{healthcheck_path}"
        startup_timeout = max(10, min(1800, int(req.startup_timeout_seconds or 300)))
        startup_failure_threshold = max(10, int(math.ceil(startup_timeout / 5)))
        dependency_checks: list[tuple[str, int, int]] = []
        for dep in req.dependency_checks or []:
            host = ""
            port_val = 0
            timeout_val = 90
            if isinstance(dep, dict):
                host = str(dep.get("host") or "").strip()
                port_val = int(dep.get("port") or 0)
                timeout_val = int(dep.get("timeout_seconds") or 90)
            else:
                host = str(getattr(dep, "host", "") or "").strip()
                port_val = int(getattr(dep, "port", 0) or 0)
                timeout_val = int(getattr(dep, "timeout_seconds", 90) or 90)
            if not host or port_val < 1 or port_val > 65535:
                continue
            timeout_val = max(5, min(600, timeout_val))
            dependency_checks.append((host, port_val, timeout_val))
        resources = client.V1ResourceRequirements(
            limits={"cpu": f"{cpu_m}m", "memory": f"{mem_mb}Mi"},
            requests={"cpu": f"{cpu_m}m", "memory": f"{mem_mb}Mi"},
        )
        probe_kwargs: dict[str, object]
        if protocol == "http":
            probe_kwargs = {"http_get": client.V1HTTPGetAction(path=healthcheck_path, port=tcp_port, scheme="HTTP")}
        else:
            probe_kwargs = {"tcp_socket": client.V1TCPSocketAction(port=tcp_port)}
        container_security_kwargs: dict[str, object] = {
            "read_only_root_filesystem": bool(req.read_only_root_filesystem),
        }
        if req.run_as_non_root:
            # Hardened profile for non-root workloads.
            container_security_kwargs.update(
                {
                    "allow_privilege_escalation": False,
                    "capabilities": client.V1Capabilities(drop=["ALL"]),
                    "run_as_non_root": True,
                    "seccomp_profile": client.V1SeccompProfile(type="RuntimeDefault"),
                }
            )

        container_kwargs: dict[str, object] = {
            "name": "container-runner",
            "image": req.image_ref,
            "resources": resources,
            "image_pull_policy": "IfNotPresent",
            "ports": [client.V1ContainerPort(container_port=tcp_port)],
            "startup_probe": client.V1Probe(
                **probe_kwargs,
                period_seconds=5,
                timeout_seconds=2,
                failure_threshold=startup_failure_threshold,
            ),
            "readiness_probe": client.V1Probe(
                **probe_kwargs,
                period_seconds=5,
                timeout_seconds=2,
                failure_threshold=3,
            ),
            "liveness_probe": client.V1Probe(
                **probe_kwargs,
                period_seconds=20,
                timeout_seconds=2,
                failure_threshold=3,
                initial_delay_seconds=max(15, min(300, startup_timeout // 2)),
            ),
            "security_context": client.V1SecurityContext(**container_security_kwargs),
        }
        args = [arg for arg in (req.args or []) if str(arg).strip()]
        if req.command:
            shell_cmd = req.command
            if args:
                shell_cmd = f"{shell_cmd} {' '.join(shlex.quote(str(arg)) for arg in args)}"
            container_kwargs["command"] = ["/bin/sh", "-lc", shell_cmd]
        elif args:
            container_kwargs["args"] = args
        env_vars = []
        for key, value in (req.env or {}).items():
            k = str(key).strip()
            if not k:
                continue
            env_vars.append(client.V1EnvVar(name=k, value=str(value)))
        if env_vars:
            container_kwargs["env"] = env_vars

        container = client.V1Container(**container_kwargs)
        spec_kwargs: dict[str, object] = {
            "containers": [container],
            "restart_policy": "Never",
            "security_context": client.V1PodSecurityContext(
                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
            ),
            "tolerations": [
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
                client.V1Toleration(
                    key="node.kubernetes.io/disk-pressure",
                    operator="Exists",
                    effect="NoSchedule",
                ),
            ],
        }
        if dependency_checks:
            lines = ["set -eu"]
            for host, dep_port, timeout_seconds in dependency_checks:
                safe_host = shlex.quote(host)
                lines.extend(
                    [
                        f'echo "Checking dependency {host}:{dep_port}"',
                        f"deadline=$(( $(date +%s) + {timeout_seconds} ))",
                        "while true; do",
                        f"  if nslookup {safe_host} >/dev/null 2>&1 && nc -z -w 2 {safe_host} {dep_port} >/dev/null 2>&1; then",
                        "    break",
                        "  fi",
                        '  if [ "$(date +%s)" -ge "$deadline" ]; then',
                        f'    echo "Dependency {host}:{dep_port} not reachable before timeout"',
                        "    exit 1",
                        "  fi",
                        "  sleep 2",
                        "done",
                    ]
                )
            init_container = client.V1Container(
                name="wait-dependencies",
                image="busybox:1.36",
                image_pull_policy="IfNotPresent",
                command=["/bin/sh", "-c", "\n".join(lines)],
                resources=client.V1ResourceRequirements(
                    requests={"cpu": "50m", "memory": "64Mi"},
                    limits={"cpu": "250m", "memory": "128Mi"},
                ),
                security_context=client.V1SecurityContext(
                    allow_privilege_escalation=False,
                    capabilities=client.V1Capabilities(drop=["ALL"]),
                    read_only_root_filesystem=False,
                    seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                ),
            )
            spec_kwargs["init_containers"] = [init_container]
        if settings.image_pull_secret:
            spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        if settings.kube_runtime_class:
            spec_kwargs["runtime_class_name"] = settings.kube_runtime_class
        if settings.kube_node_selector_value:
            spec_kwargs["node_selector"] = {settings.kube_node_selector_key: settings.kube_node_selector_value}

        spec = client.V1PodSpec(**spec_kwargs)
        body = client.V1Pod(api_version="v1", kind="Pod", metadata=metadata, spec=spec)
        try:
            core.create_namespaced_pod(namespace=namespace, body=body)
            self.apply_container_network_policy(
                req.instance_id,
                pod_name,
                tcp_port,
                mode=req.network_mode,
                namespace=namespace,
            )
            return PodStatus(instance_id=req.instance_id, phase="Pending", reason="Pending")
        except ApiException as exc:
            logger.error("Failed to create container pod: %s", exc)
            raise

    def stop_pod(self, instance_id: str, owner: str, namespace: str | None = None) -> PodStatus:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_pod_name(instance_id, owner)
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=ns)
            phase = (pod.status.phase or "").lower()
            if phase in {"succeeded", "failed"}:
                return PodStatus(instance_id=instance_id, phase=pod.status.phase or "Succeeded")
        except ApiException as exc:
            if exc.status == 404:
                return PodStatus(instance_id=instance_id, phase="Succeeded")
            logger.error("Failed to read pod %s: %s", pod_name, exc)
            raise
        # Gracefully stop QEMU inside the container without deleting the pod object.
        try:
            stream(
                core.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=ns,
                command=["/bin/sh", "-c", "kill -TERM 1 || true"],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to send stop signal to %s: %s", pod_name, exc)
        return PodStatus(instance_id=instance_id, phase="Succeeded")

    def delete_pod(
        self,
        instance_id: str,
        owner: str,
        disk_pvc: Optional[str] = None,
        namespace: str | None = None,
        delete_disk_pvc: bool = True,
    ) -> None:
        core = self._client()
        networking = self._networking_client()
        ns = self._namespace(namespace)
        pod_name = self._find_pod_name(instance_id, owner)
        pvc_name = disk_pvc or self._instance_disk_pvc_name(instance_id, owner)
        service_name = self._instance_service_name(instance_id)
        netpol_name = self._instance_netpol_name(instance_id, owner)
        try:
            core.delete_namespaced_service(name=service_name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete service %s: %s", service_name, exc)
                raise
        try:
            networking.delete_namespaced_network_policy(name=netpol_name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete network policy %s: %s", netpol_name, exc)
                raise
        try:
            core.delete_namespaced_pod(
                name=pod_name,
                namespace=ns,
                grace_period_seconds=0,
                propagation_policy="Foreground",
            )
        except ApiException as exc:
            if exc.status == 404:
                pass
            else:
                logger.error("Failed to delete pod %s: %s", pod_name, exc)
                raise
        if not delete_disk_pvc:
            return
        if pvc_name.startswith("img-src-"):
            # Guardrail: never remove golden-source PVCs via instance teardown.
            logger.info("Skipping delete of source PVC %s during instance teardown.", pvc_name)
            return
        try:
            core.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=ns)
        except ApiException as exc:
            if exc.status == 404:
                return
            logger.error("Failed to delete instance PVC %s: %s", pvc_name, exc)
            raise

    def stop_container_pod(self, instance_id: str, owner: str, namespace: str | None = None) -> PodStatus:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_container_pod_name(instance_id, owner)
        try:
            core.delete_namespaced_pod(name=pod_name, namespace=ns, grace_period_seconds=10)
        except ApiException as exc:
            if exc.status == 404:
                return PodStatus(instance_id=instance_id, phase="Succeeded")
            logger.error("Failed to stop container pod %s: %s", pod_name, exc)
            raise
        return PodStatus(instance_id=instance_id, phase="Succeeded")

    def delete_container_pod(self, instance_id: str, owner: str, namespace: str | None = None) -> None:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_container_pod_name(instance_id, owner)
        try:
            core.delete_namespaced_pod(
                name=pod_name,
                namespace=ns,
                grace_period_seconds=0,
                propagation_policy="Foreground",
            )
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete container pod %s: %s", pod_name, exc)
                raise

    def get_container_status(self, instance_id: str, owner: str, namespace: str | None = None) -> PodStatus:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_container_pod_name(instance_id, owner)
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=ns)
            phase = pod.status.phase or "Unknown"
            node = pod.spec.node_name
            message = pod.status.message
            reason = pod.status.reason
            waiting_reason = None
            waiting_message = None
            container_statuses = pod.status.container_statuses or []
            init_statuses = pod.status.init_container_statuses or []
            for status in [*init_statuses, *container_statuses]:
                state = status.state
                if state and state.waiting:
                    waiting_reason = state.waiting.reason or waiting_reason
                    waiting_message = state.waiting.message or waiting_message
                    if waiting_reason or waiting_message:
                        break
            for cond in pod.status.conditions or []:
                if cond.type == "PodScheduled" and cond.status == "False":
                    reason = cond.reason or reason
                    message = cond.message or message
                    break
            ready = bool(container_statuses) and all(bool(status.ready) for status in container_statuses)
            return PodStatus(
                instance_id=instance_id,
                phase=phase,
                node=node,
                message=message,
                reason=reason,
                waiting_reason=waiting_reason,
                waiting_message=waiting_message,
                ready=ready,
            )
        except ApiException as exc:
            logger.error("Failed to read container pod %s: %s", pod_name, exc)
            raise

    def get_container_launch_diagnostics(
        self,
        instance_id: str,
        owner: str,
        max_items: int = 8,
        namespace: str | None = None,
    ) -> list[str]:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_container_pod_name(instance_id, owner)
        details: list[str] = []
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=ns)
        except ApiException as exc:
            if exc.status == 404:
                return ["Pod not found yet."]
            raise

        for cond in pod.status.conditions or []:
            if cond.type == "PodScheduled" and cond.status == "False":
                reason = cond.reason or "Unschedulable"
                msg = cond.message or "Pod is waiting for scheduler placement."
                details.append(f"{reason}: {msg}")
                break
        for status in list(pod.status.init_container_statuses or []) + list(pod.status.container_statuses or []):
            state = status.state
            if state and state.waiting:
                reason = state.waiting.reason or "Waiting"
                msg = state.waiting.message or ""
                entry = f"{status.name}: {reason}"
                if msg:
                    entry = f"{entry} - {msg}"
                details.append(entry)
            if state and state.terminated:
                reason = state.terminated.reason or "Terminated"
                msg = state.terminated.message or ""
                entry = f"{status.name}: {reason}"
                if msg:
                    entry = f"{entry} - {msg}"
                details.append(entry)

        try:
            events = core.list_namespaced_event(
                namespace=ns,
                field_selector=f"involvedObject.kind=Pod,involvedObject.name={pod_name}",
            ).items
        except ApiException:
            events = []
        events.sort(
            key=lambda ev: (
                ev.last_timestamp
                or ev.event_time
                or ev.first_timestamp
                or (ev.metadata.creation_timestamp if ev.metadata else None)
                or datetime.min
            )
        )
        for event in events[-max_items:]:
            reason = str(event.reason or "Event").strip()
            message = str(event.message or "").strip()
            if message:
                details.append(f"{reason}: {message}")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in details:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized[:300])
            if len(deduped) >= max_items:
                break
        return deduped

    def scan_container_image(self, image_ref: str, severity: str = "HIGH,CRITICAL") -> tuple[str, str]:
        script = r"""
set +e
trivy image --quiet --no-progress --format json --severity "$SEVERITY" "$IMAGE_REF" >/tmp/trivy.json 2>/tmp/trivy.err
rc=$?
printf '__BLABS_TRIVY_JSON_BEGIN__\n'
cat /tmp/trivy.json 2>/dev/null || true
printf '\n__BLABS_TRIVY_JSON_END__\n'
printf '__BLABS_TRIVY_ERR_BEGIN__\n'
cat /tmp/trivy.err 2>/dev/null || true
printf '\n__BLABS_TRIVY_ERR_END__\n'
exit "$rc"
"""
        env = {"IMAGE_REF": str(image_ref or "").strip(), "SEVERITY": str(severity or "HIGH,CRITICAL").strip()}
        try:
            code, output = self._run_admin_helper_pod(
                command=["/bin/sh", "-c", script],
                timeout_seconds=240,
                env=env,
            )
        except TimeoutError:
            return "error", "scan timed out"
        logs = str(output or "")
        json_match = re.search(r"__BLABS_TRIVY_JSON_BEGIN__\n(.*?)\n__BLABS_TRIVY_JSON_END__", logs, re.DOTALL)
        err_match = re.search(r"__BLABS_TRIVY_ERR_BEGIN__\n(.*?)\n__BLABS_TRIVY_ERR_END__", logs, re.DOTALL)
        json_payload = (json_match.group(1) if json_match else "").strip()
        err_payload = (err_match.group(1) if err_match else "").strip()

        if code == 127:
            return "skipped", "trivy is not installed in backend admin image"
        if code not in {0, 1}:
            detail = (err_payload or logs or "scan command failed").strip()
            return "error", detail[:512]
        try:
            payload = json.loads(json_payload or "{}")
        except Exception:
            return "error", "scan output could not be parsed"
        critical = 0
        high = 0
        results = payload.get("Results") if isinstance(payload, dict) else None
        for result_item in results or []:
            vulns = (result_item or {}).get("Vulnerabilities") or []
            for vuln in vulns:
                sev = str((vuln or {}).get("Severity") or "").upper()
                if sev == "CRITICAL":
                    critical += 1
                elif sev == "HIGH":
                    high += 1
        total = critical + high
        if total == 0:
            return "clean", "No HIGH/CRITICAL vulnerabilities detected"
        return "vulnerable", f"HIGH={high}, CRITICAL={critical}"

    def get_status(self, instance_id: str, owner: str, namespace: str | None = None) -> PodStatus:
        core = self._client()
        ns = self._namespace(namespace)
        pod_name = self._find_pod_name(instance_id, owner)
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=ns)
            phase = pod.status.phase or "Unknown"
            node = pod.spec.node_name
            message = pod.status.message
            reason = pod.status.reason
            waiting_reason = None
            waiting_message = None
            container_statuses = pod.status.container_statuses or []
            init_statuses = pod.status.init_container_statuses or []
            for status in [*init_statuses, *container_statuses]:
                state = status.state
                if state and state.waiting:
                    waiting_reason = state.waiting.reason or waiting_reason
                    waiting_message = state.waiting.message or waiting_message
                    if waiting_reason or waiting_message:
                        break
            # If scheduling failed, surface the scheduler reason/message explicitly.
            for cond in pod.status.conditions or []:
                if cond.type == "PodScheduled" and cond.status == "False":
                    reason = cond.reason or reason
                    message = cond.message or message
                    break
            ready = bool(container_statuses) and all(bool(status.ready) for status in container_statuses)
            return PodStatus(
                instance_id=instance_id,
                phase=phase,
                node=node,
                message=message,
                reason=reason,
                waiting_reason=waiting_reason,
                waiting_message=waiting_message,
                ready=ready,
            )
        except ApiException as exc:
            logger.error("Failed to read pod %s: %s", pod_name, exc)
            raise

    def apply_network_policy(self, pod_name: str, mode: str = "default", namespace: str | None = None) -> None:
        networking = self._networking_client()
        ns = self._namespace(namespace)
        policy = self.desired_network_policy(pod_name, ns, mode=mode)
        try:
            networking.create_namespaced_network_policy(namespace=ns, body=policy)
        except ApiException as exc:
            if exc.status == 409:
                try:
                    networking.patch_namespaced_network_policy(
                        name=policy.metadata.name,
                        namespace=ns,
                        body={"spec": policy.spec},
                    )
                except ApiException as patch_exc:
                    logger.error("Failed to update network policy for %s: %s", pod_name, patch_exc)
                    raise
            else:
                logger.error("Failed to apply network policy for %s: %s", pod_name, exc)
                raise

    def desired_network_policy(self, pod_name: str, namespace: str, mode: str = "bridge") -> client.V1NetworkPolicy:
        # bridge: allow DNS + outbound web; isolated/none: deny all egress; ingress always allows SPICE websocket.
        egress_rules = []
        if mode not in {"isolated", "none"}:
            egress_ports = [
                client.V1NetworkPolicyPort(protocol="TCP", port=53),
                client.V1NetworkPolicyPort(protocol="UDP", port=53),
                client.V1NetworkPolicyPort(protocol="TCP", port=443),
                client.V1NetworkPolicyPort(protocol="TCP", port=80),
            ]
            egress_rules = [client.V1NetworkPolicyEgressRule(ports=egress_ports)]
        ingress_peers = self._vm_console_ingress_peers()
        ingress_rule = client.V1NetworkPolicyIngressRule(
            ports=[
                client.V1NetworkPolicyPort(protocol="TCP", port=6080),
            ],
            _from=ingress_peers,
        )
        return client.V1NetworkPolicy(
            api_version="networking.k8s.io/v1",
            kind="NetworkPolicy",
            metadata=client.V1ObjectMeta(name=f"{pod_name}-egress-only", namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={"app": pod_name}),
                policy_types=["Ingress", "Egress"],
                ingress=[ingress_rule],
                egress=egress_rules,
            ),
        )

    def _vm_console_external_traffic_policy(self) -> str:
        raw = str(getattr(settings, "vm_console_external_traffic_policy", "Local") or "Local").strip().lower()
        if raw not in {"cluster", "local"}:
            return "Local"
        return "Cluster" if raw == "cluster" else "Local"

    def _vm_console_ingress_peers(self) -> list[client.V1NetworkPolicyPeer] | None:
        raw = str(getattr(settings, "vm_console_source_cidrs", "") or "").strip()
        if not raw:
            return None
        # When CIDR allowlists are enabled, always allow backend pods so proxy-based console access
        # keeps working even if pod CIDRs are not part of the external source ranges.
        peers: list[client.V1NetworkPolicyPeer] = [
            client.V1NetworkPolicyPeer(pod_selector=client.V1LabelSelector(match_labels={"app": "bretter-backend"}))
        ]
        for entry in raw.split(","):
            cidr = entry.strip()
            if not cidr:
                continue
            try:
                normalized = str(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                logger.warning("Ignoring invalid BLABS_VM_CONSOLE_SOURCE_CIDRS entry: %s", cidr)
                continue
            peers.append(client.V1NetworkPolicyPeer(ip_block=client.V1IPBlock(cidr=normalized)))
        return peers or None

    def apply_container_network_policy(
        self,
        instance_id: str,
        pod_name: str,
        app_port: int,
        mode: str = "bridge",
        namespace: str | None = None,
    ) -> None:
        networking = self._networking_client()
        ns = self._namespace(namespace)
        normalized_mode = str(mode or "bridge").strip().lower()
        policy_name = self._container_netpol_name(instance_id)
        if normalized_mode in {"unrestricted", "host"}:
            try:
                networking.delete_namespaced_network_policy(name=policy_name, namespace=ns)
            except ApiException as exc:
                if exc.status != 404:
                    logger.error("Failed to delete container network policy for %s: %s", pod_name, exc)
                    raise
            return
        policy = self.desired_container_network_policy(
            instance_id,
            pod_name,
            ns,
            app_port,
            mode=normalized_mode,
        )
        try:
            networking.create_namespaced_network_policy(namespace=ns, body=policy)
        except ApiException as exc:
            if exc.status == 409:
                try:
                    networking.patch_namespaced_network_policy(
                        name=policy.metadata.name,
                        namespace=ns,
                        body={"spec": policy.spec},
                    )
                except ApiException as patch_exc:
                    logger.error("Failed to update container network policy for %s: %s", pod_name, patch_exc)
                    raise
            else:
                logger.error("Failed to apply container network policy for %s: %s", pod_name, exc)
                raise

    def desired_container_network_policy(
        self,
        instance_id: str,
        pod_name: str,
        namespace: str,
        app_port: int,
        mode: str = "bridge",
    ) -> client.V1NetworkPolicy:
        normalized_mode = str(mode or "bridge").strip().lower()
        ingress_rule = client.V1NetworkPolicyIngressRule(
            ports=[
                client.V1NetworkPolicyPort(
                    protocol="TCP",
                    port=max(1, min(65535, int(app_port))),
                )
            ],
        )
        egress_rules: list[client.V1NetworkPolicyEgressRule] = []
        if normalized_mode not in {"isolated", "none"}:
            egress_ports = [
                client.V1NetworkPolicyPort(protocol="TCP", port=53),
                client.V1NetworkPolicyPort(protocol="UDP", port=53),
                client.V1NetworkPolicyPort(protocol="TCP", port=443),
                client.V1NetworkPolicyPort(protocol="TCP", port=80),
            ]
            egress_rules = [client.V1NetworkPolicyEgressRule(ports=egress_ports)]
        return client.V1NetworkPolicy(
            api_version="networking.k8s.io/v1",
            kind="NetworkPolicy",
            metadata=client.V1ObjectMeta(name=self._container_netpol_name(instance_id), namespace=namespace),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={"app": pod_name}),
                policy_types=["Ingress", "Egress"],
                ingress=[ingress_rule],
                egress=egress_rules,
            ),
        )

    def _find_pod_name(self, instance_id: str, owner: str) -> str:
        # In this simplified mapping, pod name is derived deterministically from owner + instance id.
        return f"vm-{self._safe_owner(owner)}-{instance_id[:8]}"

    def _find_container_pod_name(self, instance_id: str, owner: str) -> str:
        return self._container_pod_name(instance_id, owner)

    def _cleanup_orphan_container_services(self, session: Session) -> None:
        core = self._client()
        active_rows = session.exec(
            select(ContainerInstance).where(ContainerInstance.status.in_(["pending", "running"]))
        ).all()
        active_by_namespace: dict[str, set[str]] = {}
        for row in active_rows:
            ns = self._namespace(getattr(row, "namespace", None))
            active_by_namespace.setdefault(ns, set()).add(self._container_service_name(row.id))
        if not active_by_namespace:
            active_by_namespace[self._namespace()] = set()
        for namespace, active_service_names in active_by_namespace.items():
            try:
                services = core.list_namespaced_service(namespace=namespace).items
            except Exception:
                logger.warning(
                    "Failed to list services during orphan ctsvc cleanup in %s",
                    namespace,
                    exc_info=True,
                )
                continue
            for svc in services:
                name = str(getattr(getattr(svc, "metadata", None), "name", "") or "")
                if not name.startswith("ctsvc-"):
                    continue
                if name in active_service_names:
                    continue
                try:
                    core.delete_namespaced_service(name=name, namespace=namespace)
                    logger.info("Deleted orphaned container service %s in %s", name, namespace)
                except ApiException as exc:
                    if exc.status != 404:
                        logger.warning("Failed deleting orphaned container service %s in %s: %s", name, namespace, exc)
                except Exception:
                    logger.warning(
                        "Failed deleting orphaned container service %s in %s", name, namespace, exc_info=True
                    )

    def reaper_tick(self, session: Session) -> None:
        config_row = session.get(Config, 1) or Config()
        templates = {t.id: t for t in session.exec(select(Template)).all()}
        container_templates = {t.id: t for t in session.exec(select(ContainerTemplate)).all()}
        images = {img.id: img for img in session.exec(select(Image)).all()}
        now = utc_now()
        stale_instances: list[Instance] = []
        stale_container_instances: list[ContainerInstance] = []
        for inst in session.exec(select(Instance).where(Instance.status == "running")).all():
            tmpl = templates.get(inst.template_id)
            timeout_minutes = (
                getattr(tmpl, "idle_timeout_minutes", None)
                or config_row.idle_timeout_minutes
                or settings.idle_timeout_minutes
            )
            cutoff = now - timedelta(minutes=timeout_minutes)
            if inst.last_active_at < cutoff:
                stale_instances.append(inst)
        for inst in session.exec(select(ContainerInstance).where(ContainerInstance.status == "running")).all():
            tmpl = container_templates.get(inst.template_id)
            timeout_minutes = (
                getattr(tmpl, "idle_timeout_minutes", None)
                or config_row.idle_timeout_minutes
                or settings.idle_timeout_minutes
            )
            cutoff = now - timedelta(minutes=timeout_minutes)
            if inst.last_active_at < cutoff:
                stale_container_instances.append(inst)
        for inst in stale_instances:
            try:
                self.delete_pod(
                    inst.id,
                    inst.owner,
                    disk_pvc=inst.disk_pvc,
                    namespace=str(getattr(inst, "namespace", "") or self._namespace()),
                )
            except Exception:
                logger.warning("Failed to delete pod for instance %s during reaper", inst.id)
            session.delete(inst)
        for inst in stale_container_instances:
            try:
                ns = str(getattr(inst, "namespace", "") or self._namespace())
                self.delete_container_pod(inst.id, inst.owner, namespace=ns)
                self.delete_container_service(inst.id, namespace=ns)
            except Exception:
                logger.warning("Failed to delete container workload %s during reaper", inst.id, exc_info=True)
            session.delete(inst)
        self._cleanup_orphan_container_services(session)
        recent_cutoff = now - timedelta(minutes=max(1, int(settings.warm_pool_window_minutes)))
        recent_launches: dict[str, int] = {}
        for template_id in session.exec(select(Instance.template_id).where(Instance.started_at >= recent_cutoff)).all():
            recent_launches[template_id] = recent_launches.get(template_id, 0) + 1
        for tmpl in templates.values():
            min_pool = int(getattr(tmpl, "preclone_pool_size", 0) or 0)
            max_pool = int(getattr(tmpl, "preclone_pool_max", min_pool) or min_pool)
            desired = self._autoscaled_warm_pool_target(min_pool, max_pool, recent_launches.get(tmpl.id, 0))
            if not tmpl.enabled:
                continue
            image = images.get(tmpl.image_id)
            if not image or not image.source_pvc:
                continue
            try:
                self.ensure_warm_pool(tmpl.id, image.source_pvc, desired)
            except Exception:
                logger.warning("Failed to reconcile warm pool for template %s", tmpl.id, exc_info=True)
        if stale_instances or stale_container_instances:
            session.commit()

    def ensure_namespace(self, namespace: str) -> None:
        core = self._client()
        try:
            core.read_namespace(name=namespace)
        except ApiException as exc:
            if exc.status == 404:
                if settings.kube_auto_create_namespace:
                    ns_body = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
                    core.create_namespace(body=ns_body)
                    return
                raise RuntimeError(
                    f"Kubernetes namespace {namespace} not found. Create it first or set BLABS_KUBE_AUTO_CREATE_NAMESPACE=true."
                )
            raise


kube = KubernetesService()
