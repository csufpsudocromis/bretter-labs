import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import SQLModel, Session

TEST_ROOT = Path(__file__).resolve().parent / ".tmp"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
SITE_ASSETS_DIR = TEST_ROOT / "site-assets"
SITE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("BLABS_DATABASE_PATH", str(TEST_ROOT / "test.db"))
os.environ.setdefault("BLABS_SITE_ASSETS_DIR", str(SITE_ASSETS_DIR))
os.environ.setdefault("BLABS_AUTH_COOKIE_SECURE", "false")
os.environ.setdefault("BLABS_CONNECT_COOKIE_SECURE", "false")
os.environ.setdefault("BLABS_KUBE_VM_STORAGE_CLASS", "longhorn-r1")
os.environ.setdefault("BLABS_REAPER_INTERVAL_SECONDS", "3600")
os.environ.setdefault("BLABS_CONTAINER_SCAN_ENABLED", "false")

from src.auth import hash_password  # noqa: E402
from src.db import engine  # noqa: E402
from src.main import app  # noqa: E402
import src.main as main_module  # noqa: E402
import src.routes.auth as auth_routes  # noqa: E402
import src.routes.user as user_routes  # noqa: E402
import src.routes.user_containers as user_container_routes  # noqa: E402
from src.services.kubernetes import PodStatus, kube  # noqa: E402
from src.tables import (  # noqa: E402
    Config,
    ConnectToken,
    ContainerImage,
    ContainerInstance,
    ContainerTemplate,
    Image,
    Instance,
    OIDCLoginState,
    TeamQuota,
    Template,
    Token,
    User,
)
from src.rbac import Role  # noqa: E402


class _FakeStorageApi:
    def __init__(self, _api_client):
        pass

    def read_storage_class(self, name: str):
        return {"metadata": {"name": name}}


@pytest.fixture
def client(monkeypatch, reset_db):
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    port_counter = {"value": 32000}

    def _next_port() -> int:
        port_counter["value"] += 1
        return port_counter["value"]

    monkeypatch.setattr(kube, "reaper_tick", lambda session: None)
    monkeypatch.setattr(kube, "reserve_warm_pool_pvc", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        kube,
        "create_pod",
        lambda req: PodStatus(instance_id=req.instance_id, phase="pending", disk_pvc=f"pvc-{req.instance_id[:8]}"),
    )
    monkeypatch.setattr(kube, "create_service_for_pod", lambda *args, **kwargs: _next_port())
    monkeypatch.setattr(kube, "stop_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "delete_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        kube, "get_status", lambda *args, **kwargs: PodStatus(instance_id="", phase="running", ready=True)
    )
    kube._core = SimpleNamespace(api_client=object())
    monkeypatch.setattr(
        kube,
        "resolve_vm_source_pvc",
        lambda **kwargs: (
            SimpleNamespace(spec=SimpleNamespace(storage_class_name="longhorn-r1")),
            str(kwargs.get("runtime_namespace", "labs") or "labs"),
        ),
    )
    monkeypatch.setattr(
        kube,
        "check_vm_runner_image_pullability",
        lambda *args, **kwargs: (True, "runner image pull check completed (test fixture)."),
    )
    monkeypatch.setattr(user_routes.k8s_client, "StorageV1Api", _FakeStorageApi)
    monkeypatch.setattr(
        user_routes,
        "evaluate_node_launch_admission",
        lambda _kube: (True, "Node admission passed in test fixture."),
    )
    monkeypatch.setattr(
        user_routes,
        "evaluate_vm_storage_launch_admission",
        lambda _kube, namespace: (True, f"PVC admission passed in test fixture ({namespace})."),
    )

    monkeypatch.setattr(
        kube, "create_container_pod", lambda req: PodStatus(instance_id=req.instance_id, phase="pending")
    )
    monkeypatch.setattr(kube, "ensure_container_service", lambda *args, **kwargs: _next_port())
    monkeypatch.setattr(kube, "ensure_container_ingress", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "delete_container_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "stop_container_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "delete_container_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "get_container_launch_diagnostics", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        user_container_routes,
        "evaluate_node_launch_admission",
        lambda _kube: (True, "Node admission passed in test fixture."),
    )

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def reset_db():
    auth_routes._LOGIN_ATTEMPTS.clear()
    auth_routes._LOGIN_BLOCKED_UNTIL.clear()
    for path in SITE_ASSETS_DIR.glob("*"):
        if path.is_file():
            path.unlink()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for model in [
            OIDCLoginState,
            ConnectToken,
            Token,
            Instance,
            ContainerInstance,
            Template,
            ContainerTemplate,
            Image,
            ContainerImage,
            Config,
            TeamQuota,
            User,
        ]:
            session.exec(delete(model))
        session.commit()
        session.add(
            Config(
                id=1,
                max_concurrent_vms=50,
                per_user_vm_limit=1,
                idle_timeout_minutes=30,
            )
        )
        session.add(
            User(
                username="admin",
                password_hash=hash_password("admin"),
                role=Role.PLATFORM_ADMIN,
                is_admin=True,
                force_password_change=False,
            )
        )
        session.add(
            User(
                username="alice",
                password_hash=hash_password("password"),
                role=Role.USER,
                is_admin=False,
                force_password_change=False,
            )
        )
        session.commit()
    yield


@pytest.fixture
def login_user(client: TestClient):
    response = client.post("/auth/login", json={"username": "alice", "password": "password"})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def login_admin(client: TestClient):
    response = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200, response.text
    return client
