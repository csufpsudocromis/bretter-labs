from datetime import datetime, timezone
from types import SimpleNamespace

import src.routes.admin as admin_routes
from src.models import ErrorLogView


def test_alerts_errors_reports_clear_unavailable_when_log_path_missing(login_admin, monkeypatch):
    monkeypatch.setattr(admin_routes.settings, "error_log_file_path", "")
    monkeypatch.setattr(admin_routes, "_fetch_alertmanager_alerts", lambda: ([], ""))
    monkeypatch.setattr(
        admin_routes,
        "_collect_k8s_error_logs",
        lambda max_bytes, page, per_page: ErrorLogView(
            source="kubernetes:labs:backend",
            bytes=0,
            truncated=False,
            content="No error lines found.",
        ),
    )

    response = login_admin.get("/admin/alerts-errors")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["error_log_clear_supported"] is False
    assert "BLABS_ERROR_LOG_FILE_PATH" in payload["error_log_clear_reason"]
    assert payload["rdp_readiness"]["status"] in {"ok", "warning", "critical", "unknown"}
    assert "stuck_instances" in payload["rdp_readiness"]


def test_clear_alerts_error_log_rejects_when_log_path_missing(login_admin, monkeypatch):
    monkeypatch.setattr(admin_routes.settings, "error_log_file_path", "")
    response = login_admin.post("/admin/alerts-errors/clear")
    assert response.status_code == 400
    assert "BLABS_ERROR_LOG_FILE_PATH" in response.json().get("detail", "")


def test_collect_k8s_error_logs_uses_backend_pods_when_labels_missing(monkeypatch):
    backend_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="bretter-backend-abc123",
            creation_timestamp=datetime.now(timezone.utc),
        )
    )
    non_backend_pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="some-other-pod",
            creation_timestamp=datetime.now(timezone.utc),
        )
    )

    class FakeCore:
        def list_namespaced_pod(self, namespace, label_selector=None):
            if label_selector == "app=bretter-backend":
                return SimpleNamespace(items=[])
            return SimpleNamespace(items=[non_backend_pod, backend_pod])

        def read_namespaced_pod_log(self, name, namespace, timestamps, tail_lines, limit_bytes):
            if name != "bretter-backend-abc123":
                raise AssertionError(f"unexpected pod log read: {name}")
            return "2026-03-17T00:00:00Z ERROR finalize failed"

    monkeypatch.setattr(admin_routes.settings, "kube_namespace", "labs")
    monkeypatch.setattr(admin_routes.kube, "_client", lambda: FakeCore())

    view = admin_routes._collect_k8s_error_logs(max_bytes=1024 * 1024, page=1, per_page=50)
    assert view.source == "kubernetes:labs:backend"
    assert view.total_lines == 1
    assert view.lines[0].startswith("[bretter-backend-abc123]")


def test_list_backend_pods_excludes_completed_and_terminating(monkeypatch):
    running_backend = SimpleNamespace(
        metadata=SimpleNamespace(name="bretter-backend-running", deletion_timestamp=None),
        status=SimpleNamespace(phase="Running"),
    )
    completed_backend = SimpleNamespace(
        metadata=SimpleNamespace(name="bretter-backend-completed", deletion_timestamp=None),
        status=SimpleNamespace(phase="Succeeded"),
    )
    terminating_backend = SimpleNamespace(
        metadata=SimpleNamespace(name="bretter-backend-terminating", deletion_timestamp=datetime.now(timezone.utc)),
        status=SimpleNamespace(phase="Running"),
    )

    class FakeCore:
        def list_namespaced_pod(self, namespace, label_selector=None):
            return SimpleNamespace(items=[running_backend, completed_backend, terminating_backend])

    monkeypatch.setattr(admin_routes.settings, "kube_namespace", "labs")
    pods, err = admin_routes._list_backend_pods(FakeCore())
    assert err == ""
    assert [pod.metadata.name for pod in pods] == ["bretter-backend-running"]


def test_fetch_alertmanager_alerts_filters_expected_job_noise(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "labels": {
                        "alertname": "KubeJobFailed",
                        "severity": "warning",
                        "job_name": "bretter-slo-rdp-connect-latency-29575830",
                    },
                    "annotations": {"summary": "job failed"},
                    "status": {"state": "active"},
                    "startsAt": "2026-03-26T00:00:00Z",
                    "generatorURL": "http://prometheus",
                },
                {
                    "labels": {
                        "alertname": "BretterBackendUnavailable",
                        "severity": "critical",
                        "pod": "bretter-backend-abc",
                    },
                    "annotations": {"summary": "backend unavailable"},
                    "status": {"state": "active"},
                    "startsAt": "2026-03-26T00:01:00Z",
                    "generatorURL": "http://prometheus",
                },
            ]

    monkeypatch.setattr(admin_routes.settings, "alertmanager_api_url", "http://example-alertmanager")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_alert_names", "KubeJobFailed,KubePodNotReady")
    monkeypatch.setattr(
        admin_routes.settings,
        "alertmanager_suppressed_job_name_regex",
        r"^bretter-(slo-|cleanup-|ghcr-access-check-|kubelet-serving-csr-approver-)",
    )
    monkeypatch.setattr(
        admin_routes.settings,
        "alertmanager_suppressed_pod_regex",
        r"^bretter-(slo-|cleanup-|ghcr-access-check-|kubelet-serving-csr-approver-)",
    )
    monkeypatch.setattr(admin_routes.requests, "get", lambda url, timeout: FakeResp())

    alerts, err = admin_routes._fetch_alertmanager_alerts()
    assert err == ""
    assert len(alerts) == 1
    assert alerts[0].name == "BretterBackendUnavailable"


def test_fetch_alertmanager_alerts_keeps_actionable_kubejobfailed_alerts(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "labels": {
                        "alertname": "KubeJobFailed",
                        "severity": "warning",
                        "job_name": "database-maintenance-nightly",
                    },
                    "annotations": {"summary": "job failed"},
                    "status": {"state": "active"},
                    "startsAt": "2026-03-26T00:00:00Z",
                    "generatorURL": "http://prometheus",
                }
            ]

    monkeypatch.setattr(admin_routes.settings, "alertmanager_api_url", "http://example-alertmanager")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_alert_names", "KubeJobFailed")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_job_name_regex", r"^bretter-slo-")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_pod_regex", r"^bretter-slo-")
    monkeypatch.setattr(admin_routes.requests, "get", lambda url, timeout: FakeResp())

    alerts, err = admin_routes._fetch_alertmanager_alerts()
    assert err == ""
    assert len(alerts) == 1
    assert alerts[0].name == "KubeJobFailed"


def test_fetch_alertmanager_alerts_suppresses_watchdog_by_name_without_labels(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "labels": {
                        "alertname": "Watchdog",
                        "severity": "none",
                    },
                    "annotations": {"summary": "always firing"},
                    "status": {"state": "active"},
                    "startsAt": "2026-03-26T00:00:00Z",
                    "generatorURL": "http://prometheus",
                }
            ]

    monkeypatch.setattr(admin_routes.settings, "alertmanager_api_url", "http://example-alertmanager")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_alert_names", "Watchdog")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_job_name_regex", r"^bretter-slo-")
    monkeypatch.setattr(admin_routes.settings, "alertmanager_suppressed_pod_regex", r"^bretter-slo-")
    monkeypatch.setattr(admin_routes.requests, "get", lambda url, timeout: FakeResp())

    alerts, err = admin_routes._fetch_alertmanager_alerts()
    assert err == ""
    assert alerts == []
