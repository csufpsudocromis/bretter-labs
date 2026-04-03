from types import SimpleNamespace
from uuid import uuid4

from starlette.requests import Request
from sqlmodel import Session

from src.routes import admin as admin_routes
from src.routes import admin_containers as admin_containers_routes
from src.db import engine
from src.tables import ManagedNamespace, TeamQuota, User


def _request_with_headers(*, headers: list[tuple[bytes, bytes]] | None = None, query_string: bytes = b"") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/admin/operations/upload-tasks",
        "raw_path": b"/admin/operations/upload-tasks",
        "query_string": query_string,
        "headers": headers or [],
    }
    return Request(scope)


def test_admin_requested_namespace_hint_requires_explicit_namespace() -> None:
    request = _request_with_headers()
    assert admin_routes._requested_namespace_hint(request) is None


def test_admin_requested_namespace_hint_reads_header_or_query() -> None:
    header_request = _request_with_headers(headers=[(b"x-bretter-namespace", b"TEST-NS")])
    assert admin_routes._requested_namespace_hint(header_request) == "test-ns"

    query_request = _request_with_headers(query_string=b"namespace=Alt-NS")
    assert admin_routes._requested_namespace_hint(query_request) == "alt-ns"


def test_admin_record_visible_for_platform_admin_without_namespace_filter() -> None:
    actor = User(username="admin", password_hash="x", role="platform_admin", is_admin=True)
    record = SimpleNamespace(namespace="test-namespace", shared_catalog=False)
    assert admin_routes._record_visible_for_actor(record, actor, requested_namespace=None)


def test_admin_containers_requested_namespace_hint_requires_explicit_namespace() -> None:
    request = _request_with_headers()
    assert admin_containers_routes._requested_namespace_hint(request) is None


def test_admin_containers_record_visibility_without_namespace_filter() -> None:
    actor = User(username="admin", password_hash="x", role="platform_admin", is_admin=True)
    record = SimpleNamespace(namespace="test-namespace", shared_catalog=False)
    assert admin_containers_routes._record_visible_for_actor(record, actor, requested_namespace=None)


def test_template_namespace_catalog_skips_cluster_namespace_listing(login_admin, monkeypatch) -> None:
    with Session(engine) as session:
        session.add(ManagedNamespace(id=str(uuid4()), namespace="test-namespace"))
        session.add(TeamQuota(id=str(uuid4()), team="default", namespace="quota-namespace"))
        session.commit()

    def _unexpected_kube_client():  # pragma: no cover - should never execute
        raise AssertionError("template namespace catalog should not enumerate all cluster namespaces")

    monkeypatch.setattr(admin_routes.kube, "_client", _unexpected_kube_client)
    response = login_admin.get("/admin/template-namespaces")
    assert response.status_code == 200, response.text
    values = response.json()
    assert "test-namespace" in values
    assert "quota-namespace" in values
    assert "labs" in values


def test_quota_namespace_catalog_skips_cluster_namespace_listing(login_admin, monkeypatch) -> None:
    with Session(engine) as session:
        session.add(ManagedNamespace(id=str(uuid4()), namespace="labs-namespace"))
        session.add(TeamQuota(id=str(uuid4()), team="default", namespace="quota-space"))
        session.commit()

    def _unexpected_kube_client():  # pragma: no cover - should never execute
        raise AssertionError("quota namespace catalog should not enumerate all cluster namespaces")

    monkeypatch.setattr(admin_routes.kube, "_client", _unexpected_kube_client)
    response = login_admin.get("/admin/quota-namespaces")
    assert response.status_code == 200, response.text
    values = response.json()
    assert "labs-namespace" in values
    assert "quota-space" in values
    assert "labs" in values
