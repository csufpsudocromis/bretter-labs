"""
Kubernetes integration helpers.

Creates/stops/deletes VM pods, applies egress-only NetworkPolicies, and generates console URLs.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.stream import stream
from kubernetes.client import ApiException
from sqlmodel import Session, select

from ..config import settings
from ..tables import Config, Instance, Template

logger = logging.getLogger(__name__)


@dataclass
class PodRequest:
    instance_id: str
    template_id: str
    image_path: str
    os_type: str
    cpu_cores: int
    ram_mb: int
    owner: str
    network_mode: str = "default"


@dataclass
class PodStatus:
    instance_id: str
    phase: str
    node: Optional[str] = None
    message: Optional[str] = None
    console_endpoint: Optional[str] = None


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
        # Give QEMU some headroom above the guest RAM to avoid cgroup OOM kills from host overhead.
        mem_limit_mb = req.ram_mb + 2048
        metadata = client.V1ObjectMeta(
            name=pod_name,
            labels={"app": pod_name, "owner": req.owner, "instance": req.instance_id},
        )
        resources = client.V1ResourceRequirements(
            limits={"cpu": str(req.cpu_cores), "memory": f"{mem_limit_mb}Mi"},
            requests={"cpu": str(req.cpu_cores), "memory": f"{req.ram_mb}Mi"},
        )
        volume_mounts = [
            client.V1VolumeMount(name="images", mount_path="/images", read_only=True),
            client.V1VolumeMount(name="data", mount_path="/data", read_only=False),
        ]
        volumes = [
            client.V1Volume(
                name="images",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=settings.kube_image_pvc),
            ),
            client.V1Volume(name="data", empty_dir=client.V1EmptyDirVolumeSource()),
        ]
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
        is_linux = req.os_type.lower() == "linux"
        # For Linux VHDs, convert to raw for better kernel/EFI support; Windows copies as-is.
        dest_disk = f"/data/{Path(req.image_path).name}"
        drive_if = "ide"
        vga = "std" if is_linux else "qxl"
        suffix = Path(req.image_path).suffix.lower()
        # Use the native disk format for both Linux and Windows.
        disk_format = None
        if is_linux:
            drive_if = "ide"
        if suffix in {".vhd", ".vhdx"}:
            disk_format = "vpc"
        elif suffix in {".qcow", ".qcow2"}:
            disk_format = "qcow2"
        elif suffix == ".raw":
            disk_format = "raw"
        elif suffix == ".vdi":
            disk_format = "vdi"
        env_vars = [
            client.V1EnvVar(name="CPU_CORES", value=str(req.cpu_cores)),
            client.V1EnvVar(name="RAM_MB", value=str(req.ram_mb)),
            client.V1EnvVar(name="OS_TYPE", value=req.os_type.lower()),
            client.V1EnvVar(name="DRIVE_IF", value=drive_if),
            client.V1EnvVar(name="VGA_TYPE", value=vga),
            client.V1EnvVar(name="MACHINE_TYPE", value="q35"),
            # Linux images are UEFI; enable EFI for both OS types.
            client.V1EnvVar(name="EFI_ENABLED", value="true"),
        ]
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
            security_context=client.V1SecurityContext(privileged=settings.kube_use_kvm),
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
        }
        if settings.image_pull_secret:
            spec_kwargs["image_pull_secrets"] = [client.V1LocalObjectReference(name=settings.image_pull_secret)]
        init_cmd = f"cp /images/{req.image_path} {dest_disk} && sync"
        init_container = client.V1Container(
            name="prepare-disk",
            image="busybox:1.36",
            command=["/bin/sh", "-c", init_cmd],
            volume_mounts=[
                client.V1VolumeMount(name="images", mount_path="/images", read_only=True),
                client.V1VolumeMount(name="data", mount_path="/data", read_only=False),
            ],
        )
        spec_kwargs["init_containers"] = [init_container]
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
            return PodStatus(instance_id=req.instance_id, phase="Pending", console_endpoint=self._console_url(req))
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

    def delete_pod(self, instance_id: str, owner: str) -> None:
        core = self._client()
        pod_name = self._find_pod_name(instance_id, owner)
        try:
            core.delete_namespaced_pod(
                name=pod_name, namespace=settings.kube_namespace, grace_period_seconds=0, propagation_policy="Foreground"
            )
        except ApiException as exc:
            if exc.status == 404:
                return
            logger.error("Failed to delete pod %s: %s", pod_name, exc)
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
                self.delete_pod(inst.id, inst.owner)
            except Exception:
                logger.warning("Failed to delete pod for instance %s during reaper", inst.id)
            session.delete(inst)
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
