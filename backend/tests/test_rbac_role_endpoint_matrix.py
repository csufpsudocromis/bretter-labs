import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from src.auth import hash_password
from src.db import engine
from src.rbac import Role
from src.tables import User


def _upsert_user(*, username: str, password: str, role: str, is_admin: bool, namespace_scopes: list[str]) -> None:
    with Session(engine) as session:
        user = session.get(User, username)
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_admin=is_admin,
                force_password_change=False,
            )
        else:
            user.password_hash = hash_password(password)
            user.role = role
            user.is_admin = is_admin
            user.force_password_change = False
        user.namespace_scopes_json = json.dumps(namespace_scopes)
        session.add(user)
        session.commit()


def _login(client: TestClient, *, username: str, password: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


ROLE_USERS: dict[str, tuple[str, str, bool, list[str]]] = {
    Role.USER: ("matrix-user", "password", False, ["labs"]),
    Role.LAB_ADMIN: ("matrix-lab-admin", "password", True, ["labs"]),
    Role.NAMESPACE_ADMIN: ("matrix-namespace-admin", "password", True, ["labs"]),
    Role.PLATFORM_ADMIN: ("admin", "admin", True, []),
}

ENDPOINT_MATRIX = [
    ("GET", "/admin/images", None, {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    ("GET", "/admin/templates", None, {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    ("GET", "/admin/operations/upload-tasks", None, {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    (
        "POST",
        "/admin/operations/upload-tasks/missing-task/retry",
        None,
        {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN},
    ),
    (
        "PATCH",
        "/admin/templates/missing-template",
        {"enabled": True},
        {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN},
    ),
    ("DELETE", "/admin/images/missing-image", None, {Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    ("GET", "/admin/users", None, {Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    ("GET", "/admin/settings/site", None, {Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
    ("POST", "/admin/settings/idle-timeout", {"idle_timeout_minutes": 30}, {Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN}),
]


@pytest.mark.parametrize("role", [Role.USER, Role.LAB_ADMIN, Role.NAMESPACE_ADMIN, Role.PLATFORM_ADMIN])
def test_rbac_role_endpoint_matrix(client: TestClient, role: str) -> None:
    username, password, is_admin, namespace_scopes = ROLE_USERS[role]
    if role != Role.PLATFORM_ADMIN:
        _upsert_user(
            username=username,
            password=password,
            role=role,
            is_admin=is_admin,
            namespace_scopes=namespace_scopes,
        )
    _login(client, username=username, password=password)

    for method, path, payload, allowed_roles in ENDPOINT_MATRIX:
        if method == "GET":
            response = client.get(path)
        elif method == "POST":
            response = client.post(path, json=payload)
        elif method == "PATCH":
            response = client.patch(path, json=payload)
        elif method == "DELETE":
            response = client.delete(path)
        else:
            raise AssertionError(f"unsupported test method: {method}")

        allowed = role in allowed_roles
        if allowed:
            assert response.status_code not in {
                401,
                403,
            }, f"{role} expected allow for {method} {path}, got {response.status_code}: {response.text}"
        else:
            assert response.status_code in {
                401,
                403,
            }, f"{role} expected deny for {method} {path}, got {response.status_code}: {response.text}"
