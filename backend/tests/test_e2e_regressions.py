from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from kubernetes.client import ApiException
from sqlmodel import Session

from src.auth import connect_token_storage_key, hash_password, lookup_session_token
from src.config import settings
from src.db import engine
from src.rbac import Role
from src.services.kubernetes import PodStatus, kube
from src.tables import Config, ConnectToken, ContainerImage, ContainerTemplate, Image, TeamQuota, Template, User
from src.time_utils import utc_now

SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."


def _seed_vm_template(
    *,
    console_provider: str = "spice",
    rdp_default_username: str = "",
    rdp_default_password: str = "",
) -> None:
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
                rdp_default_username=rdp_default_username,
                rdp_default_password=rdp_default_password,
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


def test_rbac_lab_admin_has_expected_admin_access(client: TestClient):
    with Session(engine) as session:
        session.add(
            User(
                username="labadmin1",
                password_hash=hash_password("password"),
                role=Role.LAB_ADMIN,
                is_admin=True,
                force_password_change=False,
            )
        )
        session.commit()

    login = client.post("/auth/login", json={"username": "labadmin1", "password": "password"})
    assert login.status_code == 200, login.text
    user = login.json()["user"]
    assert user["role"] == Role.LAB_ADMIN
    assert user["can_access_admin"] is True
    assert user["is_admin"] is True

    images_read = client.get("/admin/images")
    assert images_read.status_code == 200, images_read.text

    templates_read = client.get("/admin/templates")
    assert templates_read.status_code == 200, templates_read.text

    blocked_read = client.get("/admin/settings/site")
    assert blocked_read.status_code == 403
    assert "missing permission" in blocked_read.json()["detail"]

    blocked_write = client.post("/admin/settings/idle-timeout", json={"idle_timeout_minutes": 30})
    assert blocked_write.status_code == 403
    assert "missing permission" in blocked_write.json()["detail"]


def test_admin_roles_catalog_exposes_canonical_roles(login_admin: TestClient):
    response = login_admin.get("/admin/users/roles")
    assert response.status_code == 200, response.text
    payload = response.json()
    roles = [item["role"] for item in payload]
    assert roles == [Role.USER, Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN]
    assert any(item["role"] == Role.LAB_ADMIN and "admin.images.write" in item["permissions"] for item in payload)


