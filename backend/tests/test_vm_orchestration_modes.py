from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from src.config import settings
from src.db import engine
from src.services.kubernetes import PodStatus
from src.tables import Image, Instance, Template
import src.routes.user as user_routes


def _seed_template_and_image() -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id="img-vm-1",
                name="Windows 11",
                filename="win11.vdi",
                source_pvc="golden-win11",
                checksum="abc123",
                size_bytes=10 * 1024 * 1024 * 1024,
            )
        )
        session.add(
            Template(
                id="tmpl-vm-1",
                name="Windows VM",
                description="vm template",
                os_type="windows",
                image_id="img-vm-1",
                cpu_cores=2,
                ram_mb=4096,
                enabled=True,
                network_mode="bridge",
                console_provider="spice",
            )
        )
        session.commit()


def _seed_instance(instance_id: str = "vm-alice-1") -> None:
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            Instance(
                id=instance_id,
                template_id="tmpl-vm-1",
                owner="alice",
                status="running",
                disk_pvc="pvc-vm-alice",
                started_at=now,
                last_active_at=now,
                console_url="https://example.invalid/console",
            )
        )
        session.commit()


def test_start_vm_crd_mode_skips_legacy_kube_launch(login_user, monkeypatch) -> None:
    _seed_template_and_image()
    monkeypatch.setattr(settings, "orchestration_backend", "crd")

    def _forbidden_create_pod(*args, **kwargs):
        raise AssertionError("legacy kube.create_pod should not be called in crd mode")

    monkeypatch.setattr(user_routes.kube, "create_pod", _forbidden_create_pod)
    monkeypatch.setattr(
        user_routes.kube, "create_service_for_pod", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    seen = {"upsert": 0}
    monkeypatch.setattr(user_routes, "upsert_vm_labinstance", lambda **kwargs: seen.__setitem__("upsert", 1))

    response = login_user.post("/user/templates/tmpl-vm-1/start")
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "pending"
    assert "Queued for operator reconciliation." in payload["status_detail"]
    assert seen["upsert"] == 1


def test_start_vm_dual_mode_runs_legacy_and_crd_shadow(login_user, monkeypatch) -> None:
    _seed_template_and_image()
    monkeypatch.setattr(settings, "orchestration_backend", "dual")

    seen = {"create_pod": 0, "upsert": 0}

    def _fake_create_pod(req):
        seen["create_pod"] += 1
        return PodStatus(instance_id=req.instance_id, phase="pending", disk_pvc=f"pvc-{req.instance_id[:8]}")

    monkeypatch.setattr(user_routes.kube, "create_pod", _fake_create_pod)
    monkeypatch.setattr(user_routes, "upsert_vm_labinstance", lambda **kwargs: seen.__setitem__("upsert", 1))

    response = login_user.post("/user/templates/tmpl-vm-1/start")
    assert response.status_code == 201, response.text
    assert seen["create_pod"] == 1
    assert seen["upsert"] == 1


def test_stop_vm_crd_mode_patches_desired_state_without_legacy_stop(login_user, monkeypatch) -> None:
    _seed_template_and_image()
    _seed_instance()
    monkeypatch.setattr(settings, "orchestration_backend", "crd")

    def _forbidden_stop(*args, **kwargs):
        raise AssertionError("legacy kube.stop_pod should not be called in crd mode")

    monkeypatch.setattr(user_routes.kube, "stop_pod", _forbidden_stop)
    seen = {"patched": 0}
    monkeypatch.setattr(
        user_routes,
        "patch_vm_labinstance_desired_state",
        lambda instance_id, desired_state, **_kwargs: seen.__setitem__("patched", 1),
    )

    response = login_user.post("/user/pods/vm-alice-1/stop")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "stopped"
    assert seen["patched"] == 1
