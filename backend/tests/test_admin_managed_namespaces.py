from fastapi.testclient import TestClient
from sqlmodel import Session, select

import src.routes.admin_namespaces as admin_namespaces_routes
from src.db import engine
from src.tables import Image, Instance, ManagedNamespace, Template
from src.time_utils import utc_now


def test_admin_managed_namespace_crud(login_admin: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(admin_namespaces_routes, "_reconcile_managed_namespace", lambda _row: None)

    created = login_admin.post(
        "/admin/settings/namespaces",
        json={
            "namespace": "labs-team-red",
            "team_label": "red",
            "security_profile": "baseline",
            "enforce_network_policies": True,
            "max_pods": "220",
            "max_services": "120",
            "max_persistent_volume_claims": "180",
            "requests_cpu": "10",
            "limits_cpu": "20",
            "requests_memory": "24Gi",
            "limits_memory": "48Gi",
            "requests_storage": "3Ti",
            "limit_min_cpu": "100m",
            "limit_min_memory": "128Mi",
            "limit_default_request_cpu": "500m",
            "limit_default_request_memory": "512Mi",
            "limit_default_cpu": "4",
            "limit_default_memory": "4Gi",
            "limit_max_cpu": "12",
            "limit_max_memory": "24Gi",
            "enabled": True,
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["namespace"] == "labs-team-red"
    assert payload["team_label"] == "red"
    assert payload["security_profile"] == "baseline"
    assert payload["max_pods"] == "220"

    listed = login_admin.get("/admin/settings/namespaces")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert any(row["namespace"] == "labs-team-red" for row in rows)

    updated = login_admin.patch(
        "/admin/settings/namespaces/labs-team-red",
        json={"security_profile": "restricted", "enabled": False, "max_pods": "99"},
    )
    assert updated.status_code == 200, updated.text
    updated_payload = updated.json()
    assert updated_payload["security_profile"] == "restricted"
    assert updated_payload["enabled"] is False
    assert updated_payload["max_pods"] == "99"

    reconcile_disabled = login_admin.post("/admin/settings/namespaces/labs-team-red/reconcile")
    assert reconcile_disabled.status_code == 409, reconcile_disabled.text

    enabled = login_admin.patch("/admin/settings/namespaces/labs-team-red", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    reconciled = login_admin.post("/admin/settings/namespaces/labs-team-red/reconcile")
    assert reconciled.status_code == 200, reconciled.text

    deleted = login_admin.delete(
        "/admin/settings/namespaces/labs-team-red", params={"delete_cluster_namespace": "false"}
    )
    assert deleted.status_code == 204, deleted.text
    listed_after_delete = login_admin.get("/admin/settings/namespaces")
    assert listed_after_delete.status_code == 200, listed_after_delete.text
    assert not any(row["namespace"] == "labs-team-red" for row in listed_after_delete.json())


def test_admin_managed_namespace_delete_blocks_when_active(login_admin: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(admin_namespaces_routes, "_reconcile_managed_namespace", lambda _row: None)

    created = login_admin.post(
        "/admin/settings/namespaces",
        json={"namespace": "labs-team-blue", "team_label": "blue", "enabled": False},
    )
    assert created.status_code == 201, created.text

    with Session(engine) as session:
        image = Image(
            id="img-blue-active",
            name="blue",
            filename="blue.qcow2",
            checksum="a" * 64,
            size_bytes=1,
            created_at=utc_now(),
        )
        template = Template(
            id="tmpl-blue-active",
            name="blue",
            image_id=image.id,
            cpu_cores=1,
            ram_mb=1024,
            auto_delete_minutes=30,
            enabled=True,
            created_at=utc_now(),
        )
        instance = Instance(
            id="inst-blue-active",
            template_id=template.id,
            owner="admin",
            namespace="labs-team-blue",
            status="running",
            started_at=utc_now(),
            last_active_at=utc_now(),
        )
        session.add(image)
        session.add(template)
        session.add(instance)
        session.commit()

    blocked = login_admin.delete(
        "/admin/settings/namespaces/labs-team-blue", params={"delete_cluster_namespace": "false"}
    )
    assert blocked.status_code == 409, blocked.text
    assert "active labs" in blocked.json()["detail"].lower()

    with Session(engine) as session:
        row = session.get(Instance, "inst-blue-active")
        assert row is not None
        row.status = "stopped"
        row.last_active_at = utc_now()
        session.add(row)
        session.commit()

    deleted = login_admin.delete(
        "/admin/settings/namespaces/labs-team-blue", params={"delete_cluster_namespace": "false"}
    )
    assert deleted.status_code == 204, deleted.text
    with Session(engine) as session:
        check = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == "labs-team-blue")).first()
        assert check is None
