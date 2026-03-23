from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
from src.tables import Image
from src.time_utils import utc_now


def test_admin_clusters_lists_local_cluster(login_admin: TestClient) -> None:
    response = login_admin.get("/admin/settings/clusters")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert any(str(item.get("id")) == "local" for item in rows)


def test_admin_can_create_update_and_disable_cluster(login_admin: TestClient) -> None:
    created = login_admin.post(
        "/admin/settings/clusters",
        json={
            "id": "edge-west-1",
            "name": "Edge West",
            "region": "us-west",
            "compliance_tags": ["soc2", "ferpa"],
            "capacity_weight": 180,
            "enabled": True,
            "schedule_enabled": True,
            "runtime_enabled": False,
            "notes": "edge test",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["id"] == "edge-west-1"

    updated = login_admin.patch(
        "/admin/settings/clusters/edge-west-1",
        json={
            "capacity_weight": 250,
            "schedule_enabled": False,
        },
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["capacity_weight"] == 250
    assert payload["schedule_enabled"] is False

    disabled = login_admin.delete("/admin/settings/clusters/edge-west-1")
    assert disabled.status_code == 200, disabled.text
    disabled_payload = disabled.json()
    assert disabled_payload["enabled"] is False
    assert disabled_payload["schedule_enabled"] is False
    assert disabled_payload["runtime_enabled"] is False


def test_admin_can_upsert_team_placement_policy(login_admin: TestClient) -> None:
    # Ensure target cluster exists for preferred_cluster_id validation.
    created_cluster = login_admin.post(
        "/admin/settings/clusters",
        json={
            "id": "central-1",
            "name": "Central 1",
            "region": "us-central",
            "compliance_tags": ["soc2"],
            "capacity_weight": 120,
            "enabled": True,
            "schedule_enabled": True,
            "runtime_enabled": False,
        },
    )
    assert created_cluster.status_code in {201, 409}, created_cluster.text

    upserted = login_admin.put(
        "/admin/settings/placement-policies/default",
        json={
            "preferred_cluster_id": "central-1",
            "hard_pin_cluster": False,
            "required_regions": ["us-central"],
            "required_compliance_tags": ["soc2"],
            "allowed_cluster_ids": ["local", "central-1"],
        },
    )
    assert upserted.status_code == 200, upserted.text
    payload = upserted.json()
    assert payload["team"] == "default"
    assert payload["preferred_cluster_id"] == "central-1"
    assert "us-central" in payload["required_regions"]

    listed = login_admin.get("/admin/settings/placement-policies")
    assert listed.status_code == 200, listed.text
    assert any(item["team"] == "default" for item in listed.json())


def test_admin_can_enqueue_artifact_replication(login_admin: TestClient) -> None:
    created_cluster = login_admin.post(
        "/admin/settings/clusters",
        json={
            "id": "edge-east-1",
            "name": "Edge East",
            "region": "us-east",
            "compliance_tags": [],
            "capacity_weight": 110,
            "enabled": True,
            "schedule_enabled": True,
            "runtime_enabled": False,
        },
    )
    assert created_cluster.status_code in {201, 409}, created_cluster.text

    with Session(engine) as session:
        existing = session.get(Image, "img-mc-1")
        if not existing:
            session.add(
                Image(
                    id="img-mc-1",
                    name="MC Image",
                    filename="mc-image.qcow2",
                    tenant="global",
                    cluster_id="local",
                    source_pvc="golden-images",
                    checksum="abc123",
                    size_bytes=4096,
                    created_at=utc_now(),
                )
            )
            session.commit()

    created = login_admin.post(
        "/admin/replication/artifacts",
        json={
            "artifact_type": "vm_image",
            "artifact_id": "img-mc-1",
            "source_cluster_id": "local",
            "target_cluster_ids": ["edge-east-1"],
            "tenant": "global",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert len(payload) == 1
    assert payload[0]["artifact_type"] == "vm_image"
    assert payload[0]["target_cluster_id"] == "edge-east-1"
    assert payload[0]["status"] == "queued"
