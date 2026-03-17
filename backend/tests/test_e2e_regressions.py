from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session

from src.auth import connect_token_storage_key, hash_password, lookup_session_token
from src.db import engine
from src.rbac import Role
from src.tables import Config, ConnectToken, ContainerImage, ContainerTemplate, Image, TeamQuota, Template, User
from src.time_utils import utc_now

SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."


def _seed_vm_template(*, console_provider: str = "spice") -> None:
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
                console_provider=console_provider,
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


def test_admin_can_read_and_update_ldap_settings(login_admin: TestClient):
    read = login_admin.get("/admin/settings/ldap")
    assert read.status_code == 200, read.text
    assert read.json()["ldap_enabled"] is False
    assert read.json()["ldap_bind_password_configured"] is False

    payload = {
        "ldap_enabled": True,
        "ldap_server_uri": "ldaps://ldap.example.edu:636",
        "ldap_bind_dn": "cn=svc,dc=example,dc=edu",
        "ldap_bind_password": "secret",
        "ldap_user_base_dn": "ou=users,dc=example,dc=edu",
        "ldap_user_filter": "(uid={username})",
        "ldap_start_tls": False,
        "ldap_insecure_skip_verify": False,
        "ldap_timeout_seconds": 12,
        "ldap_auto_create_users": True,
    }
    write = login_admin.patch("/admin/settings/ldap", json=payload)
    assert write.status_code == 200, write.text
    body = write.json()
    assert body["ldap_enabled"] is True
    assert body["ldap_server_uri"] == payload["ldap_server_uri"]
    assert body["ldap_timeout_seconds"] == 12
    assert body["ldap_bind_password_configured"] is True

    bad_filter = dict(payload)
    bad_filter["ldap_user_filter"] = "(uid=test)"
    reject = login_admin.patch("/admin/settings/ldap", json=bad_filter)
    assert reject.status_code == 422


def test_admin_secret_settings_are_write_only(login_admin: TestClient):
    sso_read = login_admin.get("/admin/settings/sso")
    assert sso_read.status_code == 200, sso_read.text
    assert "sso_client_secret" not in sso_read.json()

    ldap_read = login_admin.get("/admin/settings/ldap")
    assert ldap_read.status_code == 200, ldap_read.text
    assert "ldap_bind_password" not in ldap_read.json()


def test_login_rate_limit_blocks_repeated_failures(client: TestClient, monkeypatch):
    monkeypatch.setattr("src.routes.auth.settings.auth_login_rate_limit_max_attempts", 2)
    monkeypatch.setattr("src.routes.auth.settings.auth_login_lockout_seconds", 60)
    monkeypatch.setattr("src.routes.auth.settings.auth_login_rate_limit_window_seconds", 120)

    first = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert first.status_code == 401, first.text

    second = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert second.status_code == 429, second.text
    assert "Retry-After" in second.headers

    blocked_valid = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert blocked_valid.status_code == 429, blocked_valid.text


def test_encrypted_ldap_bind_password_is_used_at_runtime(login_admin: TestClient, client: TestClient, monkeypatch):
    monkeypatch.setattr("src.secret_codec.settings.secrets_encryption_key", "unit-test-secret-key")
    monkeypatch.setattr("src.routes.auth.settings.secrets_encryption_key", "unit-test-secret-key")

    payload = {
        "ldap_enabled": True,
        "ldap_server_uri": "ldaps://ldap.example.edu:636",
        "ldap_bind_dn": "cn=svc,dc=example,dc=edu",
        "ldap_bind_password": "bind-pass-123",
        "ldap_user_base_dn": "ou=users,dc=example,dc=edu",
        "ldap_user_filter": "(uid={username})",
        "ldap_start_tls": False,
        "ldap_insecure_skip_verify": False,
        "ldap_timeout_seconds": 12,
        "ldap_auto_create_users": True,
    }
    write = login_admin.patch("/admin/settings/ldap", json=payload)
    assert write.status_code == 200, write.text

    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        assert str(cfg.ldap_bind_password).startswith("enc:v1:")

    def _ldap_ok(username, password, cfg):
        assert cfg.bind_password == "bind-pass-123"
        return True, "dn"

    monkeypatch.setattr("src.routes.auth.ldap_authenticate", _ldap_ok)
    login = client.post("/auth/login", json={"username": "ldapenc", "password": "ldap-pass"})
    assert login.status_code == 200, login.text


