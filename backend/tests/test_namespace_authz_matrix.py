import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.auth import hash_password
from src.db import engine
from src.rbac import Role
from src.tables import ContainerImage, ContainerTemplate, Image, Template, User
from src.time_utils import utc_now


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _seed_namespace_resources() -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id="img-ns-a",
                name="img-a",
                filename="a.qcow2",
                tenant="global",
                namespace="labs-a",
                cluster_id="local",
                source_pvc="golden-a",
                checksum="a" * 64,
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Image(
                id="img-ns-b",
                name="img-b",
                filename="b.qcow2",
                tenant="global",
                namespace="labs-b",
                cluster_id="local",
                source_pvc="golden-b",
                checksum="b" * 64,
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id="tmpl-ns-a",
                name="tmpl-a",
                tenant="global",
                namespace="labs-a",
                enabled_namespaces_json='["labs-a"]',
                cluster_id="local",
                description="A",
                os_type="windows",
                image_id="img-ns-a",
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id="tmpl-ns-b",
                name="tmpl-b",
                tenant="global",
                namespace="labs-b",
                enabled_namespaces_json='["labs-b"]',
                cluster_id="local",
                description="B",
                os_type="windows",
                image_id="img-ns-b",
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerImage(
                id="ct-img-a",
                name="ct-a",
                image_ref="docker.io/library/nginx:1.27",
                tenant="global",
                namespace="labs-a",
                cluster_id="local",
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerImage(
                id="ct-img-b",
                name="ct-b",
                image_ref="docker.io/library/nginx:1.27",
                tenant="global",
                namespace="labs-b",
                cluster_id="local",
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerTemplate(
                id="ct-tmpl-a",
                template_key="ct-a",
                version=1,
                is_default=True,
                name="ct-tmpl-a",
                tenant="global",
                namespace="labs-a",
                enabled_namespaces_json='["labs-a"]',
                cluster_id="local",
                container_image_id="ct-img-a",
                cpu_millicores=500,
                memory_mb=512,
                container_port=80,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            ContainerTemplate(
                id="ct-tmpl-b",
                template_key="ct-b",
                version=1,
                is_default=True,
                name="ct-tmpl-b",
                tenant="global",
                namespace="labs-b",
                enabled_namespaces_json='["labs-b"]',
                cluster_id="local",
                container_image_id="ct-img-b",
                cpu_millicores=500,
                memory_mb=512,
                container_port=80,
                enabled=True,
                created_at=utc_now(),
            )
        )
        session.add(
            User(
                username="ns-matrix",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                namespace_scopes_json=json.dumps(["labs-a"]),
                is_admin=True,
                force_password_change=False,
            )
        )
        session.add(
            User(
                username="user-matrix",
                password_hash=hash_password("password"),
                role=Role.USER,
                namespace_scopes_json=json.dumps(["labs-a"]),
                is_admin=False,
                force_password_change=False,
            )
        )
        session.commit()


def test_namespace_authorization_matrix(client: TestClient) -> None:
    _seed_namespace_resources()

    _login(client, "admin", "admin")
    assert client.get("/admin/images", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/admin/images", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 200
    assert client.get("/admin/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/admin/templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 200
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 200
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 200

    _login(client, "ns-matrix", "password")
    assert client.get("/admin/images", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/admin/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/admin/images", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403
    assert client.get("/admin/templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403

    _login(client, "user-matrix", "password")
    assert client.get("/admin/images", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 403
    assert client.get("/admin/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 403
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-a"}).status_code == 200
    assert client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403
    assert client.get("/user/container-templates", headers={"X-Bretter-Namespace": "labs-b"}).status_code == 403
