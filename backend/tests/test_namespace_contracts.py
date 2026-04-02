from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
from src.tables import Image, Template
from src.time_utils import utc_now


def _seed_vm_template(*, image_id: str, template_id: str, namespace: str) -> None:
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
                size_bytes=1024,
                created_at=utc_now(),
            )
        )
        session.add(
            Template(
                id=template_id,
                name=f"Template {namespace}",
                tenant="global",
                namespace=namespace,
                cluster_id="local",
                description="namespace contract test",
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


def test_user_role_namespace_scopes_round_trip_and_cross_namespace_denial(
    login_admin: TestClient, client: TestClient
) -> None:
    created = login_admin.post(
        "/admin/users",
        json={
            "username": "scoped-user",
            "password": "password",
            "role": "user",
            "is_admin": False,
            "namespace_scopes": ["labs-a", "labs-b"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["namespace_scopes"] == ["labs-a", "labs-b"]

    _seed_vm_template(image_id="img-scope-a", template_id="tmpl-scope-a", namespace="labs-a")
    _seed_vm_template(image_id="img-scope-c", template_id="tmpl-scope-c", namespace="labs-c")

    login = client.post("/auth/login", json={"username": "scoped-user", "password": "password"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["namespace_scopes"] == ["labs-a", "labs-b"]

    in_scope = client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-a"})
    assert in_scope.status_code == 200, in_scope.text
    in_scope_ids = {item["id"] for item in in_scope.json()}
    assert "tmpl-scope-a" in in_scope_ids
    assert "tmpl-scope-c" not in in_scope_ids

    out_of_scope = client.get("/user/templates", headers={"X-Bretter-Namespace": "labs-c"})
    assert out_of_scope.status_code == 403, out_of_scope.text


def test_namespace_admin_cannot_assign_user_namespace_scopes_outside_actor_scope(
    login_admin: TestClient, client: TestClient
) -> None:
    created = login_admin.post(
        "/admin/users",
        json={
            "username": "ns-admin-scope",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["labs-a"],
        },
    )
    assert created.status_code == 201, created.text

    ns_login = client.post("/auth/login", json={"username": "ns-admin-scope", "password": "password"})
    assert ns_login.status_code == 200, ns_login.text

    denied = client.post(
        "/admin/users",
        json={
            "username": "bad-scope-user",
            "password": "password",
            "role": "user",
            "is_admin": False,
            "namespace_scopes": ["labs-b"],
        },
    )
    assert denied.status_code == 403, denied.text
    assert "outside actor scope" in str(denied.json().get("detail", ""))

    allowed = client.post(
        "/admin/users",
        json={
            "username": "good-scope-user",
            "password": "password",
            "role": "user",
            "is_admin": False,
            "namespace_scopes": ["labs-a"],
        },
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["namespace_scopes"] == ["labs-a"]
