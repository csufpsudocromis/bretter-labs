from types import SimpleNamespace

from starlette.requests import Request

from src.routes import admin as admin_routes
from src.routes import admin_containers as admin_containers_routes
from src.tables import User


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
