from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram

_HANDSHAKE_TOTAL = Counter(
    "blabs_ws_proxy_handshake_total",
    "Websocket proxy handshakes by resource type and result.",
    ("resource_type", "result"),
)
_DISCONNECT_TOTAL = Counter(
    "blabs_ws_proxy_disconnect_total",
    "Websocket proxy disconnects by resource type, direction, and close code.",
    ("resource_type", "direction", "code"),
)
_ACTIVE_CONNECTIONS = Gauge(
    "blabs_ws_proxy_active_connections",
    "Current websocket proxy sessions by resource type.",
    ("resource_type",),
)
_SESSION_SECONDS = Histogram(
    "blabs_ws_proxy_session_seconds",
    "Websocket proxy session duration seconds by resource type.",
    ("resource_type",),
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200),
)


def _normalize_resource_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"vm", "container"}:
        return normalized
    return "unknown"


def _normalize_direction(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"upstream", "downstream"}:
        return normalized
    return "unknown"


def _normalize_code(value: str | int | None) -> str:
    if value is None:
        return "unknown"
    raw = str(value).strip()
    if not raw:
        return "unknown"
    if raw.isdigit():
        return raw
    return "other"


def extract_close_code(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    candidates = (
        getattr(exc, "code", None),
        getattr(getattr(exc, "rcvd", None), "code", None),
        getattr(getattr(exc, "sent", None), "code", None),
        getattr(exc, "status_code", None),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        raw = str(candidate).strip()
        if raw:
            return raw
    return None


def record_handshake(resource_type: str, *, success: bool) -> None:
    result = "success" if success else "failure"
    _HANDSHAKE_TOTAL.labels(resource_type=_normalize_resource_type(resource_type), result=result).inc()


def record_disconnect(resource_type: str, *, direction: str, code: str | int | None) -> None:
    _DISCONNECT_TOTAL.labels(
        resource_type=_normalize_resource_type(resource_type),
        direction=_normalize_direction(direction),
        code=_normalize_code(code),
    ).inc()


def mark_connection_open(resource_type: str) -> float:
    normalized = _normalize_resource_type(resource_type)
    _ACTIVE_CONNECTIONS.labels(resource_type=normalized).inc()
    return time.monotonic()


def mark_connection_close(resource_type: str, started_at: float | None) -> None:
    normalized = _normalize_resource_type(resource_type)
    _ACTIVE_CONNECTIONS.labels(resource_type=normalized).dec()
    if started_at is None:
        return
    duration = max(0.0, time.monotonic() - float(started_at))
    _SESSION_SECONDS.labels(resource_type=normalized).observe(duration)
