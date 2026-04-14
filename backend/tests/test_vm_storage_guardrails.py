from fastapi.testclient import TestClient
from sqlmodel import Session

import src.routes.user as user_routes
from src.db import engine
from src.tables import Image, Template
from src.time_utils import utc_now


def _seed_ready_vm_template(*, template_id: str, image_id: str, namespace: str = "labs") -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id=image_id,
                name="Storage Guardrail Image",
                filename=f"{image_id}.qcow2",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                source_pvc=f"golden-{image_id}",
                checksum=f"sha256-{image_id}",
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id=template_id,
                name="Storage Guardrail Template",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                enabled_namespaces_json=f'["{namespace}"]',
                description="storage guardrail test",
                os_type="windows",
                image_id=image_id,
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.commit()


def test_vm_start_blocks_when_storage_admission_fails(client: TestClient, monkeypatch) -> None:
    _seed_ready_vm_template(template_id="tmpl-storage-blocked", image_id="img-storage-blocked")
    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200, login.text

    monkeypatch.setattr(
        user_routes,
        "evaluate_vm_storage_launch_admission",
        lambda _kube, namespace: (
            False,
            f"Launch blocked: storage provisioning appears degraded in {namespace} (pending PVC backlog).",
        ),
    )

    response = client.post("/user/templates/tmpl-storage-blocked/start", headers={"X-Bretter-Namespace": "labs"})
    assert response.status_code == 503, response.text
    assert "storage provisioning appears degraded" in str(response.json().get("detail", "")).lower()


def test_vm_start_maps_storage_scheduler_errors_to_clear_message(client: TestClient, monkeypatch) -> None:
    _seed_ready_vm_template(template_id="tmpl-storage-map", image_id="img-storage-map")
    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200, login.text

    def _raise_storage_error(*_args, **_kwargs):
        raise RuntimeError(
            "0/2 nodes are available: pod has unbound immediate PersistentVolumeClaims. preemption is not helpful."
        )

    monkeypatch.setattr(user_routes.kube, "create_pod", _raise_storage_error)

    response = client.post("/user/templates/tmpl-storage-map/start", headers={"X-Bretter-Namespace": "labs"})
    assert response.status_code == 503, response.text
    assert "waiting for storage resources" in str(response.json().get("detail", "")).lower()
