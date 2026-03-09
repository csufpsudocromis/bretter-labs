from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.auth import hash_password
from src.db import engine
from src.rbac import Role
from src.tables import ContainerImage, ContainerTemplate, Image, Template, Token, User
from src.time_utils import utc_now

SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."


def _seed_vm_template() -> None:
    with Session(engine) as session:
        session.add(
            Image(
                id="img-vm-1",
                name="Windows Base",
                filename="windows-base.qcow2",
                checksum="sha256:test",
                size_bytes=1024,
                source_pvc="golden-images-vm",
            )
        )
        session.add(
            Template(
                id="tmpl-vm-1",
                name="Windows Lab",
                description="VM template",
                os_type="windows",
                image_id="img-vm-1",
                cpu_cores=2,
                ram_mb=4096,
                auto_delete_minutes=60,
                idle_timeout_minutes=30,
                enabled=True,
                network_mode="bridge",
            )
        )
        session.commit()


def _seed_container_template() -> None:
    with Session(engine) as session:
        session.add(
            ContainerImage(
                id="img-ct-1",
                name="Kimai",
                image_ref="docker.io/library/nginx:stable",
            )
        )
        session.add(
            ContainerTemplate(
                id="tmpl-ct-1",
                template_key="kimai",
                version=1,
                is_default=True,
                name="Kimai Lab",
                description="Container template",
                container_image_id="img-ct-1",
                cpu_millicores=1000,
                memory_mb=1024,
                container_port=80,
                healthcheck_protocol="tcp",
                healthcheck_path="/",
                startup_timeout_seconds=300,
                expose_strategy="nodeport",
                network_mode="bridge",
                enabled=True,
                idle_timeout_minutes=30,
            )
        )
        session.commit()


def test_cookie_auth_login_me_logout(client: TestClient):
    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200
    payload = login.json()
    assert "token" not in payload
    set_cookie = login.headers.get("set-cookie", "")
    assert "blabs_session=" in set_cookie
    assert "HttpOnly" in set_cookie

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    unauthorized = client.get("/auth/me")
    assert unauthorized.status_code == 401


def test_rbac_user_cannot_access_admin(client: TestClient):
    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200
    forbidden = client.get("/admin/settings/site")
    assert forbidden.status_code == 403


def test_rbac_viewer_has_read_only_admin_access(client: TestClient):
    with Session(engine) as session:
        session.add(
            User(
                username="viewer1",
                password_hash=hash_password("password"),
                role=Role.VIEWER,
                is_admin=True,
                force_password_change=False,
            )
        )
        session.commit()

    login = client.post("/auth/login", json={"username": "viewer1", "password": "password"})
    assert login.status_code == 200, login.text
    user = login.json()["user"]
    assert user["role"] == Role.VIEWER
    assert user["can_access_admin"] is True
    assert user["is_admin"] is True

    site_read = client.get("/admin/settings/site")
    assert site_read.status_code == 200, site_read.text

    blocked_write = client.post("/admin/settings/idle-timeout", json={"idle_timeout_minutes": 30})
    assert blocked_write.status_code == 403
    assert "missing permission" in blocked_write.json()["detail"]


def test_rbac_rejects_invalid_role_on_user_create(login_admin: TestClient):
    bad_create = login_admin.post(
        "/admin/users",
        json={"username": "badroleuser", "password": "password", "role": "superuser"},
    )
    assert bad_create.status_code == 422
    assert "invalid role" in bad_create.json()["detail"]


def test_cookie_auth_session_ttl_is_enforced(client: TestClient, monkeypatch):
    monkeypatch.setattr("src.auth.settings.auth_cookie_ttl_seconds", 60)

    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200
    token_value = client.cookies.get("blabs_session")
    assert token_value

    with Session(engine) as session:
        token = session.get(Token, token_value)
        assert token is not None
        token.issued_at = utc_now() - timedelta(seconds=120)
        session.add(token)
        session.commit()

    expired = client.get("/auth/me")
    assert expired.status_code == 401
    assert expired.json()["detail"] == "session expired"

    with Session(engine) as session:
        assert session.get(Token, token_value) is None


