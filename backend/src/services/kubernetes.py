"""
Kubernetes integration helpers.

Creates/stops/deletes VM pods, applies egress-only NetworkPolicies, and generates console URLs.
"""

import logging
import math
import re
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
from ..tables import Config, Image, Instance, Template

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
    network_mode: str = "default"
    instance_disk_pvc: Optional[str] = None


@dataclass
class PodStatus:
    instance_id: str
    phase: str
    node: Optional[str] = None
    message: Optional[str] = None
    console_endpoint: Optional[str] = None
    disk_pvc: Optional[str] = None


class KubernetesService:
    def __init__(self) -> None:
        self._core = None
        self._networking = None

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

    def _pod_name(self, req: PodRequest) -> str:
        return f"vm-{req.owner}-{req.instance_id[:8]}"

    def _instance_disk_pvc_name(self, instance_id: str, owner: str) -> str:
        safe_owner = re.sub(r"[^a-z0-9-]+", "-", owner.lower()).strip("-")
        if not safe_owner:
            safe_owner = "user"
        return f"vm-disk-{safe_owner[:20]}-{instance_id[:8]}"

    def _instance_service_name(self, instance_id: str) -> str:
        return f"svc-{instance_id[:8]}"

    def _instance_netpol_name(self, instance_id: str, owner: str) -> str:
        return f"{self._find_pod_name(instance_id, owner)}-egress-only"

    def _pool_pvc_name(self, template_id: str) -> str:
        return f"pool-{template_id[:8]}-{uuid4().hex[:6]}"

    def _ensure_instance_disk_pvc(self, req: PodRequest) -> str:
        core = self._client()
        if req.instance_disk_pvc:
            existing = core.read_namespaced_persistent_volume_claim(
                name=req.instance_disk_pvc,
                namespace=settings.kube_namespace,
            )
            phase = (existing.status.phase or "").lower()
            if phase == "lost":
                raise RuntimeError(f"instance PVC {req.instance_disk_pvc} entered Lost phase")
            return req.instance_disk_pvc
        if not req.image_source_pvc:
            raise RuntimeError("image source PVC is required for clone-based VM launch")

        pvc_name = self._instance_disk_pvc_name(req.instance_id, req.owner)
        try:
            existing = core.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=settings.kube_namespace)
            # A restart can race with PVC deletion. If we reuse a claim that is terminating,
            # the pod references a missing claim and remains Pending indefinitely.
            if existing.metadata and existing.metadata.deletion_timestamp:
                deadline = time.time() + 90
                while time.time() < deadline:
                    try:
                        core.read_namespaced_persistent_volume_claim(name=pvc_name, namespace=settings.kube_namespace)
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

        source = core.read_namespaced_persistent_volume_claim(name=req.image_source_pvc, namespace=settings.kube_namespace)
        source_request = None
        if source.spec and source.spec.resources and source.spec.resources.requests:
            source_request = source.spec.resources.requests.get("storage")
        if not source_request:
            raise RuntimeError(f"source PVC {req.image_source_pvc} has no storage request")

        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels={"owner": req.owner, "instance": req.instance_id, "app.kubernetes.io/part-of": "bretter-labs"},
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=(settings.kube_vm_storage_class or source.spec.storage_class_name or None),
                resources=client.V1ResourceRequirements(requests={"storage": source_request}),
                data_source=client.V1TypedLocalObjectReference(
                    api_group="",
                    kind="PersistentVolumeClaim",
                    name=req.image_source_pvc,
                ),
            ),
        )
        core.create_namespaced_persistent_volume_claim(namespace=settings.kube_namespace, body=body)
        return pvc_name

    def reserve_warm_pool_pvc(self, template_id: str, instance_id: str, owner: str) -> Optional[str]:
        core = self._client()
        selector = f"blabs-pool=true,template-id={template_id},pool-state=ready"
        items = core.list_namespaced_persistent_volume_claim(namespace=settings.kube_namespace, label_selector=selector).items
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
                    namespace=settings.kube_namespace,
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
        selector = f"blabs-pool=true,template-id={template_id},pool-state=ready"
        ready_pool = core.list_namespaced_persistent_volume_claim(
            namespace=settings.kube_namespace,
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
                        namespace=settings.kube_namespace,
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        logger.warning("Failed to delete warm pool PVC %s", pvc.metadata.name, exc_info=True)
            return
        if current >= desired:
            return

        source = core.read_namespaced_persistent_volume_claim(name=image_source_pvc, namespace=settings.kube_namespace)
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
                    data_source=client.V1TypedLocalObjectReference(
                        api_group="",
                        kind="PersistentVolumeClaim",
                        name=image_source_pvc,
                    ),
                ),
            )
            try:
                core.create_namespaced_persistent_volume_claim(namespace=settings.kube_namespace, body=body)
            except ApiException as exc:
                if exc.status != 409:
                    raise

    def create_service_for_pod(self, pod_name: str, service_name: str) -> int:
        core = self._client()
        body = client.V1Service(
            metadata=client.V1ObjectMeta(name=service_name, labels={"app": pod_name}),
            spec=client.V1ServiceSpec(
                selector={"app": pod_name},
                type="NodePort",
                ports=[client.V1ServicePort(port=6080, target_port=6080, protocol="TCP")],
            ),
        )
        try:
            svc = core.create_namespaced_service(namespace=settings.kube_namespace, body=body)
            # Fetch assigned nodePort
            return svc.spec.ports[0].node_port
        except ApiException as exc:
            if exc.status != 409:
                logger.error("Failed to create service %s: %s", service_name, exc)
                raise
            # If already exists, fetch existing
            existing = core.read_namespaced_service(name=service_name, namespace=settings.kube_namespace)
            return existing.spec.ports[0].node_port

    def _console_url(self, req: PodRequest) -> str:
        return ""

    def create_pod(self, req: PodRequest) -> PodStatus:
        core = self._client()
        pod_name = self._pod_name(req)
        self.ensure_namespace(settings.kube_namespace)
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
        vga = "std" if is_linux else "qxl"
        suffix = Path(req.image_path).suffix.lower()
        # Use the native disk format for both Linux and Windows.
        disk_format = None
        if suffix in {".vhd", ".vhdx"}:
            disk_format = "vpc"
        elif suffix in {".qcow", ".qcow2"}:
            disk_format = "qcow2"
        elif suffix == ".raw":
            disk_format = "raw"
        elif suffix == ".vdi":
            disk_format = "vdi"
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
        ]
        if tls_secret_name:
            env_vars.extend(
                [
                    client.V1EnvVar(name="TLS_CERT_FILE", value="/tls/tls.crt"),
                    client.V1EnvVar(name="TLS_KEY_FILE", value="/tls/tls.key"),
                ]
            )
        if disk_format:
            env_vars.append(client.V1EnvVar(name="DISK_FORMAT", value=disk_format))
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
                privileged=(settings.kube_use_kvm or settings.vm_net_backend == "tap-nat")
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
        host_network = (req.network_mode or "bridge") == "host"
        spec_kwargs = {
            "containers": [container],
            "restart_policy": "Never",
            "volumes": volumes,
            "host_network": host_network,
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
                    label_selector=client.V1LabelSelector(
                        match_labels={"app.kubernetes.io/component": "vm-runner"}
                    ),
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
            core.create_namespaced_pod(namespace=settings.kube_namespace, body=body)
            if (req.network_mode or "bridge") not in {"unrestricted", "host"}:
                self.apply_network_policy(pod_name, mode=req.network_mode or "bridge")
            return PodStatus(
                instance_id=req.instance_id,
                phase="Pending",
                console_endpoint=self._console_url(req),
                disk_pvc=instance_disk_pvc,
            )
        except ApiException as exc:
            logger.error("Failed to create pod: %s", exc)
            raise

    def stop_pod(self, instance_id: str, owner: str) -> PodStatus:
        core = self._client()
        pod_name = self._find_pod_name(instance_id, owner)
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=settings.kube_namespace)
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
                namespace=settings.kube_namespace,
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

    def delete_pod(self, instance_id: str, owner: str, disk_pvc: Optional[str] = None) -> None:
        core = self._client()
        networking = self._networking_client()
        pod_name = self._find_pod_name(instance_id, owner)
        pvc_name = disk_pvc or self._instance_disk_pvc_name(instance_id, owner)
        service_name = self._instance_service_name(instance_id)
        netpol_name = self._instance_netpol_name(instance_id, owner)
        try:
            core.delete_namespaced_service(name=service_name, namespace=settings.kube_namespace)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete service %s: %s", service_name, exc)
                raise
        try:
            networking.delete_namespaced_network_policy(name=netpol_name, namespace=settings.kube_namespace)
        except ApiException as exc:
            if exc.status != 404:
                logger.error("Failed to delete network policy %s: %s", netpol_name, exc)
                raise
        try:
            core.delete_namespaced_pod(
                name=pod_name, namespace=settings.kube_namespace, grace_period_seconds=0, propagation_policy="Foreground"
            )
        except ApiException as exc:
            if exc.status == 404:
                pass
            else:
                logger.error("Failed to delete pod %s: %s", pod_name, exc)
                raise
        try:
            core.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace=settings.kube_namespace)
        except ApiException as exc:
            if exc.status == 404:
                return
            logger.error("Failed to delete instance PVC %s: %s", pvc_name, exc)
            raise

    def get_status(self, instance_id: str, owner: str) -> PodStatus:
        core = self._client()
        pod_name = self._find_pod_name(instance_id, owner)
        try:
            pod = core.read_namespaced_pod(name=pod_name, namespace=settings.kube_namespace)
            phase = pod.status.phase or "Unknown"
            node = pod.spec.node_name
            message = pod.status.message
            return PodStatus(instance_id=instance_id, phase=phase, node=node, message=message)
        except ApiException as exc:
            logger.error("Failed to read pod %s: %s", pod_name, exc)
            raise

    def apply_network_policy(self, pod_name: str, mode: str = "default") -> None:
        networking = self._networking_client()
        policy = self.desired_network_policy(pod_name, settings.kube_namespace, mode=mode)
        try:
            networking.create_namespaced_network_policy(namespace=settings.kube_namespace, body=policy)
        except ApiException as exc:
            if exc.status == 409:
                try:
                    networking.patch_namespaced_network_policy(
                        name=policy.metadata.name,
                        namespace=settings.kube_namespace,
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
        ingress_rule = client.V1NetworkPolicyIngressRule(
            ports=[
                client.V1NetworkPolicyPort(protocol="TCP", port=6080),
            ],
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

    def _find_pod_name(self, instance_id: str, owner: str) -> str:
        # In this simplified mapping, pod name is derived deterministically from owner + instance id.
        return f"vm-{owner}-{instance_id[:8]}"

    def reaper_tick(self, session: Session) -> None:
        config_row = session.get(Config, 1) or Config()
        templates = {t.id: t for t in session.exec(select(Template)).all()}
        images = {img.id: img for img in session.exec(select(Image)).all()}
        now = datetime.utcnow()
        stale_instances: list[Instance] = []
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
        for inst in stale_instances:
            try:
                self.delete_pod(inst.id, inst.owner, disk_pvc=inst.disk_pvc)
            except Exception:
                logger.warning("Failed to delete pod for instance %s during reaper", inst.id)
            session.delete(inst)
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
        if stale_instances:
            session.commit()

    def ensure_namespace(self, namespace: str) -> None:
        core = self._client()
        try:
            core.read_namespace(name=namespace)
        except ApiException as exc:
            if exc.status == 404:
                ns_body = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
                core.create_namespace(body=ns_body)
            else:
                raise


kube = KubernetesService()
