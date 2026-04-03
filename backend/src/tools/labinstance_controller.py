from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException
from sqlmodel import select

from ..config import settings
from ..console_providers import normalize_vm_console_provider
from ..db import session_scope
from ..network_modes import normalize_vm_network_mode
from ..secret_codec import decrypt_secret
from ..services.kubernetes import PodRequest, kube
from ..tables import Image, Instance, Template

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("labinstance-controller")


def _ts() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_condition(
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


class _Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reconcile_ok_total = 0
        self.reconcile_error_total = 0
        self.reconcile_seconds_sum = 0.0
        self.reconcile_seconds_count = 0
        self.labinstances_total = 0
        self.finalizer_backlog = 0
        self.stuck_instances = 0
        self.last_cycle_unix = 0
        self.last_liveness_unix = int(time.time())
        self.ready = 0
        self.leader = 0

    def observe(self, result: str, seconds: float) -> None:
        with self._lock:
            if result == "ok":
                self.reconcile_ok_total += 1
            else:
                self.reconcile_error_total += 1
            self.reconcile_seconds_sum += max(0.0, float(seconds))
            self.reconcile_seconds_count += 1

    def set_cycle(self, *, total: int, finalizer_backlog: int, stuck_instances: int) -> None:
        with self._lock:
            self.labinstances_total = max(0, int(total))
            self.finalizer_backlog = max(0, int(finalizer_backlog))
            self.stuck_instances = max(0, int(stuck_instances))
            self.last_cycle_unix = int(time.time())

    def touch_liveness(self) -> None:
        with self._lock:
            self.last_liveness_unix = int(time.time())

    def set_health(self, *, ready: bool, leader: bool) -> None:
        with self._lock:
            self.ready = 1 if ready else 0
            self.leader = 1 if leader else 0

    def is_live(self, *, stale_after_seconds: int = 180) -> bool:
        with self._lock:
            return int(time.time()) - self.last_liveness_unix <= max(30, int(stale_after_seconds or 180))

    def is_ready(self) -> bool:
        with self._lock:
            return self.ready == 1

    def render_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP blabs_labinstance_reconcile_total Total LabInstance reconciliations by result.",
                "# TYPE blabs_labinstance_reconcile_total counter",
                f'blabs_labinstance_reconcile_total{{result="ok"}} {self.reconcile_ok_total}',
                f'blabs_labinstance_reconcile_total{{result="error"}} {self.reconcile_error_total}',
                "# HELP blabs_labinstance_reconcile_duration_seconds Reconcile duration summary.",
                "# TYPE blabs_labinstance_reconcile_duration_seconds summary",
                f"blabs_labinstance_reconcile_duration_seconds_sum {self.reconcile_seconds_sum:.6f}",
                f"blabs_labinstance_reconcile_duration_seconds_count {self.reconcile_seconds_count}",
                "# HELP blabs_labinstance_objects Total LabInstance objects seen in latest cycle.",
                "# TYPE blabs_labinstance_objects gauge",
                f"blabs_labinstance_objects {self.labinstances_total}",
                "# HELP blabs_labinstance_finalizer_backlog LabInstances marked for deletion but still carrying finalizer.",
                "# TYPE blabs_labinstance_finalizer_backlog gauge",
                f"blabs_labinstance_finalizer_backlog {self.finalizer_backlog}",
                "# HELP blabs_labinstance_stuck_instances LabInstances stuck in pending/start phases beyond threshold.",
                "# TYPE blabs_labinstance_stuck_instances gauge",
                f"blabs_labinstance_stuck_instances {self.stuck_instances}",
                "# HELP blabs_labinstance_last_reconcile_unix Latest successful reconcile cycle timestamp.",
                "# TYPE blabs_labinstance_last_reconcile_unix gauge",
                f"blabs_labinstance_last_reconcile_unix {self.last_cycle_unix}",
                "# HELP blabs_labinstance_controller_ready Controller readiness state.",
                "# TYPE blabs_labinstance_controller_ready gauge",
                f"blabs_labinstance_controller_ready {self.ready}",
                "# HELP blabs_labinstance_controller_leader Controller lease leadership state.",
                "# TYPE blabs_labinstance_controller_leader gauge",
                f"blabs_labinstance_controller_leader {self.leader}",
                "",
            ]
            return "\n".join(lines)


