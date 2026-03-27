from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
import src.routes.admin as admin_routes
from src.tables import (
    Config,
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    Instance,
    ManagedNamespace,
    Template,
)
from src.time_utils import utc_now


def test_namespace_upload_limit_is_enforced(login_admin: TestClient, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(admin_routes, "_image_dir", lambda: tmp_path)
    with Session(engine) as session:
        session.add(
            ManagedNamespace(
                id="ns-policy-upload",
                namespace="labs",
                team_label="default",
                enabled=True,
                upload_max_bytes=10,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()

    response = login_admin.post(
        "/admin/images",
        headers={"X-Bretter-Namespace": "labs"},
        files={"file": ("policy-limit.qcow2", b"01234567890", "application/octet-stream")},
    )
    assert response.status_code == 413, response.text
    assert "image too large" in str(response.json().get("detail", "")).lower()


def test_namespace_queue_limit_is_enforced_for_container_queue(login_user: TestClient) -> None:
    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        cfg.max_concurrent_vms = 1
        session.add(cfg)

        session.add(
            ManagedNamespace(
                id="ns-policy-queue",
                namespace="labs",
                team_label="default",
                enabled=True,
                queue_max_pending=1,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

        session.add(
            Image(
                id="img-queue-vm",
                name="queue-vm",
                filename="queue-vm.qcow2",
                tenant="global",
                namespace="labs",
                cluster_id="local",
                source_pvc="golden-queue-vm",
                checksum="c" * 64,
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id="tmpl-busy",
                name="busy-template",
                tenant="global",
                namespace="labs",
                cluster_id="local",
                enabled_namespaces_json='["labs"]',
                description="busy",
                os_type="windows",
                image_id="img-queue-vm",
                cpu_cores=1,
                ram_mb=1024,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            Instance(
                id="inst-busy",
                template_id="tmpl-busy",
                owner="admin",
                tenant="global",
                namespace="labs",
                cluster_id="local",
                status="running",
                started_at=utc_now(),
                last_active_at=utc_now(),
            )
        )
        session.add(
            ContainerImage(
                id="ct-img-policy",
                name="ct-policy",
                image_ref="docker.io/library/nginx:1.27",
                tenant="global",
                namespace="labs",
                cluster_id="local",
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerTemplate(
                id="ct-tmpl-policy",
                template_key="ct-policy",
                version=1,
                is_default=True,
                name="ct-policy",
                tenant="global",
                namespace="labs",
                enabled_namespaces_json='["labs"]',
                cluster_id="local",
                container_image_id="ct-img-policy",
                cpu_millicores=500,
                memory_mb=512,
                container_port=80,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerInstance(
                id="ct-queued-existing",
                template_id="ct-tmpl-policy",
                owner="admin",
                tenant="global",
                namespace="labs",
                cluster_id="local",
                status="queued",
                queue_attempts=1,
                queue_reason="queued",
                started_at=utc_now(),
                last_active_at=utc_now(),
            )
        )
        session.commit()

    response = login_user.post(
        "/user/container-templates/ct-tmpl-policy/start",
        headers={"X-Bretter-Namespace": "labs"},
    )
    assert response.status_code == 429, response.text
    assert "namespace queue limit reached" in str(response.json().get("detail", "")).lower()
