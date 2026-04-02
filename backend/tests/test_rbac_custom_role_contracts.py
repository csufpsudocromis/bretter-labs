from fastapi.testclient import TestClient


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def _logout(client: TestClient) -> None:
    client.post("/auth/logout")


def test_custom_role_images_only_contract(login_admin: TestClient) -> None:
    create_role = login_admin.post(
        "/admin/settings/roles",
        json={
            "role": "image_auditor",
            "label": "Image Auditor",
            "description": "Read-only image access for audits.",
            "permissions": ["admin.access", "admin.images.read"],
        },
    )
    assert create_role.status_code == 201, create_role.text

    create_user = login_admin.post(
        "/admin/users",
        json={
            "username": "image_auditor_user",
            "password": "password",
            "role": "image_auditor",
            "is_admin": True,
        },
    )
    assert create_user.status_code == 201, create_user.text

    _logout(login_admin)
    _login(login_admin, "image_auditor_user", "password")

    assert login_admin.get("/admin/images").status_code == 200
    assert login_admin.get("/admin/templates").status_code == 403
    assert login_admin.get("/admin/users").status_code == 403
    assert login_admin.get("/admin/settings/concurrency").status_code == 403


def test_custom_role_users_and_settings_read_contract(login_admin: TestClient) -> None:
    create_role = login_admin.post(
        "/admin/settings/roles",
        json={
            "role": "ops_auditor",
            "label": "Ops Auditor",
            "description": "Audit users/settings without image access.",
            "permissions": ["admin.access", "admin.users.read", "admin.settings.read"],
        },
    )
    assert create_role.status_code == 201, create_role.text

    create_user = login_admin.post(
        "/admin/users",
        json={
            "username": "ops_auditor_user",
            "password": "password",
            "role": "ops_auditor",
            "is_admin": True,
        },
    )
    assert create_user.status_code == 201, create_user.text

    _logout(login_admin)
    _login(login_admin, "ops_auditor_user", "password")

    assert login_admin.get("/admin/users").status_code == 200
    assert login_admin.get("/admin/settings/concurrency").status_code == 200
    assert login_admin.get("/admin/images").status_code == 403
