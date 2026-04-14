from fastapi.testclient import TestClient
from sqlmodel import Session

from src.db import engine
from src.tables import Image, ManagedNamespace, Template, User
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
                size_bytes=1024,
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


def test_unscoped_user_namespace_resolution_prefers_actor_scope_when_control_namespace_disabled(
    client: TestClient,
) -> None:
    _seed_vm_template(image_id="img-cbe-default", template_id="tmpl-cbe-default", namespace="cbe")
    _seed_vm_template(image_id="img-labs-disabled", template_id="tmpl-labs-disabled", namespace="labs")

    with Session(engine) as session:
        user = session.get(User, "alice")
        assert user is not None
        user.namespace_scopes_json = '["cbe"]'
        session.add(user)
        session.add(ManagedNamespace(id="mn-labs-disabled", namespace="labs", enabled=False))
        session.add(ManagedNamespace(id="mn-cbe-enabled", namespace="cbe", enabled=True))
        session.commit()

    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200, login.text
    user_payload = login.json()["user"]
    assert user_payload["default_namespace"] == "cbe"
    assert user_payload["enabled_namespaces"] == ["cbe"]

    unscoped = client.get("/user/templates")
    assert unscoped.status_code == 200, unscoped.text
    listed_ids = {row["id"] for row in unscoped.json()}
    assert "tmpl-cbe-default" in listed_ids
    assert "tmpl-labs-disabled" not in listed_ids

    denied = client.get("/user/templates", headers={"X-Bretter-Namespace": "labs"})
    assert denied.status_code == 403, denied.text
    assert "namespace access denied" in str(denied.json().get("detail", "")).lower()


def test_unscoped_admin_template_create_uses_actor_scope_when_control_namespace_disabled(
    login_admin: TestClient, client: TestClient
) -> None:
    created = login_admin.post(
        "/admin/users",
        json={
            "username": "lab-admin-cbe",
            "password": "password",
            "role": "lab_admin",
            "is_admin": True,
            "namespace_scopes": ["cbe"],
        },
    )
    assert created.status_code == 201, created.text

    _seed_image(image_id="img-admin-cbe", namespace="cbe")

    with Session(engine) as session:
        session.add(ManagedNamespace(id="mn-labs-disabled-2", namespace="labs", enabled=False))
        session.add(ManagedNamespace(id="mn-cbe-enabled-2", namespace="cbe", enabled=True))
        session.commit()

    login = client.post("/auth/login", json={"username": "lab-admin-cbe", "password": "password"})
    assert login.status_code == 200, login.text

    created_template = client.post(
        "/admin/templates",
        json={
            "name": "Unscoped Template Create",
            "description": "created without explicit namespace header",
            "os_type": "windows",
            "image_id": "img-admin-cbe",
            "cpu_cores": 2,
            "ram_mb": 2048,
            "auto_delete_minutes": 30,
            "idle_timeout_minutes": 30,
            "enabled": False,
        },
    )
    assert created_template.status_code == 201, created_template.text
    payload = created_template.json()
    assert payload["namespace"] == "cbe"

    denied = client.post(
        "/admin/templates",
        headers={"X-Bretter-Namespace": "labs"},
        json={
            "name": "Denied Cross Namespace Template",
            "description": "should fail",
            "os_type": "windows",
            "image_id": "img-admin-cbe",
            "cpu_cores": 2,
            "ram_mb": 2048,
            "auto_delete_minutes": 30,
            "idle_timeout_minutes": 30,
            "enabled": False,
        },
    )
    assert denied.status_code == 403, denied.text