def test_platform_admin_can_manage_custom_roles(login_admin: TestClient):
    catalog = login_admin.get("/admin/settings/roles")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert "admin.images.read" in body["permission_catalog"]
    assert any(item["role"] == "platform_admin" and item["editable"] is False for item in body["roles"])

    create = login_admin.post(
        "/admin/settings/roles",
        json={
            "role": "support_admin",
            "label": "Support Admin",
            "description": "Can read and operate images/templates",
            "permissions": ["admin.access", "admin.images.read", "admin.templates.read"],
        },
    )
    assert create.status_code == 201, create.text
    assert create.json()["role"] == "support_admin"
    assert create.json()["deletable"] is True

    update = login_admin.patch(
        "/admin/settings/roles/support_admin",
        json={
            "description": "Updated description",
            "permissions": ["admin.access", "admin.images.read", "admin.images.write"],
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["description"] == "Updated description"
    assert "admin.images.write" in update.json()["permissions"]

    delete = login_admin.delete("/admin/settings/roles/support_admin")
    assert delete.status_code == 204, delete.text

    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        assert "support_admin" not in str(cfg.rbac_roles_json or "")


def test_namespace_admin_cannot_assign_high_privilege_custom_role(client: TestClient):
    admin_login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert admin_login.status_code == 200, admin_login.text
    create = client.post(
        "/admin/settings/roles",
        json={
            "role": "security_admin",
            "label": "Security Admin",
            "description": "High-privilege role",
            "permissions": ["admin.access", "admin.settings.write", "admin.users.write"],
        },
    )
    assert create.status_code == 201, create.text

    with Session(engine) as session:
        session.add(
            User(
                username="nsadmin1",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                namespace_scopes_json='["labs"]',
                is_admin=True,
                force_password_change=False,
            )
        )
        session.commit()

    ns_login = client.post("/auth/login", json={"username": "nsadmin1", "password": "password"})
    assert ns_login.status_code == 200, ns_login.text

    assign = client.post(
        "/admin/users",
        json={"username": "role-test-user", "password": "password", "role": "security_admin", "is_admin": True},
    )
    assert assign.status_code == 403, assign.text


def test_namespace_admin_scopes_can_be_assigned_and_updated(login_admin: TestClient):
    create = login_admin.post(
        "/admin/users",
        json={
            "username": "ns-owner",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["labs", "labs-team-red", "LABS"],
        },
    )
    assert create.status_code == 201, create.text
    created = create.json()
    assert created["role"] == "namespace_admin"
    assert created["namespace_scopes"] == ["labs", "labs-team-red"]

    updated = login_admin.patch(
        "/admin/users/ns-owner",
        json={
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["labs-team-blue", "labs-team-green"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["namespace_scopes"] == ["labs-team-blue", "labs-team-green"]

    demoted = login_admin.patch(
        "/admin/users/ns-owner",
        json={
            "role": "user",
            "is_admin": False,
        },
    )
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["role"] == "user"
    assert demoted.json()["namespace_scopes"] == []


def test_namespace_admin_scopes_reject_invalid_namespace(login_admin: TestClient):
    create = login_admin.post(
        "/admin/users",
        json={
            "username": "ns-owner-invalid",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["bad namespace"],
        },
    )
    assert create.status_code == 422, create.text
    assert "invalid namespace scope" in create.json()["detail"]


def test_namespace_admin_login_returns_namespace_scopes(client: TestClient) -> None:
    admin_login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert admin_login.status_code == 200, admin_login.text

    create = client.post(
        "/admin/users",
        json={
            "username": "ns-login-check",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": ["test-namespace"],
        },
    )
    assert create.status_code == 201, create.text

    login = client.post("/auth/login", json={"username": "ns-login-check", "password": "password"})
    assert login.status_code == 200, login.text
    payload = login.json()["user"]
    assert payload["role"] == "namespace_admin"
    assert payload["namespace_scopes"] == ["test-namespace"]


def test_namespace_admin_login_rejects_empty_namespace_scopes(client: TestClient) -> None:
    admin_login = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert admin_login.status_code == 200, admin_login.text

    create = client.post(
        "/admin/users",
        json={
            "username": "ns-login-empty-scope",
            "password": "password",
            "role": "namespace_admin",
            "is_admin": True,
            "namespace_scopes": [],
        },
    )
    assert create.status_code == 201, create.text

    login = client.post("/auth/login", json={"username": "ns-login-empty-scope", "password": "password"})
    assert login.status_code == 403, login.text
    assert "namespace scopes" in str(login.json().get("detail", "")).lower()


def test_namespace_admin_can_only_manage_vm_template_enablement_within_scope(client: TestClient):
    with Session(engine) as session:
        session.add(
            Image(
                id="img-enable-vm-1",
                name="Enable VM Image",
                filename="enable-vm.qcow2",
                checksum="sha256:enable-vm",
                size_bytes=2048,
                source_pvc="golden-images-vm",
                namespace="labs",
            )
        )
        session.add(
            Template(
                id="tmpl-enable-vm-1",
                name="Enable VM Template",
                description="scope enforcement",
                os_type="windows",
                image_id="img-enable-vm-1",
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                namespace="labs",
                enabled_namespaces_json='["labs"]',
            )
        )
        session.add(
            User(
                username="ns-enable-admin",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                is_admin=True,
                namespace_scopes_json='["labs-team-red"]',
            )
        )
        session.commit()

    ns_login = client.post("/auth/login", json={"username": "ns-enable-admin", "password": "password"})
    assert ns_login.status_code == 200, ns_login.text

    allowed = client.patch("/admin/templates/tmpl-enable-vm-1", json={"enabled_namespaces": ["labs-team-red"]})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["enabled_namespaces"] == ["labs-team-red"]

    denied = client.patch("/admin/templates/tmpl-enable-vm-1", json={"enabled_namespaces": ["labs"]})
    assert denied.status_code == 403, denied.text
    assert "namespace enablement access denied" in denied.json()["detail"]


def test_namespace_admin_can_only_manage_container_template_enablement_within_scope(client: TestClient):
    with Session(engine) as session:
        session.add(
            ContainerImage(
                id="img-enable-ct-1",
                name="Enable CT Image",
                image_ref="docker.io/library/nginx:stable",
                namespace="labs",
            )
        )
        session.add(
            ContainerTemplate(
                id="tmpl-enable-ct-1",
                template_key="enable-ct",
                version=1,
                is_default=True,
                name="Enable CT Template",
                description="scope enforcement",
                container_image_id="img-enable-ct-1",
                cpu_millicores=500,
                memory_mb=512,
                container_port=80,
                healthcheck_protocol="tcp",
                healthcheck_path="/",
                startup_timeout_seconds=300,
                expose_strategy="nodeport",
                network_mode="bridge",
                enabled=True,
                namespace="labs",
                enabled_namespaces_json='["labs"]',
                idle_timeout_minutes=30,
            )
        )
        session.add(
            User(
                username="ns-enable-admin-ct",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                is_admin=True,
                namespace_scopes_json='["labs-team-red"]',
            )
        )
        session.commit()

    ns_login = client.post("/auth/login", json={"username": "ns-enable-admin-ct", "password": "password"})
    assert ns_login.status_code == 200, ns_login.text

    allowed = client.patch(
        "/admin/container-templates/tmpl-enable-ct-1",
        json={"enabled_namespaces": ["labs-team-red"]},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["enabled_namespaces"] == ["labs-team-red"]

    denied = client.patch("/admin/container-templates/tmpl-enable-ct-1", json={"enabled_namespaces": ["labs"]})
    assert denied.status_code == 403, denied.text
    assert "namespace enablement access denied" in denied.json()["detail"]


def test_legacy_role_alias_normalizes_to_lab_admin(client: TestClient):
    with Session(engine) as session:
        session.add(
            User(
                username="legacy-ops",
                password_hash=hash_password("password"),
                role="lab_operator",
                is_admin=True,
                force_password_change=False,
            )
        )
        session.commit()

    login = client.post("/auth/login", json={"username": "legacy-ops", "password": "password"})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["role"] == Role.LAB_ADMIN

    with Session(engine) as session:
        user = session.get(User, "legacy-ops")
        assert user is not None
        assert user.role == Role.LAB_ADMIN


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


def test_admin_can_read_and_update_oidc_role_mapping_settings(login_admin: TestClient):
    read = login_admin.get("/admin/settings/sso")
    assert read.status_code == 200, read.text
    read_body = read.json()
    assert read_body["sso_role_claim"] == "groups"
    assert read_body["sso_default_role"] == "user"
    assert read_body["sso_role_mappings"] == {}
    assert read_body["sso_auto_create_users"] is True
    assert read_body["sso_sync_roles_on_login"] is True

    payload = {
        "sso_enabled": True,
        "sso_provider": "Keycloak",
        "sso_client_id": "labs-app",
        "sso_client_secret": "oidc-secret",
        "sso_authorize_url": "https://idp.example.com/auth",
        "sso_token_url": "https://idp.example.com/token",
        "sso_userinfo_url": "https://idp.example.com/userinfo",
        "sso_redirect_url": "https://labs.example.com/auth/sso/callback",
        "sso_role_claim": "groups",
        "sso_default_role": "lab_admin",
        "sso_role_mappings": {
            "admins": "platform_admin",
            "ops": "lab_admin",
        },
        "sso_auto_create_users": True,
        "sso_sync_roles_on_login": True,
    }
    write = login_admin.patch("/admin/settings/sso", json=payload)
    assert write.status_code == 200, write.text
    body = write.json()
    assert body["sso_enabled"] is True
    assert body["sso_provider"] == "Keycloak"
    assert body["sso_role_claim"] == "groups"
    assert body["sso_default_role"] == "lab_admin"
    assert body["sso_role_mappings"] == {"admins": "platform_admin", "ops": "lab_admin"}
    assert body["sso_auto_create_users"] is True
    assert body["sso_sync_roles_on_login"] is True
    assert body["sso_client_secret_configured"] is True

    with Session(engine) as session:
        cfg = session.get(Config, 1)
        assert cfg is not None
        assert cfg.sso_role_claim == "groups"
        assert cfg.sso_default_role == "lab_admin"
        assert cfg.sso_auto_create_users is True
        assert cfg.sso_sync_roles_on_login is True
        assert cfg.sso_role_mappings_json == '{"admins":"platform_admin","ops":"lab_admin"}'


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
        cfg.sso_role_claim = "groups"
        cfg.sso_default_role = "user"
        cfg.sso_role_mappings_json = '{"admins":"platform_admin","ops":"lab_admin"}'
        cfg.sso_auto_create_users = True
        cfg.sso_sync_roles_on_login = True
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
    assert me.json()["role"] == "user"


def test_oidc_role_mapping_assigns_highest_mapped_role(client: TestClient, monkeypatch):
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
        cfg.sso_role_claim = "groups"
        cfg.sso_default_role = "user"
        cfg.sso_role_mappings_json = '{"admins":"platform_admin","ops":"lab_admin","view":"user"}'
        cfg.sso_auto_create_users = True
        cfg.sso_sync_roles_on_login = True
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
        return _FakeResponse({"access_token": "access-123"})

    def _fake_get(url, headers, timeout):
        assert "userinfo" in url
        return _FakeResponse({"preferred_username": "oidcuser", "groups": ["ops", "admins"]})

    monkeypatch.setattr("src.routes.auth.requests.post", _fake_post)
    monkeypatch.setattr("src.routes.auth.requests.get", _fake_get)

    start = client.get(
        "/auth/sso/start",
        params={"return_to": "https://10.68.49.250:30073/"},
        headers={"origin": "https://10.68.49.250:30073"},
    )
    assert start.status_code == 200, start.text
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = client.get(
        "/auth/sso/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in {302, 307}

    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "oidcuser"
    assert me.json()["role"] == Role.PLATFORM_ADMIN
    assert me.json()["can_access_admin"] is True


def test_oidc_role_mapping_respects_auto_create_disabled(client: TestClient, monkeypatch):
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
        cfg.sso_role_claim = "groups"
        cfg.sso_default_role = "user"
        cfg.sso_role_mappings_json = '{"admins":"platform_admin"}'
        cfg.sso_auto_create_users = False
        cfg.sso_sync_roles_on_login = True
        session.add(cfg)
        session.commit()

    class _FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "src.routes.auth.requests.post",
        lambda url, data, headers, timeout: _FakeResponse({"access_token": "access-123"}),
    )
    monkeypatch.setattr(
        "src.routes.auth.requests.get",
        lambda url, headers, timeout: _FakeResponse({"preferred_username": "new-oidc-user", "groups": ["admins"]}),
    )

    start = client.get(
        "/auth/sso/start",
        params={"return_to": "https://10.68.49.250:30073/"},
        headers={"origin": "https://10.68.49.250:30073"},
    )
    assert start.status_code == 200, start.text
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = client.get(
        "/auth/sso/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in {302, 307}
    assert "auth_error=oidc_user_not_provisioned" in callback.headers["location"]


def test_oidc_role_mapping_syncs_existing_user_role_on_login(client: TestClient, monkeypatch):
    with Session(engine) as session:
        session.add(
            User(
                username="oidc-existing",
                password_hash=hash_password("password"),
                role=Role.USER,
                team="default",
                is_admin=False,
                force_password_change=False,
            )
        )
        cfg = session.get(Config, 1)
        assert cfg is not None
        cfg.sso_enabled = True
        cfg.sso_client_id = "oidc-client"
        cfg.sso_client_secret = "oidc-secret"
        cfg.sso_authorize_url = "https://idp.example.com/oauth2/v2/auth"
        cfg.sso_token_url = "https://idp.example.com/oauth2/v2/token"
        cfg.sso_userinfo_url = "https://idp.example.com/oauth2/v2/userinfo"
        cfg.sso_redirect_url = "https://10.68.49.250:30080/auth/sso/callback"
        cfg.sso_role_claim = "groups"
        cfg.sso_default_role = "user"
        cfg.sso_role_mappings_json = '{"ops":"lab_admin"}'
        cfg.sso_auto_create_users = False
        cfg.sso_sync_roles_on_login = True
        session.add(cfg)
        session.commit()

    class _FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self._payload = payload
            self.status_code = status_code
            self.text = str(payload)

        def json(self):
            return self._payload

    monkeypatch.setattr(
        "src.routes.auth.requests.post",
        lambda url, data, headers, timeout: _FakeResponse({"access_token": "access-123"}),
    )
    monkeypatch.setattr(
        "src.routes.auth.requests.get",
        lambda url, headers, timeout: _FakeResponse({"preferred_username": "oidc-existing", "groups": ["ops"]}),
    )

    start = client.get(
        "/auth/sso/start",
        params={"return_to": "https://10.68.49.250:30073/"},
        headers={"origin": "https://10.68.49.250:30073"},
    )
    assert start.status_code == 200, start.text
    state = parse_qs(urlparse(start.json()["authorize_url"]).query)["state"][0]

    callback = client.get(
        "/auth/sso/callback",
        params={"code": "test-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in {302, 307}

    with Session(engine) as session:
        user = session.get(User, "oidc-existing")
        assert user is not None
        assert user.role == Role.LAB_ADMIN
        assert user.is_admin is True


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


def test_vm_delete_tolerates_kubernetes_conflict_and_clears_record(login_user: TestClient, monkeypatch):
    _seed_vm_template()
    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    def _delete_conflict(*_args, **_kwargs):
        raise ApiException(status=409, reason="Conflict")

    monkeypatch.setattr("src.routes.user.kube.delete_pod", _delete_conflict)

    deleted = login_user.delete(f"/user/pods/{vm_id}")
    assert deleted.status_code == 204, deleted.text

    listed = login_user.get("/user/pods")
    assert listed.status_code == 200, listed.text
    assert all(item["id"] != vm_id for item in listed.json())


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


def test_vm_connect_token_uses_rdp_console_for_guacamole_rdp_templates(login_user: TestClient, monkeypatch):
    _seed_vm_template(console_provider="guacamole_rdp")
    monkeypatch.setattr(
        "src.routes.user._vm_rdp_ready_status",
        lambda _instance_id, _namespace=None: (True, "VM is running."),
    )

    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    token_response = login_user.post(f"/user/pods/{vm_id}/connect-token")
    assert token_response.status_code == 200, token_response.text
    connect_url = token_response.json()["connect_url"]
    assert "/connect/rdp.html" in connect_url
    assert "autoconnect=true" in connect_url
    assert "password=" not in connect_url


def test_vm_connect_token_blocks_until_rdp_ready_for_guacamole_rdp_templates(login_user: TestClient, monkeypatch):
    _seed_vm_template(console_provider="guacamole_rdp")

    monkeypatch.setattr(
        "src.routes.user._vm_rdp_ready_status",
        lambda _instance_id, _namespace=None: (False, "VM process started; waiting for RDP service."),
    )

    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    token_response = login_user.post(f"/user/pods/{vm_id}/connect-token")
    assert token_response.status_code == 409, token_response.text
    assert "waiting for RDP service" in token_response.json()["detail"]


def test_vm_list_marks_guacamole_rdp_instances_starting_until_rdp_ready(login_user: TestClient, monkeypatch):
    _seed_vm_template(console_provider="guacamole_rdp")

    monkeypatch.setattr(
        "src.routes.user._vm_rdp_ready_status",
        lambda _instance_id, _namespace=None: (False, "VM process started; waiting for RDP service."),
    )
    monkeypatch.setattr(
        "src.routes.user.kube.get_status",
        lambda instance_id, _owner, **_kwargs: PodStatus(instance_id=instance_id, phase="Running", ready=True),
    )

    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    vm_id = started.json()["id"]

    listed = login_user.get("/user/pods")
    assert listed.status_code == 200, listed.text
    entry = next(item for item in listed.json() if item["id"] == vm_id)
    assert entry["status"] == "running"
    assert entry["status_stage"] == "starting"
    assert "waiting for RDP service" in (entry.get("status_detail") or "")


def test_admin_template_console_provider_round_trip(login_admin: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "secrets_encryption_key", "unit-test-template-secret-key-123456")
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
        "rdp_default_username": "vm-user",
        "rdp_default_password": "vm-pass-123",
    }
    created = login_admin.post("/admin/templates", json=payload)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    assert created.json()["console_provider"] == "guacamole"
    assert created.json()["rdp_default_username"] == "vm-user"
    assert created.json()["rdp_default_password_configured"] is True
    with Session(engine) as session:
        record = session.get(Template, template_id)
        assert record is not None
        assert str(record.rdp_default_password or "") != "vm-pass-123"
        assert str(record.rdp_default_password or "") != ""

    listed = login_admin.get("/admin/templates")
    assert listed.status_code == 200, listed.text
    matched = [item for item in listed.json() if item["id"] == template_id]
    assert matched
    assert matched[0]["console_provider"] == "guacamole"
    assert matched[0]["rdp_default_username"] == "vm-user"
    assert matched[0]["rdp_default_password_configured"] is True

    updated = login_admin.patch(f"/admin/templates/{template_id}", json={"console_provider": "spice"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["console_provider"] == "spice"

    rdp_updated = login_admin.patch(f"/admin/templates/{template_id}", json={"console_provider": "guacamole_rdp"})
    assert rdp_updated.status_code == 200, rdp_updated.text
    assert rdp_updated.json()["console_provider"] == "guacamole_rdp"
    assert rdp_updated.json()["rdp_default_username"] == "vm-user"
    assert rdp_updated.json()["rdp_default_password_configured"] is True


def test_guacamole_rdp_template_defaults_are_passed_to_runner(login_user: TestClient, monkeypatch):
    _seed_vm_template(
        console_provider="guacamole_rdp",
        rdp_default_username="student",
        rdp_default_password="rdp-pass-123",
    )
    captured = {}

    def _create_pod(req):
        captured["req"] = req
        return PodStatus(instance_id=req.instance_id, phase="pending", disk_pvc=f"pvc-{req.instance_id[:8]}")

    monkeypatch.setattr(kube, "create_pod", _create_pod)
    started = login_user.post("/user/templates/tmpl-vm-1/start")
    assert started.status_code == 201, started.text
    req = captured.get("req")
    assert req is not None
    assert req.console_provider == "guacamole_rdp"
    assert req.rdp_default_username == "student"
    assert req.rdp_default_password == "rdp-pass-123"


def test_admin_delete_image_rejects_when_template_references_it(login_admin: TestClient):
    with Session(engine) as session:
        session.add(
            Image(
                id="img-delete-check-1",
                name="Delete Check Image",
                filename="delete-check.qcow2",
                checksum="sha256:delete-check",
                size_bytes=2048,
                source_pvc="golden-images-vm",
            )
        )
        session.add(
            Template(
                id="tmpl-delete-check-1",
                name="Delete Check Template",
                description="linked template",
                os_type="windows",
                image_id="img-delete-check-1",
                cpu_cores=2,
                ram_mb=2048,
                auto_delete_minutes=30,
                idle_timeout_minutes=30,
                enabled=True,
                network_mode="bridge",
                console_provider="spice",
            )
        )
        session.commit()

    deleted = login_admin.delete("/admin/images/img-delete-check-1")
    assert deleted.status_code == 409, deleted.text
    assert "image is in use by templates" in deleted.json()["detail"]


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
