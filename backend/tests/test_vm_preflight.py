from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlmodel import Session

import src.routes.user as user_routes
from src.db import engine
from src.services.kubernetes import PodStatus
from src.services.multi_cluster import PlacementDecision, PlacementError
from src.tables import Image, Template
from src.time_utils import utc_now


def _seed_vm_template() -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id="img-preflight-1",
                name="Windows Base",
                filename="windows-base.qcow2",
                checksum="sha256:preflight",
                size_bytes=2 * 1024 * 1024 * 1024,
                source_pvc="golden-images-vm",
            )
        )
        session.add(
            Template(
                id="tmpl-preflight-1",
                name="Windows Lab",
                description="VM template",
                os_type="windows",
                image_id="img-preflight-1",
                cpu_cores=2,
                ram_mb=4096,
                enabled=True,
                network_mode="bridge",
                console_provider="guacamole_rdp",
            )
        )
        session.commit()


class _FakeStorageApi:
    def __init__(self, _api_client):
        pass

    def read_storage_class(self, name: str):
        return {"metadata": {"name": name}}


class _FakeKube:
    def __init__(self, *, runner_pull_ok: bool = True):
        self._runner_pull_ok = runner_pull_ok
        self._core = SimpleNamespace(api_client=object())

    def _client(self):
        return self._core

    def resolve_vm_source_pvc(self, *, image_source_pvc: str, runtime_namespace: str):
        source = SimpleNamespace(spec=SimpleNamespace(storage_class_name="longhorn-r1"))
        return source, runtime_namespace

    def check_vm_runner_image_pullability(self, *, namespace: str, timeout_seconds: int = 30):
        if self._runner_pull_ok:
            return True, "runner image pull check completed (phase=running)."
        return False, "runner image pull failed: forbidden"


def test_vm_template_preflight_reports_ready(login_user: TestClient, monkeypatch) -> None:
    _seed_vm_template()
    fake_kube = _FakeKube(runner_pull_ok=True)
    monkeypatch.setattr(user_routes.k8s_client, "StorageV1Api", _FakeStorageApi)
    monkeypatch.setattr(
        user_routes,
        "select_cluster_for_launch",
        lambda *args, **kwargs: PlacementDecision(cluster_id="local", reason="policy"),
    )
    monkeypatch.setattr(user_routes, "_kube_for_instance_cluster", lambda *_args, **_kwargs: fake_kube)
    monkeypatch.setattr(user_routes, "ensure_team_runtime_namespace", lambda *_args, **_kwargs: None)

    response = login_user.get("/user/templates/tmpl-preflight-1/preflight")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is True
    assert payload["blocking_reason"] is None
    statuses = {entry["key"]: entry["status"] for entry in payload["checks"]}
    assert statuses["placement"] == "ok"
    assert statuses["namespace"] == "ok"
    assert statuses["source_pvc"] == "ok"
    assert statuses["storage_class"] == "ok"
    assert statuses["node_admission"] == "ok"
    assert statuses["pvc_admission"] == "ok"
    assert statuses["runner_image"] == "ok"


def test_vm_template_preflight_blocks_on_runner_pull_failure(login_user: TestClient, monkeypatch) -> None:
    _seed_vm_template()
    fake_kube = _FakeKube(runner_pull_ok=False)
    monkeypatch.setattr(user_routes.k8s_client, "StorageV1Api", _FakeStorageApi)
    monkeypatch.setattr(
        user_routes,
        "select_cluster_for_launch",
        lambda *args, **kwargs: PlacementDecision(cluster_id="local", reason="policy"),
    )
    monkeypatch.setattr(user_routes, "_kube_for_instance_cluster", lambda *_args, **_kwargs: fake_kube)
    monkeypatch.setattr(user_routes, "ensure_team_runtime_namespace", lambda *_args, **_kwargs: None)

    response = login_user.get("/user/templates/tmpl-preflight-1/preflight")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is False
    assert "runner image pull failed" in str(payload["blocking_reason"])
    statuses = {entry["key"]: entry["status"] for entry in payload["checks"]}
    assert statuses["node_admission"] == "ok"
    assert statuses["pvc_admission"] == "ok"
    assert statuses["runner_image"] == "error"


def test_vm_template_preflight_returns_placement_error(login_user: TestClient, monkeypatch) -> None:
    _seed_vm_template()
    monkeypatch.setattr(
        user_routes,
        "select_cluster_for_launch",
        lambda *args, **kwargs: (_ for _ in ()).throw(PlacementError("no runtime cluster available")),
    )

    response = login_user.get("/user/templates/tmpl-preflight-1/preflight")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ready"] is False
    assert payload["blocking_reason"] == "no runtime cluster available"
    assert payload["checks"][0]["key"] == "placement"
    assert payload["checks"][0]["status"] == "error"


def test_start_vm_does_not_preflight_block_on_node_admission_failure(login_user: TestClient, monkeypatch) -> None:
    _seed_vm_template()
    fake_kube = _FakeKube(runner_pull_ok=True)
    monkeypatch.setattr(user_routes.k8s_client, "StorageV1Api", _FakeStorageApi)
    monkeypatch.setattr(
        user_routes,
        "select_cluster_for_launch",
        lambda *args, **kwargs: PlacementDecision(cluster_id="local", reason="policy"),
    )
    monkeypatch.setattr(user_routes, "_kube_for_instance_cluster", lambda *_args, **_kwargs: fake_kube)
    monkeypatch.setattr(user_routes, "ensure_team_runtime_namespace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(user_routes, "evaluate_node_launch_admission", lambda _kube: (False, "nodes unavailable"))
    monkeypatch.setattr(
        user_routes,
        "evaluate_vm_storage_launch_admission",
        lambda _kube, namespace: (True, f"ok ({namespace})"),
    )
    monkeypatch.setattr(user_routes, "vm_orchestration_uses_legacy_path", lambda: False)
    monkeypatch.setattr(user_routes, "vm_orchestration_writes_crd", lambda: False)

    response = login_user.post("/user/templates/tmpl-preflight-1/start")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "pending"


def test_status_feedback_maps_unbound_pvc_to_building_with_elapsed_hint() -> None:
    pod_status = PodStatus(
        instance_id="vm-preflight",
        phase="Pending",
        waiting_reason="ContainerCreating",
        waiting_message="0/1 nodes are available: 1 pod has unbound immediate PersistentVolumeClaims.",
    )
    stage, detail = user_routes._status_feedback(
        "pending",
        pod_status,
        started_at=utc_now() - timedelta(minutes=3, seconds=12),
    )
    assert stage == "building"
    assert "PersistentVolumeClaims" in detail
    assert "Elapsed:" in detail
