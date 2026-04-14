from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src.auth import connect_token_storage_key, hash_password, session_token_storage_key
from src.db import engine
from src.rbac import Role
from src.tables import (
    ConnectToken,
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    Instance,
    Template,
    Token,
    User,
)
from src.time_utils import utc_now


def test_delete_user_cleans_terminal_instances_and_tokens(login_admin: TestClient) -> None:
    now = utc_now()
    with Session(engine) as session:
        session.add(
            User(
                username="namespaceadmin",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                is_admin=True,
                namespace_scopes_json='["labs"]',
                force_password_change=False,
            )
        )
        session.add(Token(token=session_token_storage_key("session-delete-test"), username="namespaceadmin"))
        session.add(
            ConnectToken(
                token=connect_token_storage_key("connect-delete-test"),
                username="namespaceadmin",
                instance_id="vm-delete-1",
                resource_type="vm",
                token_type="grant",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
        )
        session.add(
            Image(
                id="img-delete-user-vm",
                name="Delete User VM",
                filename="delete-user-vm.qcow2",
                checksum="sha256:test-delete-user-vm",
                size_bytes=1024,
            )
        )
        session.add(
            Template(
                id="tmpl-delete-user-vm",
                name="Delete User VM Template",
                description="delete-user template",
                os_type="linux",
                image_id="img-delete-user-vm",
                cpu_cores=2,
                ram_mb=2048,
                enabled=True,
            )
        )
        session.add(
            ContainerImage(
                id="img-delete-user-ct",
                name="Delete User Container",
                image_ref="docker.io/library/nginx:stable",
            )
        )
        session.add(
            ContainerTemplate(
                id="tmpl-delete-user-ct",
                template_key="delete-user-ct",
                version=1,
                is_default=True,
                name="Delete User Container Template",
                container_image_id="img-delete-user-ct",
                enabled=True,
            )
        )
        session.add(
            Instance(
                id="vm-delete-1",
                template_id="tmpl-delete-user-vm",
                owner="namespaceadmin",
                status="stopped",
            )
        )
        session.add(
            ContainerInstance(
                id="ct-delete-1",
                template_id="tmpl-delete-user-ct",
                owner="namespaceadmin",
                status="completed",
            )
        )
        session.commit()

    response = login_admin.delete("/admin/users/namespaceadmin")
    assert response.status_code == 204, response.text

    with Session(engine) as session:
        assert session.get(User, "namespaceadmin") is None
        assert session.exec(select(Token).where(Token.username == "namespaceadmin")).all() == []
        assert session.exec(select(ConnectToken).where(ConnectToken.username == "namespaceadmin")).all() == []
        assert session.exec(select(Instance).where(Instance.owner == "namespaceadmin")).all() == []
        assert session.exec(select(ContainerInstance).where(ContainerInstance.owner == "namespaceadmin")).all() == []


def test_delete_user_rejects_when_active_instances_exist(login_admin: TestClient) -> None:
    with Session(engine) as session:
        session.add(
            User(
                username="namespaceadmin",
                password_hash=hash_password("password"),
                role=Role.NAMESPACE_ADMIN,
                is_admin=True,
                namespace_scopes_json='["labs"]',
                force_password_change=False,
            )
        )
        session.add(
            Image(
                id="img-delete-user-vm-active",
                name="Delete User VM Active",
                filename="delete-user-vm-active.qcow2",
                checksum="sha256:test-delete-user-vm-active",
                size_bytes=1024,
            )
        )
        session.add(
            Template(
                id="tmpl-delete-user-vm-active",
                name="Delete User VM Template Active",
                description="delete-user active template",
                os_type="linux",
                image_id="img-delete-user-vm-active",
                cpu_cores=2,
                ram_mb=2048,
                enabled=True,
            )
        )
        session.add(
            Instance(
                id="vm-delete-active-1",
                template_id="tmpl-delete-user-vm-active",
                owner="namespaceadmin",
                status="running",
            )
        )
        session.commit()

    response = login_admin.delete("/admin/users/namespaceadmin")
    assert response.status_code == 409, response.text
    detail = response.json().get("detail", "")
    assert "active labs" in detail.lower()

    with Session(engine) as session:
        assert session.get(User, "namespaceadmin") is not None
