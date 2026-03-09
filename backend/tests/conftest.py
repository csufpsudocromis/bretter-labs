import os
from pathlib import Path

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
from src.services.kubernetes import PodStatus, kube  # noqa: E402
from src.tables import (  # noqa: E402
    Config,
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
    monkeypatch.setattr(kube, "get_status", lambda *args, **kwargs: PodStatus(instance_id="", phase="running", ready=True))

    monkeypatch.setattr(kube, "create_container_pod", lambda req: PodStatus(instance_id=req.instance_id, phase="pending"))
    monkeypatch.setattr(kube, "ensure_container_service", lambda *args, **kwargs: _next_port())
    monkeypatch.setattr(kube, "ensure_container_ingress", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "delete_container_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "stop_container_pod", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "delete_container_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(kube, "get_container_launch_diagnostics", lambda *args, **kwargs: [])

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def reset_db():
    for path in SITE_ASSETS_DIR.glob("*"):
        if path.is_file():
            path.unlink()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for model in [
            ConnectToken,
            Token,
            Instance,
            ContainerInstance,
            Template,
            ContainerTemplate,
            Image,
            ContainerImage,
            Config,
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
        session.add(User(username="admin", password_hash=hash_password("admin"), is_admin=True, force_password_change=False))
        session.add(User(username="alice", password_hash=hash_password("password"), is_admin=False, force_password_change=False))
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