def test_ldap_login_auto_provisions_user(client: TestClient, monkeypatch):
    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        cfg.ldap_enabled = True
        cfg.ldap_server_uri = "ldaps://ldap.example.edu:636"
        cfg.ldap_user_base_dn = "ou=users,dc=example,dc=edu"
        cfg.ldap_user_filter = "(uid={username})"
        cfg.ldap_auto_create_users = True
        session.add(cfg)
        session.commit()

    monkeypatch.setattr("src.routes.auth.ldap_authenticate", lambda username, password, cfg: (True, "dn"))

    login = client.post("/auth/login", json={"username": "ldapuser", "password": "ldap-pass"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["username"] == "ldapuser"
    assert login.json()["user"]["role"] == Role.USER

    with Session(engine) as session:
        created = session.get(User, "ldapuser")
        assert created is not None
        assert created.role == Role.USER


def test_ldap_login_requires_existing_local_user_when_auto_create_disabled(client: TestClient, monkeypatch):
    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        cfg.ldap_enabled = True
        cfg.ldap_server_uri = "ldaps://ldap.example.edu:636"
        cfg.ldap_user_base_dn = "ou=users,dc=example,dc=edu"
        cfg.ldap_user_filter = "(uid={username})"
        cfg.ldap_auto_create_users = False
        session.add(cfg)
        session.commit()

    monkeypatch.setattr("src.routes.auth.ldap_authenticate", lambda username, password, cfg: (True, "dn"))

    login = client.post("/auth/login", json={"username": "new-ldap-user", "password": "ldap-pass"})
    assert login.status_code == 403, login.text


def test_rbac_rejects_invalid_role_on_user_create(login_admin: TestClient):
    bad_create = login_admin.post(
        "/admin/users",
        json={"username": "badroleuser", "password": "password", "role": "superuser"},
    )
    assert bad_create.status_code == 422
    assert "invalid role" in bad_create.json()["detail"]


def test_oidc_start_and_callback_issue_session_cookie(client: TestClient, monkeypatch):
    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        cfg.sso_enabled = True
        cfg.sso_client_id = "oidc-client"
        cfg.sso_client_secret = "oidc-secret"
        cfg.sso_authorize_url = "https://idp.example.com/oauth2/v2/auth"
        cfg.sso_token_url = "https://idp.example.com/oauth2/v2/token"
        cfg.sso_userinfo_url = "https://idp.example.com/oauth2/v2/userinfo"
        cfg.sso_redirect_url = "https://10.68.49.250:30080/auth/sso/callback"
        session.add(cfg)
        session.commit()

    class _FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self):
            return self._payload

    def _fake_post(url, data, headers, timeout):
        assert "token" in url
        assert data["code"] == "test-code"
        assert data["code_verifier"]
        return _FakeResponse({"access_token": "access-123"})

    def _fake_get(url, headers, timeout):
        assert "userinfo" in url
        assert headers["Authorization"] == "Bearer access-123"
        return _FakeResponse({"preferred_username": "oidcuser"})

    monkeypatch.setattr("src.routes.auth.requests.post", _fake_post)
    monkeypatch.setattr("src.routes.auth.requests.get", _fake_get)

    start = client.get(
        "/auth/sso/start",
        params={"return_to": "https://10.68.49.250:30073/"},
        headers={"origin": "https://10.68.49.250:30073"},
    )
    assert start.status_code == 200, start.text
    authorize_url = start.json()["authorize_url"]
    parsed = urlparse(authorize_url)
    params = parse_qs(parsed.query)
    state = params["state"][0]
    assert params["code_challenge_method"][0] == "S256"

    callback = client.get(
        "/auth/sso/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in {302, 307}
    assert callback.headers["location"] == "https://10.68.49.250:30073/"

    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "oidcuser"


def test_cookie_auth_session_ttl_is_enforced(client: TestClient, monkeypatch):
    monkeypatch.setattr("src.auth.settings.auth_cookie_ttl_seconds", 60)

    login = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert login.status_code == 200
    token_value = client.cookies.get("blabs_session")
    assert token_value

    with Session(engine) as session:
        token = lookup_session_token(session, token_value)
        assert token is not None
        assert token.token != token_value
        token.issued_at = utc_now() - timedelta(seconds=120)
        session.add(token)
        session.commit()

    expired = client.get("/auth/me")
    assert expired.status_code == 401
    assert expired.json()["detail"] == "session expired"

    with Session(engine) as session:
        assert lookup_session_token(session, token_value) is None


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


def test_vm_connect_token_uses_spice_embed_for_spice_templates(login_user: TestClient):
    _seed_vm_template(console_provider="spice")

    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    token_response = login_user.post(f"/user/pods/{vm_id}/connect-token")
    assert token_response.status_code == 200, token_response.text
    connect_url = token_response.json()["connect_url"]
    assert "/connect/spice-embed.html" in connect_url
    assert "password=" in connect_url


def test_vm_connect_token_uses_vnc_console_for_guacamole_templates(login_user: TestClient):
    _seed_vm_template(console_provider="guacamole")

    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    token_response = login_user.post(f"/user/pods/{vm_id}/connect-token")
    assert token_response.status_code == 200, token_response.text
    connect_url = token_response.json()["connect_url"]
    assert "/connect/vnc.html" in connect_url
    assert "password=" not in connect_url


def test_admin_template_console_provider_round_trip(login_admin: TestClient):
    with Session(engine) as session:
        session.add(
            Image(
                id="img-vm-admin-1",
                name="Admin VM Image",
                filename="admin-vm.qcow2",
                checksum="sha256:admin",
                size_bytes=4096,
                source_pvc="golden-images-vm",
            )
        )
        session.commit()

    payload = {
        "name": "Admin VM",
        "description": "provider test",
        "os_type": "windows",
        "image_id": "img-vm-admin-1",
        "cpu_cores": 2,
        "ram_mb": 4096,
        "auto_delete_minutes": 30,
        "idle_timeout_minutes": 30,
        "console_provider": "guacamole",
    }
    created = login_admin.post("/admin/templates", json=payload)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    assert created.json()["console_provider"] == "guacamole"

    listed = login_admin.get("/admin/templates")
    assert listed.status_code == 200, listed.text
    matched = [item for item in listed.json() if item["id"] == template_id]
    assert matched
    assert matched[0]["console_provider"] == "guacamole"

    updated = login_admin.patch(f"/admin/templates/{template_id}", json={"console_provider": "spice"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["console_provider"] == "spice"


def test_team_namespace_quota_caps_launch_and_idle_timeout(login_user: TestClient):
    _seed_vm_template()
    _seed_container_template()

    with Session(engine) as session:
        session.add(
            TeamQuota(
                id="quota-alpha-labs",
                team="default",
                namespace="labs",
                max_cpu_millicores=1500,
                idle_timeout_minutes_cap=5,
                enabled=True,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()

    vm_templates = login_user.get("/user/templates")
    assert vm_templates.status_code == 200, vm_templates.text
    assert vm_templates.json()[0]["idle_timeout_minutes"] == 5

    container_templates = login_user.get("/user/container-templates")
    assert container_templates.status_code == 200, container_templates.text
    assert container_templates.json()[0]["idle_timeout_minutes"] == 5

    blocked_vm = login_user.post("/user/templates/tmpl-vm-1/start")
    assert blocked_vm.status_code == 429, blocked_vm.text
    assert "CPU cap" in blocked_vm.json()["detail"]


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
    with Session(engine) as session:
        stored_grant = session.get(ConnectToken, connect_token_storage_key(grant_cookie))
        assert stored_grant is not None
        assert stored_grant.token != grant_cookie

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
