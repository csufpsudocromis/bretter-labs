import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
from src.tables import Image, Template
from src.time_utils import utc_now


def _seed_image(*, image_id: str, namespace: str) -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id=image_id,
                name=f"Image {namespace}",
                filename=f"{image_id}.qcow2",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                source_pvc=f"golden-{image_id}",
                checksum=f"sha256-{image_id}",
                size_bytes=2048,
                created_at=utc_now(),
            )
        )
        session.commit()


def _seed_template(*, template_id: str, image_id: str, namespace: str) -> None:
    with Session(engine) as session:
        session.add(
            Template(
                id=template_id,
                name=f"Template {namespace}",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                enabled_namespaces_json=f'["{namespace}"]',
                description="namespace matrix test",
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


def _detail_text(response) -> str:
    payload = response.json()
    detail = payload.get("detail", "")
    if isinstance(detail, (list, dict)):
        return json.dumps(detail).lower()
    return str(detail).lower()


def test_platform_admin_cannot_bind_vm_template_to_foreign_namespace_owned_image(login_admin: TestClient) -> None:
    _seed_image(image_id="img-matrix-a", namespace="labs-a")
    _seed_image(image_id="img-matrix-b", namespace="labs-b")

    response = login_admin.post(
        "/admin/templates",
        json={
            "name": "Invalid Namespace Binding",
            "namespace": "labs-a",
            "description": "cross-namespace image binding should fail",
            "os_type": "windows",
            "image_id": "img-matrix-b",
            "cpu_cores": 2,
            "ram_mb": 2048,
            "auto_delete_minutes": 30,
            "idle_timeout_minutes": 30,
            "enabled": False,
        },
    )
    assert response.status_code == 422, response.text
    assert "cannot use namespace-owned image namespace labs-b" in str(response.json().get("detail", "")).lower()


def test_namespace_admin_cannot_expand_template_enablement_outside_scope(
    login_admin: TestClient, client: TestClient
) -> None:
    _seed_image(image_id="img-ns-admin", namespace="labs-a")
    create_user = login_admin.post(
        "/admin/users",
        json={
            "username": "ns-admin-matrix",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["labs-a"],
        },
    )
    assert create_user.status_code == 201, create_user.text

    login = client.post("/auth/login", json={"username": "ns-admin-matrix", "password": "password"})
    assert login.status_code == 200, login.text

    created = client.post(
        "/admin/templates",
        headers={"X-Bretter-Namespace": "labs-a"},
        json={
            "name": "Scoped Namespace Template",
            "description": "owned by labs-a",
            "os_type": "windows",
            "image_id": "img-ns-admin",
            "cpu_cores": 2,
            "ram_mb": 2048,
            "auto_delete_minutes": 30,
            "idle_timeout_minutes": 30,
            "enabled": False,
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    denied = client.patch(
        f"/admin/templates/{template_id}",
        headers={"X-Bretter-Namespace": "labs-a"},
        json={"enabled_namespaces": ["labs-a", "labs-b"]},
    )
    assert denied.status_code in {403, 422}, denied.text
    detail_text = _detail_text(denied)
    assert any(
        token in detail_text
        for token in (
            "outside actor scope",
            "namespace-owned template can only target its own namespace",
            "namespace enablement access denied",
        )
    ), denied.text


def test_scoped_user_is_denied_cross_namespace_template_access(login_admin: TestClient, client: TestClient) -> None:
    _seed_image(image_id="img-user-a", namespace="labs-a")
    _seed_image(image_id="img-user-b", namespace="labs-b")
    _seed_template(template_id="tmpl-user-a", image_id="img-user-a", namespace="labs-a")
    _seed_template(template_id="tmpl-user-b", image_id="img-user-b", namespace="labs-b")

    created = login_admin.post(
        "/admin/users",
        json={
            "username": "scoped-matrix-user",
            "password": "password",
            "role": "user",
            "is_admin": False,
            "namespace_scopes": ["labs-a"],
        },
    )
    assert created.status_code == 201, created.text

    login = client.post("/auth/login", json={"username": "scoped-matrix-user", "password": "password"})
    assert login.status_code == 200, login.text

    allowed = client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"})
    assert allowed.status_code == 200, allowed.text
    allowed_ids = {row["id"] for row in allowed.json()}
    assert "tmpl-user-a" in allowed_ids
    assert "tmpl-user-b" not in allowed_ids

    denied = client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-b"})
    assert denied.status_code == 403, denied.text
    assert "namespace access denied" in str(denied.json().get("detail", "")).lower()