class _MetricsHandler(BaseHTTPRequestHandler):
    metrics: _Metrics

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/livez", "/livez/"}:
            status_code = 200 if self.metrics.is_live() else 503
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n" if status_code == 200 else b"stale\n")
            return
        if path in {"/readyz", "/readyz/"}:
            status_code = 200 if self.metrics.is_ready() else 503
            self.send_response(status_code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ready\n" if status_code == 200 else b"not-ready\n")
            return
        if path not in {"/metrics", "/metrics/"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = self.metrics.render_prometheus().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("metrics http: " + fmt, *args)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _LeaderElector:
    def __init__(
        self,
        *,
        enabled: bool,
        namespace: str,
        lease_name: str,
        identity: str,
        lease_duration_seconds: int,
        retry_period_seconds: int,
    ) -> None:
        self.enabled = enabled
        self.namespace = namespace
        self.lease_name = lease_name
        self.identity = identity
        self.lease_duration_seconds = max(15, int(lease_duration_seconds or 30))
        self.retry_period_seconds = max(2, int(retry_period_seconds or 5))
        self._coord: client.CoordinationV1Api | None = None
        self._last_state: bool | None = None

    def bind_client(self, coord: client.CoordinationV1Api) -> None:
        self._coord = coord

    @staticmethod
    def _normalize_time(raw: Any) -> datetime | None:
        if isinstance(raw, datetime):
            return raw.astimezone(UTC) if raw.tzinfo else raw.replace(tzinfo=UTC)
        value = str(raw or "").strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def _is_lease_expired(self, lease: client.V1Lease, now_utc: datetime) -> bool:
        spec = lease.spec or client.V1LeaseSpec()
        renew_time = self._normalize_time(spec.renew_time)
        if renew_time is None:
            renew_time = self._normalize_time(spec.acquire_time)
        if renew_time is None:
            return True
        duration = int(spec.lease_duration_seconds or self.lease_duration_seconds)
        return now_utc > (renew_time + timedelta(seconds=max(1, duration)))

    @staticmethod
    def _fmt_micro(now: datetime) -> str:
        return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _build_spec(
        self,
        *,
        previous: client.V1Lease | None,
        now: datetime,
    ) -> dict[str, Any]:
        now_micro = self._fmt_micro(now)
        prev_spec = previous.spec if previous and previous.spec else client.V1LeaseSpec()
        previous_holder = str(prev_spec.holder_identity or "").strip()
        transitions = int(prev_spec.lease_transitions or 0)
        if previous_holder and previous_holder != self.identity:
            transitions += 1
        acquire_time_obj = self._normalize_time(prev_spec.acquire_time)
        acquire_time = self._fmt_micro(acquire_time_obj) if acquire_time_obj is not None else ""
        if not previous_holder or previous_holder != self.identity or not acquire_time:
            acquire_time = now_micro
        return {
            "holderIdentity": self.identity,
            "acquireTime": acquire_time,
            "renewTime": now_micro,
            "leaseDurationSeconds": self.lease_duration_seconds,
            "leaseTransitions": transitions,
        }

    def _log_state(self, is_leader: bool) -> None:
        if self._last_state is None or self._last_state != is_leader:
            if is_leader:
                logger.info("Leader election: acquired lease %s/%s", self.namespace, self.lease_name)
            else:
                logger.info("Leader election: standby mode for lease %s/%s", self.namespace, self.lease_name)
        self._last_state = is_leader

    def should_run_reconcile(self) -> bool:
        if not self.enabled:
            return True
        if self._coord is None:
            raise RuntimeError("leader elector client is not bound")
        now = datetime.now(UTC)
        try:
            lease = self._coord.read_namespaced_lease(name=self.lease_name, namespace=self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            body = {
                "apiVersion": "coordination.k8s.io/v1",
                "kind": "Lease",
                "metadata": {"name": self.lease_name, "namespace": self.namespace},
                "spec": self._build_spec(previous=None, now=now),
            }
            try:
                self._coord.create_namespaced_lease(namespace=self.namespace, body=body)
                self._log_state(True)
                return True
            except ApiException as create_exc:
                if create_exc.status not in {409, 422}:
                    raise
                self._log_state(False)
                return False

        holder = str((lease.spec or client.V1LeaseSpec()).holder_identity or "").strip()
        expired = self._is_lease_expired(lease, now)
        if holder and holder != self.identity and not expired:
            self._log_state(False)
            return False

        body = {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": {
                "name": self.lease_name,
                "namespace": self.namespace,
                "resourceVersion": (lease.metadata.resource_version if lease.metadata else None),
            },
            "spec": self._build_spec(previous=lease, now=now),
        }
        try:
            self._coord.replace_namespaced_lease(name=self.lease_name, namespace=self.namespace, body=body)
            self._log_state(True)
            return True
        except ApiException as exc:
            if exc.status not in {409, 422}:
                raise
            self._log_state(False)
            return False


class LabInstanceController:
    def __init__(self, metrics: _Metrics) -> None:
        self.metrics = metrics
        self.namespace = settings.kube_namespace
        self.group = str(settings.labinstance_crd_group or "labs.bretter.io").strip()
        self.version = str(settings.labinstance_crd_version or "v1alpha1").strip()
        self.plural = str(settings.labinstance_crd_plural or "labinstances").strip()
        self.finalizer = str(settings.labinstance_crd_finalizer or "labs.bretter.io/finalizer").strip()
        self.dry_run = bool(getattr(settings, "labinstance_controller_dry_run", False))
        self.poll_seconds = max(3, int(settings.labinstance_controller_poll_seconds or 15))
        self.stuck_seconds = max(60, int(settings.labinstance_controller_stuck_seconds or 600))
        self.leader_retry_seconds = max(2, int(settings.labinstance_controller_retry_period_seconds or 5))

        self._custom: client.CustomObjectsApi | None = None
        self._coord: client.CoordinationV1Api | None = None
        identity = f"{socket.gethostname()}-{os.getpid()}"
        self._leader_elector = _LeaderElector(
            enabled=bool(getattr(settings, "labinstance_controller_leader_election_enabled", True)),
            namespace=self.namespace,
            lease_name=str(
                getattr(settings, "labinstance_controller_lease_name", "bretter-labinstance-controller-leader")
                or "bretter-labinstance-controller-leader"
            ).strip(),
            identity=identity,
            lease_duration_seconds=int(getattr(settings, "labinstance_controller_lease_duration_seconds", 30) or 30),
            retry_period_seconds=self.leader_retry_seconds,
        )

    def _load_kube(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._custom = client.CustomObjectsApi()
        self._coord = client.CoordinationV1Api()
        self._leader_elector.bind_client(self._coord)
        kube._client()

    def _api(self) -> client.CustomObjectsApi:
        if self._custom is None:
            self._load_kube()
        assert self._custom is not None
        return self._custom

    def _list(self) -> list[dict[str, Any]]:
        payload = self._api().list_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=self.namespace,
            plural=self.plural,
        )
        items = payload.get("items")
        return list(items) if isinstance(items, list) else []

    def _patch(self, name: str, body: dict[str, Any]) -> None:
        self._api().patch_namespaced_custom_object(
            group=self.group,
            version=self.version,
            namespace=self.namespace,
            plural=self.plural,
            name=name,
            body=body,
        )

    def _patch_status(self, name: str, body: dict[str, Any]) -> None:
        self._api().patch_namespaced_custom_object_status(
            group=self.group,
            version=self.version,
            namespace=self.namespace,
            plural=self.plural,
            name=name,
            body={"status": body},
        )

    def _ensure_finalizer(self, name: str, metadata: dict[str, Any]) -> None:
        if not self.finalizer:
            return
        finalizers = list(metadata.get("finalizers") or [])
        if self.finalizer in finalizers:
            return
        finalizers.append(self.finalizer)
        self._patch(name, {"metadata": {"finalizers": finalizers}})

    def _remove_finalizer(self, name: str, metadata: dict[str, Any]) -> None:
        if not self.finalizer:
            return
        finalizers = [item for item in list(metadata.get("finalizers") or []) if item != self.finalizer]
        self._patch(name, {"metadata": {"finalizers": finalizers}})

    @staticmethod
    def _phase_from_pod(phase: str) -> str:
        normalized = str(phase or "").strip().lower()
        if normalized == "running":
            return "Running"
        if normalized == "pending":
            return "Pending"
        if normalized == "succeeded":
            return "Completed"
        if normalized == "failed":
            return "Failed"
        return "Unknown"

    def _resolve_template_image(self, template_id: str) -> tuple[Template | None, Image | None]:
        with session_scope() as session:
            template = session.get(Template, template_id)
            if not template:
                return None, None
            image = session.get(Image, template.image_id)
            return template, image

    def _update_instance_db_status(self, instance_id: str, phase: str) -> None:
        status_map = {
            "Running": "running",
            "Pending": "pending",
            "Completed": "completed",
            "Stopped": "stopped",
            "Failed": "failed",
        }
        status_value = status_map.get(phase, "pending")
        try:
            with session_scope() as session:
                row = session.get(Instance, instance_id)
                if not row:
                    return
                if str(row.status or "") == status_value:
                    return
                row.status = status_value
                session.add(row)
                session.commit()
        except Exception:
            logger.warning("Unable to persist DB status for LabInstance %s", instance_id, exc_info=True)

    def _resolve_template_rdp_defaults(self, template: Template) -> tuple[str | None, str | None]:
        username = str(getattr(template, "rdp_default_username", "") or "").strip()[:128]
        encrypted_password = str(getattr(template, "rdp_default_password", "") or "")
        if not encrypted_password:
            return username or None, None
        try:
            password = decrypt_secret(encrypted_password).strip()
        except Exception:
            logger.warning("Failed to decrypt template RDP password for template=%s", template.id, exc_info=True)
            return username or None, None
        return username or None, password or None

    def _reconcile_vm_running(
        self,
        *,
        name: str,
        owner: str,
        template_id: str,
        metadata: dict[str, Any],
    ) -> None:
        if self.dry_run:
            self._patch_status(
                name,
                {
                    "observedGeneration": int(metadata.get("generation") or 0),
                    "phase": "Running",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="True",
                            reason="DryRunLifecycle",
                            message="Controller dry-run mode marked LabInstance Running.",
                        )
                    ],
                },
            )
            self._update_instance_db_status(name, "Running")
            return

        template, image = self._resolve_template_image(template_id)
        if template is None:
            self._patch_status(
                name,
                {
                    "phase": "Failed",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="False",
                            reason="TemplateNotFound",
                            message=f"Template {template_id} was not found.",
                        )
                    ],
                },
            )
            return
        if image is None:
            self._patch_status(
                name,
                {
                    "phase": "Failed",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="False",
                            reason="ImageNotFound",
                            message=f"Image {template.image_id} was not found.",
                        )
                    ],
                },
            )
            return
        if not image.source_pvc:
            self._patch_status(
                name,
                {
                    "phase": "Failed",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="False",
                            reason="ImageCloneSourceMissing",
                            message="Template image has no source PVC for clone-based launch.",
                        )
                    ],
                },
            )
            return

        instance_id = name
        try:
            pod_status = kube.get_status(instance_id, owner)
        except ApiException as exc:
            if exc.status != 404:
                raise
            console_provider = normalize_vm_console_provider(getattr(template, "console_provider", "spice"))
            rdp_default_username, rdp_default_password = (None, None)
            if console_provider == "guacamole_rdp":
                rdp_default_username, rdp_default_password = self._resolve_template_rdp_defaults(template)
            pod_request = PodRequest(
                instance_id=instance_id,
                template_id=template.id,
                image_path=str(image.filename),
                image_source_pvc=image.source_pvc,
                os_type=template.os_type,
                cpu_cores=template.cpu_cores,
                ram_mb=template.ram_mb,
                owner=owner,
                network_mode=normalize_vm_network_mode(getattr(template, "network_mode", "bridge")),
                console_provider=console_provider,
                rdp_default_username=rdp_default_username,
                rdp_default_password=rdp_default_password,
                installer_iso_filename=(str(getattr(image, "installer_iso_filename", "") or "").strip() or None),
                boot_order=(
                    "dc"
                    if str(getattr(image, "source_kind", "") or "").strip().lower() == "scratch"
                    and str(getattr(image, "installer_iso_filename", "") or "").strip()
                    else None
                ),
            )
            pod_status = kube.create_pod(pod_request)
            kube.create_service_for_pod(
                pod_name=kube._pod_name(pod_request),
                service_name=f"svc-{instance_id[:8]}",
                service_type="ClusterIP",
            )

        phase = self._phase_from_pod(pod_status.phase)
        self._patch_status(
            name,
            {
                "observedGeneration": int(metadata.get("generation") or 0),
                "phase": phase,
                "runtime": {
                    "podName": kube._pod_name(
                        PodRequest(
                            instance_id=instance_id,
                            template_id=template.id,
                            image_path=str(image.filename),
                            image_source_pvc=image.source_pvc,
                            os_type=template.os_type,
                            cpu_cores=template.cpu_cores,
                            ram_mb=template.ram_mb,
                            owner=owner,
                            installer_iso_filename=(
                                str(getattr(image, "installer_iso_filename", "") or "").strip() or None
                            ),
                            boot_order=(
                                "dc"
                                if str(getattr(image, "source_kind", "") or "").strip().lower() == "scratch"
                                and str(getattr(image, "installer_iso_filename", "") or "").strip()
                                else None
                            ),
                        )
                    ),
                    "serviceName": f"svc-{instance_id[:8]}",
                },
                "conditions": [
                    _status_condition(
                        condition_type="ReconcileReady",
                        condition_status="True" if phase in {"Pending", "Running"} else "False",
                        reason="VmLifecycleManaged",
                        message=f"Controller reconciled VM to phase {phase}.",
                    )
                ],
            },
        )
        self._update_instance_db_status(instance_id, phase)

    def _reconcile_deleting(self, *, name: str, owner: str, metadata: dict[str, Any]) -> None:
        if self.dry_run:
            self._remove_finalizer(name, metadata)
            return
        disk_pvc = None
        with session_scope() as session:
            row = session.get(Instance, name)
            if row:
                disk_pvc = row.disk_pvc
        try:
            kube.delete_pod(name, owner, disk_pvc=disk_pvc)
        except ApiException as exc:
            if exc.status not in {404, 409, 422}:
                raise
        self._remove_finalizer(name, metadata)

    def _reconcile_one(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        name = str(metadata.get("name") or "").strip()
        if not name:
            return "error"
        owner = str(
            ((spec.get("owner") or {}).get("username") if isinstance(spec.get("owner"), dict) else "") or ""
        ).strip()
        if not owner:
            owner = "unknown"

        deletion_timestamp = metadata.get("deletionTimestamp")
        self._ensure_finalizer(name, metadata)
        if deletion_timestamp:
            self._reconcile_deleting(name=name, owner=owner, metadata=metadata)
            return "ok"

        workload = spec.get("workload") if isinstance(spec.get("workload"), dict) else {}
        workload_kind = str(workload.get("kind") or "vm").strip().lower()
        if workload_kind != "vm":
            self._patch_status(
                name,
                {
                    "phase": "Failed",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="False",
                            reason="UnsupportedWorkloadKind",
                            message=f"Unsupported workload.kind={workload_kind!r}.",
                        )
                    ],
                },
            )
            return "ok"

        lifecycle = spec.get("lifecycle") if isinstance(spec.get("lifecycle"), dict) else {}
        desired_state = str(lifecycle.get("desiredState") or "running").strip().lower()
        if desired_state == "stopped":
            if not self.dry_run:
                try:
                    kube.stop_pod(name, owner)
                except ApiException as exc:
                    if exc.status != 404:
                        raise
            self._patch_status(
                name,
                {
                    "phase": "Stopped",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="True",
                            reason="DryRunDesiredStopped" if self.dry_run else "DesiredStopped",
                            message=(
                                "Controller dry-run mode set VM to stopped state."
                                if self.dry_run
                                else "Controller set VM to stopped state."
                            ),
                        )
                    ],
                },
            )
            self._update_instance_db_status(name, "Stopped")
            return "ok"

        template_ref = spec.get("templateRef") if isinstance(spec.get("templateRef"), dict) else {}
        template_id = str(template_ref.get("name") or "").strip()
        if not template_id:
            self._patch_status(
                name,
                {
                    "phase": "Failed",
                    "conditions": [
                        _status_condition(
                            condition_type="ReconcileReady",
                            condition_status="False",
                            reason="TemplateRefMissing",
                            message="spec.templateRef.name is required for VM reconcile.",
                        )
                    ],
                },
            )
            return "ok"
        self._reconcile_vm_running(name=name, owner=owner, template_id=template_id, metadata=metadata)
        return "ok"

    def run_forever(self) -> None:
        self._load_kube()
        logger.info(
            "LabInstance controller started namespace=%s group=%s version=%s plural=%s poll=%ss leaderElection=%s",
            self.namespace,
            self.group,
            self.version,
            self.plural,
            self.poll_seconds,
            "enabled" if self._leader_elector.enabled else "disabled",
        )
        self.metrics.set_health(ready=False, leader=False)
        while True:
            self.metrics.touch_liveness()
            try:
                can_reconcile = self._leader_elector.should_run_reconcile()
            except Exception:
                logger.exception("Leader election check failed")
                self.metrics.set_health(ready=False, leader=False)
                time.sleep(self.leader_retry_seconds)
                continue
            if not can_reconcile:
                self.metrics.set_health(ready=False, leader=False)
                time.sleep(self.leader_retry_seconds)
                continue

            self.metrics.set_health(ready=True, leader=True)
            started = time.monotonic()
            result = "ok"
            try:
                items = self._list()
                now = datetime.now(UTC)
                backlog = 0
                stuck = 0
                for item in items:
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    status = item.get("status") if isinstance(item.get("status"), dict) else {}
                    phase = str(status.get("phase") or "").strip().lower()
                    created_at_raw = str(metadata.get("creationTimestamp") or "").strip()
                    created_at = None
                    if created_at_raw.endswith("Z"):
                        try:
                            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
                        except ValueError:
                            created_at = None
                    if metadata.get("deletionTimestamp") and self.finalizer in list(metadata.get("finalizers") or []):
                        backlog += 1
                    if created_at and phase in {"pending", "building", "starting"}:
                        if (now - created_at) > timedelta(seconds=self.stuck_seconds):
                            stuck += 1

                    one_started = time.monotonic()
                    one_result = self._reconcile_one(item)
                    self.metrics.observe(one_result, time.monotonic() - one_started)

                self.metrics.set_cycle(total=len(items), finalizer_backlog=backlog, stuck_instances=stuck)
            except Exception:
                result = "error"
                logger.exception("LabInstance reconcile cycle failed")
                self.metrics.set_health(ready=False, leader=True)
                self.metrics.observe("error", max(0.0, time.monotonic() - started))
            if result == "ok":
                elapsed = time.monotonic() - started
                logger.debug("LabInstance reconcile cycle completed in %.3fs", elapsed)
            time.sleep(self.poll_seconds)


def _start_metrics_server(metrics: _Metrics) -> _ThreadingHTTPServer:
    bind_host = str(settings.labinstance_controller_metrics_bind or "0.0.0.0").strip() or "0.0.0.0"
    bind_port = max(1, min(65535, int(settings.labinstance_controller_metrics_port or 9408)))

    _MetricsHandler.metrics = metrics
    server = _ThreadingHTTPServer((bind_host, bind_port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(
        "LabInstance controller health endpoints listening on %s:%s (metrics=/metrics, livez=/livez, readyz=/readyz)",
        bind_host,
        bind_port,
    )
    return server


def main() -> int:
    if not bool(settings.labinstance_controller_enabled):
        logger.info("BLABS_LABINSTANCE_CONTROLLER_ENABLED is false; exiting.")
        return 0
    metrics = _Metrics()
    _start_metrics_server(metrics)
    controller = LabInstanceController(metrics)
    controller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