def test_vm_and_container_lifecycle_with_single_active_lab_enforced(login_user: TestClient):
    _seed_vm_template()
    _seed_container_template()

    start_vm = login_user.post("/user/templates/tmpl-vm-1/start")
    assert start_vm.status_code == 201, start_vm.text
    vm_id = start_vm.json()["id"]

    blocked_second_start = login_user.post("/user/container-templates/tmpl-ct-1/start")
    assert blocked_second_start.status_code == 429
    assert blocked_second_start.json()["detail"] == SINGLE_LAB_LIMIT_MESSAGE

    vm_activity = login_user.post(f"/user/pods/{vm_id}/activity")
    assert vm_activity.status_code == 204
    vm_stop = login_user.post(f"/user/pods/{vm_id}/stop")
    assert vm_stop.status_code == 200
    vm_restart = login_user.post(f"/user/pods/{vm_id}/start")
    assert vm_restart.status_code == 200
    vm_delete = login_user.delete(f"/user/pods/{vm_id}")
    assert vm_delete.status_code == 204

    start_container = login_user.post("/user/container-templates/tmpl-ct-1/start")
    assert start_container.status_code == 201, start_container.text
    container_id = start_container.json()["id"]

    container_activity = login_user.post(f"/user/containers/{container_id}/activity")
    assert container_activity.status_code == 204
    container_stop = login_user.post(f"/user/containers/{container_id}/stop")
    assert container_stop.status_code == 200
    container_restart = login_user.post(f"/user/containers/{container_id}/start")
    assert container_restart.status_code == 200
    container_delete = login_user.delete(f"/user/containers/{container_id}")
    assert container_delete.status_code == 204


def test_container_connect_tokens_are_one_time_and_not_url_based(login_user: TestClient):
    _seed_container_template()

    started = login_user.post("/user/container-templates/tmpl-ct-1/start")
    assert started.status_code == 201, started.text
    container_id = started.json()["id"]

    token_response = login_user.post(f"/user/containers/{container_id}/connect-token")
    assert token_response.status_code == 200, token_response.text
    connect_url = token_response.json()["connect_url"]
    assert "ct=" not in connect_url

    grant_cookie = login_user.cookies.get("blabs_connect_grant")
    assert grant_cookie

    with TestClient(login_user.app) as first_use_client:
        first_use_client.cookies.set(
            "blabs_connect_grant",
            grant_cookie,
            path=f"/user/containers/{container_id}/connect/",
        )
        first_use = first_use_client.get(f"/user/containers/{container_id}/connect/__blabs_idle_bridge.js")
        assert first_use.status_code == 200
        assert "blabs_connect_session" in first_use.headers.get("set-cookie", "")

    with TestClient(login_user.app) as replay_client:
        replay_client.cookies.set(
            "blabs_connect_grant",
            grant_cookie,
            path=f"/user/containers/{container_id}/connect/",
        )
        replay = replay_client.get(f"/user/containers/{container_id}/connect/__blabs_idle_bridge.js")
        assert replay.status_code == 401
        assert "invalid connect token" in replay.text.lower()


def test_admin_can_upload_and_serve_local_login_background(login_admin: TestClient):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"test-image-bytes"
    upload = login_admin.post(
        "/admin/settings/site/background",
        files={"file": ("login-bg.png", fake_png, "image/png")},
    )
    assert upload.status_code == 201, upload.text
    payload = upload.json()
    assert payload["theme_bg_image"].startswith("/user/site-assets/")
    assert payload["size_bytes"] == len(fake_png)

    site = login_admin.get("/user/settings/site")
    assert site.status_code == 200
    assert site.json()["theme_bg_image"] == payload["theme_bg_image"]

    asset = login_admin.get(payload["theme_bg_image"])
    assert asset.status_code == 200
    assert asset.content == fake_png
