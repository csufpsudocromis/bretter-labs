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
from sqlmodel import Session

from ..config import settings
from ..db import engine, init_db
from ..routes import admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("labimageimport-controller")


class _Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scanned_total = 0
        self.completed_total = 0
        self.failed_total = 0
        self.errors_total = 0
        self.last_cycle_unix = 0
        self.ready = 0
        self.leader = 0
        self.last_liveness_unix = int(time.time())

    def observe(self, stats: dict[str, int]) -> None:
        with self._lock:
            self.scanned_total += max(0, int(stats.get("scanned", 0)))
            self.completed_total += max(0, int(stats.get("completed", 0)))
            self.failed_total += max(0, int(stats.get("failed", 0)))
            self.errors_total += max(0, int(stats.get("errors", 0)))
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
                "# HELP blabs_labimageimport_watchdog_scanned_total Total upload tasks scanned by controller.",
                "# TYPE blabs_labimageimport_watchdog_scanned_total counter",
                f"blabs_labimageimport_watchdog_scanned_total {self.scanned_total}",
                "# HELP blabs_labimageimport_watchdog_completed_total Total upload tasks completed by controller.",
                "# TYPE blabs_labimageimport_watchdog_completed_total counter",
                f"blabs_labimageimport_watchdog_completed_total {self.completed_total}",
                "# HELP blabs_labimageimport_watchdog_failed_total Total upload tasks failed by controller.",
                "# TYPE blabs_labimageimport_watchdog_failed_total counter",
                f"blabs_labimageimport_watchdog_failed_total {self.failed_total}",
                "# HELP blabs_labimageimport_watchdog_errors_total Total watchdog internal refresh errors.",
                "# TYPE blabs_labimageimport_watchdog_errors_total counter",
                f"blabs_labimageimport_watchdog_errors_total {self.errors_total}",
                "# HELP blabs_labimageimport_last_reconcile_unix Latest reconcile cycle timestamp.",
                "# TYPE blabs_labimageimport_last_reconcile_unix gauge",
                f"blabs_labimageimport_last_reconcile_unix {self.last_cycle_unix}",
                "# HELP blabs_labimageimport_controller_ready Controller readiness state.",
                "# TYPE blabs_labimageimport_controller_ready gauge",
                f"blabs_labimageimport_controller_ready {self.ready}",
                "# HELP blabs_labimageimport_controller_leader Controller leadership state.",
                "# TYPE blabs_labimageimport_controller_leader gauge",
                f"blabs_labimageimport_controller_leader {self.leader}",
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

    @staticmethod
    def _fmt_micro(now: datetime) -> str:
        return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _is_lease_expired(self, lease: client.V1Lease, now_utc: datetime) -> bool:
        spec = lease.spec or client.V1LeaseSpec()
        renew_time = self._normalize_time(spec.renew_time)
        if renew_time is None:
            renew_time = self._normalize_time(spec.acquire_time)
        if renew_time is None:
            return True
        duration = int(spec.lease_duration_seconds or self.lease_duration_seconds)
        return now_utc > (renew_time + timedelta(seconds=max(1, duration)))

    def _build_spec(self, *, previous: client.V1Lease | None, now: datetime) -> dict[str, Any]:
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


class LabImageImportController:
    def __init__(self, metrics: _Metrics) -> None:
        self.metrics = metrics
        self.poll_seconds = max(3, int(getattr(settings, "labimageimport_controller_poll_seconds", 10) or 10))
        self.max_tasks = max(1, int(getattr(settings, "image_upload_watchdog_max_tasks", 25) or 25))
        self.retry_seconds = max(2, int(getattr(settings, "labimageimport_controller_retry_period_seconds", 5) or 5))

        self._coord: client.CoordinationV1Api | None = None
        identity = f"{socket.gethostname()}-{os.getpid()}"
        self._leader_elector = _LeaderElector(
            enabled=bool(getattr(settings, "labimageimport_controller_leader_election_enabled", True)),
            namespace=settings.kube_namespace,
            lease_name=str(
                getattr(settings, "labimageimport_controller_lease_name", "bretter-labimageimport-controller-leader")
                or "bretter-labimageimport-controller-leader"
            ).strip(),
            identity=identity,
            lease_duration_seconds=int(getattr(settings, "labimageimport_controller_lease_duration_seconds", 30) or 30),
            retry_period_seconds=self.retry_seconds,
        )

    def _load_kube(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self._coord = client.CoordinationV1Api()
        self._leader_elector.bind_client(self._coord)

    def run_forever(self) -> None:
        self._load_kube()
        logger.info(
            "LabImageImport controller started namespace=%s poll=%ss leaderElection=%s",
            settings.kube_namespace,
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
                time.sleep(self.retry_seconds)
                continue
            if not can_reconcile:
                self.metrics.set_health(ready=False, leader=False)
                time.sleep(self.retry_seconds)
                continue

            self.metrics.set_health(ready=True, leader=True)
            try:
                with Session(engine) as session:
                    stats = admin.run_upload_task_watchdog(session, max_tasks=self.max_tasks)
                    self.metrics.observe(stats)
                    if stats.get("scanned", 0) > 0:
                        logger.info(
                            "LabImageImport reconcile scanned=%s completed=%s failed=%s errors=%s "
                            "cleanup_scanned=%s cleanup_deleted=%s cleanup_errors=%s",
                            stats.get("scanned", 0),
                            stats.get("completed", 0),
                            stats.get("failed", 0),
                            stats.get("errors", 0),
                            stats.get("cleanup_scanned", 0),
                            stats.get("cleanup_deleted", 0),
                            stats.get("cleanup_errors", 0),
                        )
            except Exception:
                logger.exception("LabImageImport reconcile cycle failed")
                self.metrics.set_health(ready=False, leader=True)
            time.sleep(self.poll_seconds)


def _start_metrics_server(metrics: _Metrics) -> _ThreadingHTTPServer:
    bind_host = str(getattr(settings, "labimageimport_controller_metrics_bind", "0.0.0.0") or "0.0.0.0").strip()
    bind_port = max(1, min(65535, int(getattr(settings, "labimageimport_controller_metrics_port", 9410) or 9410)))
    _MetricsHandler.metrics = metrics
    server = _ThreadingHTTPServer((bind_host, bind_port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(
        "LabImageImport controller health endpoints listening on %s:%s (metrics=/metrics, livez=/livez, readyz=/readyz)",
        bind_host,
        bind_port,
    )
    return server


def main() -> int:
    if not bool(getattr(settings, "labimageimport_controller_enabled", True)):
        logger.info("BLABS_LABIMAGEIMPORT_CONTROLLER_ENABLED is false; exiting.")
        return 0
    init_db()
    metrics = _Metrics()
    _start_metrics_server(metrics)
    controller = LabImageImportController(metrics)
    controller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
