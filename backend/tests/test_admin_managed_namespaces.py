from fastapi.testclient import TestClient
from sqlmodel import Session, select

import src.routes.admin_namespaces as admin_namespaces_routes
from src.db import engine
from src.tables import (
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    ImageUploadTask,
    Instance,
    ManagedNamespace,
    Template,
)
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
    assert payload["idle_timeout_minutes_default"] == 30
    assert payload["vm_auto_delete_minutes_default"] == 60
    assert payload["container_auto_delete_minutes_default"] == 60
    assert payload["queue_max_pending"] == 25

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
    reconcile_all = login_admin.post("/admin/settings/namespaces/reconcile-all")
    assert reconcile_all.status_code == 200, reconcile_all.text
    assert int(reconcile_all.json().get("total", 0)) >= 1

    deleted = login_admin.delete(
        "/admin/settings/namespaces/labs-team-red", params={"delete_cluster_namespace": "false"}
    )
    assert deleted.status_code == 200, deleted.text
    report = deleted.json()
    assert report["namespace"] == "labs-team-red"
    assert report["blocked"] is False
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
    assert deleted.status_code == 200, deleted.text
    with Session(engine) as session:
        check = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == "labs-team-blue")).first()
        assert check is None


def test_managed_namespace_observability_endpoint(login_admin: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(admin_namespaces_routes, "_reconcile_managed_namespace", lambda _row: None)
    monkeypatch.setattr(
        admin_namespaces_routes,
        "_namespace_policy_presence",
        lambda _namespace: (True, True, 5, []),
    )

    created = login_admin.post(
        "/admin/settings/namespaces",
        json={"namespace": "labs-observe", "enabled": False},
    )
    assert created.status_code == 201, created.text

    observed = login_admin.get("/admin/settings/namespaces/observability")
    assert observed.status_code == 200, observed.text
    rows = observed.json()
    target = next((row for row in rows if row["namespace"] == "labs-observe"), None)
    assert target is not None
    assert target["resource_quota_present"] is True
    assert target["limit_range_present"] is True
    assert target["network_policy_count"] == 5
    assert target["required_network_policies_missing"] == []


def test_delete_managed_namespace_force_cleanup_removes_active_records(login_admin: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(admin_namespaces_routes, "_reconcile_managed_namespace", lambda _row: None)
    monkeypatch.setattr(admin_namespaces_routes, "_delete_namespaced_runtime_resources", lambda _namespace: 4)

    created = login_admin.post(
        "/admin/settings/namespaces",
        json={"namespace": "labs-force-clean", "enabled": False},
    )
    assert created.status_code == 201, created.text

    with Session(engine) as session:
        image = Image(
            id="img-force-clean",
            name="force-clean",
            filename="force-clean.qcow2",
            checksum="f" * 64,
            size_bytes=1,
            namespace="labs-force-clean",
            created_at=utc_now(),
        )
        template = Template(
            id="tmpl-force-clean",
            name="force-clean",
            image_id=image.id,
            namespace="labs-force-clean",
            enabled_namespaces_json='["labs-force-clean","labs-other"]',
            cpu_cores=1,
            ram_mb=1024,
            auto_delete_minutes=30,
            idle_timeout_minutes=30,
            enabled=True,
            created_at=utc_now(),
        )
        ct_image = ContainerImage(
            id="ct-img-force-clean",
            name="ct-force-clean",
            image_ref="docker.io/library/nginx:1.27",
            namespace="labs-force-clean",
            created_at=utc_now(),
        )
        ct_template = ContainerTemplate(
            id="ct-tmpl-force-clean",
            template_key="ct-force-clean",
            version=1,
            is_default=True,
            name="ct-force-clean",
            container_image_id=ct_image.id,
            namespace="labs-force-clean",
            enabled_namespaces_json='["labs-force-clean"]',
            cpu_millicores=250,
            memory_mb=256,
            container_port=8080,
            auto_delete_minutes=30,
            idle_timeout_minutes=30,
            enabled=True,
            created_at=utc_now(),
        )
        vm = Instance(
            id="inst-force-clean",
            template_id=template.id,
            owner="admin",
            namespace="labs-force-clean",
            status="running",
            started_at=utc_now(),
            last_active_at=utc_now(),
        )
        ct = ContainerInstance(
            id="ct-inst-force-clean",
            template_id=ct_template.id,
            owner="admin",
            namespace="labs-force-clean",
            status="queued",
            started_at=utc_now(),
            last_active_at=utc_now(),
        )
        upload = ImageUploadTask(
            id="upload-force-clean",
            original_filename="force-clean.vdi",
            filename="force-clean.vdi",
            namespace="labs-force-clean",
            status="running",
            stage="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(image)
        session.add(template)
        session.add(ct_image)
        session.add(ct_template)
        session.add(vm)
        session.add(ct)
        session.add(upload)
        session.commit()

    blocked = login_admin.delete(
        "/admin/settings/namespaces/labs-force-clean", params={"delete_cluster_namespace": "false"}
    )
    assert blocked.status_code == 409, blocked.text

    deleted = login_admin.delete(
        "/admin/settings/namespaces/labs-force-clean",
        params={"delete_cluster_namespace": "false", "force_cleanup": "true"},
    )
    assert deleted.status_code == 200, deleted.text
    payload = deleted.json()
    assert payload["blocked"] is False
    assert int(payload["deleted_database_records"]) >= 7
    assert int(payload["deleted_cluster_resources"]) == 4

    with Session(engine) as session:
        assert session.get(Instance, "inst-force-clean") is None
        assert session.get(ContainerInstance, "ct-inst-force-clean") is None
        assert session.get(ImageUploadTask, "upload-force-clean") is None
        assert (
            session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == "labs-force-clean")).first()
            is None
        )


def test_managed_namespace_blocks_disabling_netpol_in_production(login_admin: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(admin_namespaces_routes, "_reconcile_managed_namespace", lambda _row: None)
    monkeypatch.setattr(admin_namespaces_routes.settings, "production_profile", True)
    response = login_admin.post(
        "/admin/settings/namespaces",
        json={
            "namespace": "labs-no-netpol",
            "enabled": False,
            "enforce_network_policies": False,
        },
    )
    assert response.status_code == 422, response.text
    assert "disabling default network policies" in response.json()["detail"].lower()
