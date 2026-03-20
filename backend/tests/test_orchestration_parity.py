from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

import src.routes.admin as admin_routes
from src.config import settings
from src.db import engine
from src.tables import Instance


def _seed_instance(instance_id: str, status: str) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            Instance(
                id=instance_id,
                template_id="tmpl-1",
                owner="admin",
                status=status,
                disk_pvc=f"pvc-{instance_id[:8]}",
                started_at=now,
                last_active_at=now,
                console_url="https://example.invalid/console",
            )
        )
        session.commit()


def test_orchestration_parity_report_disabled_for_db_mode(login_admin, monkeypatch) -> None:
    monkeypatch.setattr(settings, "orchestration_backend", "db")
    response = login_admin.get("/admin/settings/runtime/orchestration-parity")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is False
    assert payload["mode"] == "db"


def test_orchestration_parity_report_compares_db_and_crd(login_admin, monkeypatch) -> None:
    _seed_instance("vm-db-a", "running")
    _seed_instance("vm-db-b", "stopped")
    _seed_instance("vm-db-only", "pending")

    monkeypatch.setattr(settings, "orchestration_backend", "dual")
    monkeypatch.setattr(admin_routes.kube, "_client", lambda: object())

    class _FakeCustomObjectsApi:
        def list_namespaced_custom_object(self, **kwargs):
            return {
                "items": [
                    {"metadata": {"name": "vm-db-a"}, "status": {"phase": "Running"}},
                    {"metadata": {"name": "vm-db-b"}, "status": {"phase": "Running"}},
                    {"metadata": {"name": "vm-crd-only"}, "status": {"phase": "Pending"}},
                ]
            }

    monkeypatch.setattr(admin_routes.client, "CustomObjectsApi", lambda: _FakeCustomObjectsApi())

    response = login_admin.get("/admin/settings/runtime/orchestration-parity")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["available"] is True
    assert payload["mode"] == "dual"
    assert payload["db_instances"] == 3
    assert payload["crd_instances"] == 3
    assert payload["missing_in_crd"] == 1
    assert payload["missing_in_db"] == 1
    assert payload["status_mismatch"] == 1
    assert payload["missing_in_crd_samples"] == ["vm-db-only"]
    assert payload["missing_in_db_samples"] == ["vm-crd-only"]
    assert payload["status_mismatch_samples"][0]["instance_id"] == "vm-db-b"
    assert payload["status_mismatch_samples"][0]["db_status"] == "stopped"
    assert payload["status_mismatch_samples"][0]["crd_phase"] == "running"
