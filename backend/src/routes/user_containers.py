import asyncio
import base64
import html
import json
import logging
import ssl
import socket
import threading
import time
import warnings
from http.client import HTTPConnection
from datetime import timedelta
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import requests
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import RedirectResponse
from kubernetes.client import ApiException
from sqlmodel import Session, func, select
from urllib3.exceptions import InsecureRequestWarning

from ..auth import (
    issue_connect_token,
    require_user,
    consume_connect_grant,
    validate_connect_session,
)
from ..config import settings
from ..db import get_session
from ..models import ContainerInstance as ContainerInstanceView
from ..models import ContainerConnectReadiness
from ..models import ContainerDependencyCheck
from ..models import ContainerTemplate as ContainerTemplateView
from ..services.launch_lock import lock_user_launch_slot
from ..services.kubernetes import ContainerPodRequest, PodStatus, kube
from ..services.launch_admission import evaluate_node_launch_admission
from ..services.multi_cluster import (
    PlacementError,
    kube_service_for_cluster,
    local_cluster_id,
    select_cluster_for_launch,
)
from ..services.namespace_policies import get_namespace_runtime_policy
from ..services.resource_guard import check_launch_headroom
from ..services.team_quotas import enforce_team_quota, normalize_namespace, team_idle_timeout_cap
from ..services.tenant_namespace_bootstrap import ensure_team_runtime_namespace
from ..services.tenant_context import (
    GLOBAL_TENANT,
    normalize_namespace_scopes,
    normalize_tenant,
    resolve_resource_namespace,
    tenant_namespace_for_user,
)
from ..services import ws_metrics
from ..tables import Config
from ..tables import ContainerImage as ContainerImageTable
from ..tables import ContainerInstance as ContainerInstanceTable
from ..tables import ContainerTemplate as ContainerTemplateTable
from ..tables import Instance
from ..tables import ManagedNamespace
from ..tables import User
from ..time_utils import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)
_PROXY_TIMEOUT_SECONDS = 45
_CONNECT_GRANT_COOKIE_NAME = "blabs_connect_grant"
_CONNECT_SESSION_COOKIE_NAME = "blabs_connect_session"
SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."
_CONNECT_READINESS_CACHE_TTL_SECONDS = 20.0
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_TLS_LIKELY_PORTS = {443, 8443, 9443, 6901, 4902}
_CONNECT_READINESS_CACHE_LOCK = threading.Lock()
_CONNECT_READINESS_CACHE: dict[str, tuple[float, bool, str]] = {}


def _namespace_enabled_for_user_runtime(session: Session, namespace: str) -> bool:
    selected = normalize_namespace(namespace)
    if not selected:
        return False
    row = session.exec(select(ManagedNamespace).where(ManagedNamespace.namespace == selected)).first()
    if row is None:
        return True
    return bool(getattr(row, "enabled", True))


def _resolve_selected_namespace(session: Session, user: User, request: Request) -> str:
    selected_namespace = resolve_resource_namespace(
        user, request=request, fallback_namespace=tenant_namespace_for_user(user)
    )
    if not _namespace_enabled_for_user_runtime(session, selected_namespace):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f'namespace "{selected_namespace}" is disabled',
        )
    return selected_namespace


def _namespace_container_idle_timeout_minutes(
    session: Session, namespace: str, template: ContainerTemplateTable | None
) -> int:
    policy = get_namespace_runtime_policy(session, namespace)
    template_minutes = int(getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes) or 0)
    if template_minutes <= 0:
        template_minutes = int(settings.idle_timeout_minutes or 30)
    return max(1, min(template_minutes, int(policy.idle_timeout_minutes_default)))


def _namespace_container_auto_delete_minutes(
    session: Session, namespace: str, template: ContainerTemplateTable | None
) -> int:
    policy = get_namespace_runtime_policy(session, namespace)
    template_minutes = int(getattr(template, "auto_delete_minutes", 60) or 0)
    if template_minutes <= 0:
        return max(1, int(policy.container_auto_delete_minutes_default))
    return max(1, min(template_minutes, int(policy.container_auto_delete_minutes_default)))


def _phase_to_status(phase: str) -> str:
    return {
        "queued": "queued",
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _status_feedback(status_name: str, pod_status: PodStatus | None) -> tuple[str, str]:
    normalized = (status_name or "unknown").lower()
    if normalized == "queued":
        return "queued", "Queued for retry when resources become available."
    if normalized == "running":
        if pod_status and not pod_status.ready:
            detail = (pod_status.waiting_message or pod_status.message or "").strip()
            return "starting", detail or "Container is starting."
        return "running", "Container is running."
    if normalized == "pending":
        if not pod_status:
            return "pending", "Scheduling container pod."
        reason_text = " ".join(
            [
                (pod_status.waiting_reason or "").lower(),
                (pod_status.reason or "").lower(),
            ]
        )
        detail = (pod_status.waiting_message or pod_status.message or "").strip()
        if "unschedulable" in reason_text or "failedscheduling" in reason_text:
            return "pending", detail or "Waiting for available resources."
        return "building", detail or "Preparing container image and runtime."
    if normalized == "completed":
        return "completed", "Container completed."
    if normalized == "stopped":
        return "stopped", "Container is stopped."
    if normalized == "failed":
        return "failed", "Container failed."
    return "unknown", "Container status is unknown."


def _normalize_http_path(value: str | None, *, allow_blank: bool = False) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None if allow_blank else "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


def _parse_dependency_checks(raw: str) -> list[ContainerDependencyCheck]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[ContainerDependencyCheck] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ContainerDependencyCheck.model_validate(item))
        except Exception:
            continue
    return out


def _queue_backoff_seconds(attempts: int) -> int:
    base = max(5, int(settings.container_start_queue_base_delay_seconds or 20))
    max_delay = max(base, int(settings.container_start_queue_max_delay_seconds or 300))
    scale = base * (2 ** max(0, attempts))
    return min(max_delay, scale)


def _humanize_queue_reason(raw_reason: str | None) -> str:
    text = str(raw_reason or "").strip()
    lowered = text.lower()
    if "podsecurity" in lowered or "violates podsecurity" in lowered:
        return "Waiting for container security policy compatibility (PodSecurity)."
    if "forbidden" in lowered or "quota" in lowered:
        return "Waiting for available resources or namespace quota."
    if "unschedulable" in lowered or "failedscheduling" in lowered:
        return "Waiting for available node CPU/memory."
    if len(text) > 180:
        text = text[:180].rstrip()
    return text or "Waiting for available resources."


def _container_access_url_for_target(node_port: int | None, ingress_host: str | None) -> str | None:
    scheme = (settings.public_scheme or "https").strip() or "https"
    if ingress_host:
        return f"{scheme}://{ingress_host}/"
    if not node_port:
        return None
    host = (settings.kube_node_external_host or "").strip() or "127.0.0.1"
    return f"http://{host}:{int(node_port)}/"


def _container_runtime_namespace(user: User, *, namespace: str | None = None) -> str:
    selected = normalize_namespace(namespace)
    if selected:
        return selected
    return tenant_namespace_for_user(user)


def _container_instance_namespace(record: ContainerInstanceTable, user: User | None = None) -> str:
    explicit = str(getattr(record, "namespace", "") or "").strip()
    if explicit:
        return explicit
    if user is not None:
        return _container_runtime_namespace(user)
    return str(settings.kube_namespace or "labs").strip() or "labs"


def _container_instance_cluster_id(record: ContainerInstanceTable) -> str:
    return str(getattr(record, "cluster_id", "") or local_cluster_id()).strip() or local_cluster_id()


def _container_template_namespace(record: ContainerTemplateTable) -> str:
    return (
        normalize_namespace(getattr(record, "namespace", None))
        or normalize_namespace(settings.kube_namespace)
        or "labs"
    )


def _container_template_enabled_namespaces(record: ContainerTemplateTable) -> list[str]:
    raw = getattr(record, "enabled_namespaces_json", "[]")
    payload: list[str] = []
    if isinstance(raw, list):
        payload = [str(item) for item in raw]
    else:
        try:
            decoded = json.loads(str(raw or "[]"))
            if isinstance(decoded, list):
                payload = [str(item) for item in decoded]
        except Exception:
            payload = []
    try:
        normalized = normalize_namespace_scopes(payload)
    except ValueError:
        normalized = []
    if normalized:
        return normalized
    fallback = _container_template_namespace(record)
    return [fallback] if fallback else []


def _container_template_enabled_for_namespace(record: ContainerTemplateTable, namespace: str) -> bool:
    selected = normalize_namespace(namespace)
    if not selected:
        return False
    if selected not in set(_container_template_enabled_namespaces(record)):
        return False
    template_namespace = _container_template_namespace(record)
    if selected == template_namespace:
        return True
    return bool(getattr(record, "shared_catalog", False))


def _container_image_namespace(record: ContainerImageTable) -> str:
    return (
        normalize_namespace(getattr(record, "namespace", None))
        or normalize_namespace(settings.kube_namespace)
        or "labs"
    )


def _container_image_shared_catalog(record: ContainerImageTable) -> bool:
    return bool(getattr(record, "shared_catalog", False))


def _kube_for_container_cluster(session: Session, cluster_id: str):
    try:
        return kube_service_for_cluster(session, cluster_id)
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def _container_service_host(instance_id: str, namespace: str) -> str:
    return f"ctsvc-{instance_id[:8]}.{namespace}.svc.cluster.local"


def _container_prefers_tls(template: ContainerTemplateTable | None, container_port: int) -> bool:
    if container_port in _TLS_LIKELY_PORTS:
        return True
    protocol = str(getattr(template, "healthcheck_protocol", "tcp") or "tcp").strip().lower()
    return protocol == "https"


def _container_http_schemes(template: ContainerTemplateTable | None, container_port: int) -> tuple[str, str]:
    if _container_prefers_tls(template, container_port):
        return ("https", "http")
    return ("http", "https")


def _container_ws_schemes(template: ContainerTemplateTable | None, container_port: int) -> tuple[str, str]:
    if _container_prefers_tls(template, container_port):
        return ("wss", "ws")
    return ("ws", "wss")


def _upstream_requires_https(response: requests.Response) -> bool:
    if response.status_code not in {400, 403, 426, 495, 496, 497}:
        return False
    message = (response.text or "").lower()
    return (
        "https" in message
        or "tls" in message
        or "ssl" in message
        or "plain http request" in message
        or "remote end closed connection without response" in message
    )


def _tls_client_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # Container connect services commonly terminate TLS with self-signed certs
    # generated inside the workload. Keep websocket proxy behavior aligned with
    # HTTP proxy behavior, which does not enforce upstream cert validation.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _build_basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _template_image_ref(
    session: Session,
    template: ContainerTemplateTable | None,
) -> str:
    if not template:
        return ""
    image_id = getattr(template, "container_image_id", None)
    if not image_id:
        return ""
    image_row = session.get(ContainerImageTable, image_id)
    return str(getattr(image_row, "image_ref", "") or "").lower()


def _is_kasm_template(
    session: Session,
    template: ContainerTemplateTable | None,
) -> bool:
    return "kasmweb/" in _template_image_ref(session, template)


def _upstream_basic_auth_header(
    session: Session,
    template: ContainerTemplateTable | None,
) -> str | None:
    if not template:
        return None
    env_values = _parse_env(getattr(template, "env_json", "{}"))
    raw_auth = str(env_values.get("BLABS_CONNECT_BASIC_AUTH", "")).strip()
    if raw_auth:
        if raw_auth.lower().startswith("basic "):
            return raw_auth
        if ":" in raw_auth:
            username, password = raw_auth.split(":", 1)
            if username and password:
                return _build_basic_auth_header(username, password)

    username = str(env_values.get("BLABS_CONNECT_BASIC_USER", "")).strip()
    password = str(env_values.get("BLABS_CONNECT_BASIC_PASSWORD", "")).strip()
    if username and password:
        return _build_basic_auth_header(username, password)

    image_ref = _template_image_ref(session, template)

    # Kasm desktop images expose KasmVNC on 6901 with default kasm_user/vncpassword credentials.
    if "kasmweb/" in image_ref:
        username = str(env_values.get("VNC_USER", "kasm_user")).strip() or "kasm_user"
        password = str(env_values.get("VNC_PW", "vncpassword")).strip() or "vncpassword"
        return _build_basic_auth_header(username, password)

    return None


def _readiness_cache_get(instance_id: str) -> tuple[bool, str] | None:
    now = time.monotonic()
    with _CONNECT_READINESS_CACHE_LOCK:
        row = _CONNECT_READINESS_CACHE.get(instance_id)
        if not row:
            return None
        expires_at, ready, detail = row
        if now > expires_at:
            _CONNECT_READINESS_CACHE.pop(instance_id, None)
            return None
        return bool(ready), str(detail or "")


def _readiness_cache_set(instance_id: str, *, ready: bool, detail: str) -> None:
    expires_at = time.monotonic() + _CONNECT_READINESS_CACHE_TTL_SECONDS
    with _CONNECT_READINESS_CACHE_LOCK:
        _CONNECT_READINESS_CACHE[instance_id] = (expires_at, bool(ready), str(detail or ""))


def _container_requires_ws_probe(session: Session, template: ContainerTemplateTable | None) -> bool:
    image_ref = _template_image_ref(session, template)
    if not image_ref:
        return False
    return "linuxserver/webtop" in image_ref or "kasmweb/" in image_ref


async def _probe_container_connect_websocket(
    *,
    session: Session,
    template: ContainerTemplateTable | None,
    instance_id: str,
    namespace: str,
    container_port: int,
) -> tuple[bool, str]:
    upstream_host = _container_service_host(instance_id, namespace)
    protocols: list[str] = []
    upstream_auth_header = _upstream_basic_auth_header(session, template)
    is_kasm = _is_kasm_template(session, template)
    upstream_origin = None
    node_host = str(getattr(settings, "kube_node_external_host", "") or "").strip()
    if node_host:
        scheme = str(getattr(settings, "public_scheme", "https") or "https").strip().lower()
        if scheme not in {"http", "https"}:
            scheme = "https"
        upstream_origin = f"{scheme}://{node_host}:30073"
    upstream_ws_headers: dict[str, str] = {}
    if upstream_auth_header:
        upstream_ws_headers["Authorization"] = upstream_auth_header
    if upstream_origin and is_kasm:
        upstream_ws_headers["Sec-WebSocket-Origin"] = upstream_origin
    if not upstream_ws_headers:
        upstream_ws_headers = None

    attempted_urls: list[str] = []
    attempt_errors: list[str] = []
    for scheme in _container_ws_schemes(template, container_port):
        upstream_url = f"{scheme}://{upstream_host}:{container_port}/websockets"
        attempted_urls.append(upstream_url)
        ssl_context = _tls_client_context() if scheme == "wss" else None
        try:
            async with websockets.connect(
                upstream_url,
                subprotocols=protocols or None,
                additional_headers=upstream_ws_headers,
                origin=None if is_kasm else upstream_origin,
                open_timeout=3,
                close_timeout=2,
                max_size=None,
                ssl=ssl_context,
            ) as upstream:
                try:
                    first_payload = await asyncio.wait_for(upstream.recv(), timeout=2)
                except asyncio.TimeoutError:
                    return False, "websocket connected but no startup frame received yet"
                if isinstance(first_payload, bytes):
                    if not first_payload:
                        return False, "websocket connected but received empty startup frame"
                else:
                    if not str(first_payload or "").strip():
                        return False, "websocket connected but received empty startup message"
                return True, "ready"
        except Exception as exc:
            attempt_errors.append(f"{upstream_url} -> {type(exc).__name__}: {exc}")
            continue
    detail = "websocket probe failed"
    if attempt_errors:
        detail = attempt_errors[-1]
    elif attempted_urls:
        detail = f"websocket probe failed for {', '.join(attempted_urls)}"
    return False, detail


def _extract_connect_grant_token(request: Request) -> str:
    return str(request.cookies.get(_CONNECT_GRANT_COOKIE_NAME) or "").strip()


def _extract_connect_session_token(request: Request) -> str:
    return str(request.cookies.get(_CONNECT_SESSION_COOKIE_NAME) or "").strip()


def _extract_connect_session_token_ws(websocket: WebSocket) -> str:
    return str(websocket.cookies.get(_CONNECT_SESSION_COOKIE_NAME) or "").strip()


def _connect_cookie_secure(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded_proto == "https":
        return True
    if request.url.scheme == "https":
        return True
    return bool(settings.connect_cookie_secure)


def _request_base_url(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded_proto or str(request.url.scheme or "https").strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "https"
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    if not host:
        host = str(request.url.netloc or "").strip()
    return f"{scheme}://{host}"


def _connect_cookie_samesite() -> str:
    normalized = str(settings.connect_cookie_samesite or "lax").strip().lower()
    if normalized not in {"lax", "strict", "none"}:
        normalized = "lax"
    return normalized


def _rewrite_upstream_location(instance_id: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    base_path = f"/user/containers/{instance_id}/connect"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return raw
    if raw.startswith("/"):
        return f"{base_path}{raw}"
    return f"{base_path}/{raw}"


def _normalized_upstream_path(proxy_path: str) -> str:
    raw = str(proxy_path or "")
    pieces = [piece for piece in raw.split("/") if piece]
    if not pieces:
        return "/"
    path = "/" + "/".join(pieces)
    if raw.endswith("/"):
        path = f"{path}/"
    return path


def _container_idle_bridge_javascript(
    *,
    instance_id: str,
    idle_minutes: int,
) -> str:
    return f"""
(function () {{
  if (window.__blabsContainerIdleBridgeInstalled) return;
  window.__blabsContainerIdleBridgeInstalled = true;
  const instanceId = {json.dumps(instance_id)};
  const idleMinutes = Math.max(1, parseInt({int(max(1, idle_minutes))}, 10) || 30);
  const countdownSeconds = 300;
  const apiBaseDefault = `${{window.location.protocol}}//${{window.location.host}}`;
  let apiBase = apiBaseDefault;
  let openerOrigin = '*';
  const allowedOrigins = new Set();
  let promptActive = false;
  let parentPromptActive = false;
  let idleTimer = null;
  let countdownTimer = null;
  let countdownEndsAt = 0;
  let nextIdleAt = 0;
  let lastHeartbeatAt = 0;
  let idleSuspended = false;

  function normalizeOrigin(value) {{
    try {{
      return new URL(String(value || ''), window.location.href).origin;
    }} catch (err) {{
      return '';
    }}
  }}

  function rememberAllowedOrigin(value) {{
    const origin = normalizeOrigin(value);
    if (origin) {{
      allowedOrigins.add(origin);
    }}
    return origin;
  }}

  function isAllowedOrigin(origin) {{
    if (!origin) return false;
    if (allowedOrigins.size === 0) return true;
    return allowedOrigins.has(origin);
  }}

  rememberAllowedOrigin(window.location.origin);
  rememberAllowedOrigin(apiBaseDefault);
  rememberAllowedOrigin(document.referrer);

  function openerPost(type, extra) {{
    if (!window.opener) return;
    try {{
      if (window.opener.closed) return;
      const message = {{
        type,
        source: 'container',
        instanceId,
        timestamp: Date.now(),
        ...(extra || {{}})
      }};
      const targets = new Set();
      if (openerOrigin && openerOrigin !== '*') {{
        targets.add(openerOrigin);
      }}
      allowedOrigins.forEach(function (origin) {{
        targets.add(origin);
      }});
      if (targets.size === 0) {{
        targets.add('*');
      }}
      targets.forEach(function (targetOrigin) {{
        try {{
          window.opener.postMessage(message, targetOrigin);
        }} catch (err) {{}}
      }});
    }} catch (err) {{}}
  }}

  function ensureOverlay() {{
    if (document.getElementById('blabs-ct-idle-overlay')) return;
    if (!document.getElementById('blabs-ct-idle-style')) {{
      const style = document.createElement('style');
      style.id = 'blabs-ct-idle-style';
      style.textContent = `
        #blabs-ct-idle-overlay, #blabs-ct-ended-overlay {{
          position: fixed; inset: 0; background: rgba(0,0,0,0.72);
          color: #fff; display: none; align-items: center; justify-content: center;
          z-index: 2147483647; font-family: Arial, sans-serif;
        }}
        #blabs-ct-idle-card {{
          background: rgba(15,23,42,0.95); border: 1px solid #334155;
          border-radius: 12px; padding: 1.2rem 1.4rem; width: min(420px, calc(100vw - 2rem));
          box-shadow: 0 12px 40px rgba(0,0,0,0.35);
        }}
        #blabs-ct-idle-card h3 {{ margin: 0 0 0.5rem 0; font-size: 1.15rem; }}
        #blabs-ct-idle-card p {{ margin: 0.35rem 0; color: #cbd5e1; }}
        #blabs-ct-idle-actions {{ display: flex; gap: 0.5rem; margin-top: 0.75rem; }}
        #blabs-ct-idle-actions button {{
          flex: 1; padding: 0.58rem 0.75rem; border-radius: 10px; border: 1px solid #475569;
          cursor: pointer; font-weight: 700;
        }}
        #blabs-ct-idle-continue {{ background: #0ea5e9; color: #0b1727; }}
        #blabs-ct-idle-stop {{ background: transparent; color: #e2e8f0; }}
      `;
      document.head.appendChild(style);
    }}

    const idleOverlay = document.createElement('div');
    idleOverlay.id = 'blabs-ct-idle-overlay';
    idleOverlay.innerHTML = `
      <div id="blabs-ct-idle-card">
        <h3>Still using this lab?</h3>
        <p id="blabs-ct-idle-text"></p>
        <p id="blabs-ct-idle-countdown"></p>
        <div id="blabs-ct-idle-actions">
          <button id="blabs-ct-idle-stop">No, end lab</button>
          <button id="blabs-ct-idle-continue">Yes, continue</button>
        </div>
      </div>
    `;
    const endedOverlay = document.createElement('div');
    endedOverlay.id = 'blabs-ct-ended-overlay';
    endedOverlay.innerHTML = `
      <div id="blabs-ct-idle-card">
        <h3>Session ended</h3>
        <p id="blabs-ct-ended-text">Session ended due to inactivity.</p>
      </div>
    `;
    document.body.appendChild(idleOverlay);
    document.body.appendChild(endedOverlay);

    const idleText = document.getElementById('blabs-ct-idle-text');
    if (idleText) {{
      idleText.textContent = `No activity detected for ${{idleMinutes}} minute${{idleMinutes === 1 ? '' : 's'}}.`;
    }}

    const continueBtn = document.getElementById('blabs-ct-idle-continue');
    if (continueBtn) {{
      continueBtn.addEventListener('click', function () {{
        openerPost('idle-continue');
        hidePrompt();
        resetActivity(true, Date.now(), true);
      }});
    }}
    const stopBtn = document.getElementById('blabs-ct-idle-stop');
    if (stopBtn) {{
      stopBtn.addEventListener('click', function () {{
        endSession('user-end');
      }});
    }}
  }}

  function heartbeat(ts) {{
    if (!instanceId) return;
    const now = ts || Date.now();
    if (now - lastHeartbeatAt < 30000) return;
    lastHeartbeatAt = now;
    fetch(`${{apiBase.replace(/\\/$/, '')}}/user/containers/${{instanceId}}/activity`, {{
      method: 'POST',
      credentials: 'include',
      keepalive: true,
    }}).catch(function () {{}});
  }}

  function hidePrompt() {{
    const overlay = document.getElementById('blabs-ct-idle-overlay');
    if (overlay) overlay.style.display = 'none';
    promptActive = false;
    parentPromptActive = false;
    countdownEndsAt = 0;
    if (countdownTimer) {{
      clearInterval(countdownTimer);
      countdownTimer = null;
    }}
  }}

  function showEnded(reason) {{
    hidePrompt();
    const ended = document.getElementById('blabs-ct-ended-overlay');
    if (ended) ended.style.display = 'flex';
    const endedText = document.getElementById('blabs-ct-ended-text');
    if (endedText) {{
      endedText.textContent = reason === 'idle-timeout' ? 'Session ended due to inactivity.' : 'Session ended.';
    }}
  }}

  function requestDelete(reason) {{
    if (instanceId) {{
      fetch(`${{apiBase.replace(/\\/$/, '')}}/user/containers/${{instanceId}}`, {{
        method: 'DELETE',
        credentials: 'include',
        keepalive: true,
      }}).catch(function () {{}});
    }}
    openerPost('idle-stop', {{ reason: reason || 'idle-timeout', action: 'delete' }});
  }}

  function endSession(reason) {{
    showEnded(reason || 'idle-timeout');
    requestDelete(reason || 'idle-timeout');
  }}

  function updateCountdown() {{
    if (!promptActive || !countdownEndsAt) return;
    const remainingMs = countdownEndsAt - Date.now();
    if (remainingMs <= 0) {{
      endSession('idle-timeout');
      return;
    }}
    const remainingSeconds = Math.ceil(remainingMs / 1000);
    const mins = Math.floor(remainingSeconds / 60);
    const secs = String(remainingSeconds % 60).padStart(2, '0');
    const el = document.getElementById('blabs-ct-idle-countdown');
    if (el) el.textContent = `Ending in ${{mins}}:${{secs}}`;
  }}

  function startPrompt(startedAt) {{
    ensureOverlay();
    const overlay = document.getElementById('blabs-ct-idle-overlay');
    if (overlay) overlay.style.display = 'flex';
    promptActive = true;
    parentPromptActive = false;
    const baseline = startedAt || Date.now();
    countdownEndsAt = baseline + countdownSeconds * 1000;
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = setInterval(updateCountdown, 1000);
    updateCountdown();
  }}

  function scheduleIdle() {{
    if (idleTimer) {{
      clearTimeout(idleTimer);
      idleTimer = null;
    }}
    if (idleSuspended) return;
    const delay = Math.max(0, nextIdleAt - Date.now());
    idleTimer = setTimeout(function () {{
      startPrompt(nextIdleAt);
    }}, delay);
  }}

  function resetActivity(emit, ts, withHeartbeat) {{
    const now = ts || Date.now();
    if (idleSuspended) return;
    if (promptActive && parentPromptActive) {{
      if (emit !== false) openerPost('idle-activity');
      if (withHeartbeat !== false) heartbeat(now);
      return;
    }}
    if (promptActive) hidePrompt();
    nextIdleAt = now + Math.max(1, idleMinutes) * 60 * 1000;
    scheduleIdle();
    if (emit !== false) openerPost('idle-activity');
    if (withHeartbeat !== false) heartbeat(now);
  }}

  ensureOverlay();
  resetActivity(false, Date.now(), true);

  window.addEventListener('focus', function () {{
    idleSuspended = false;
    openerPost('idle-focus');
    resetActivity(false, Date.now(), true);
  }});
  window.addEventListener('blur', function () {{
    openerPost('idle-blur');
  }});
  document.addEventListener('visibilitychange', function () {{
    openerPost(document.hidden ? 'idle-blur' : 'idle-focus');
    if (!document.hidden) {{
      idleSuspended = false;
      resetActivity(false, Date.now(), true);
    }}
  }});

  // Only count direct user interactions. High-frequency events like scroll/mousemove
  // can fire continuously in some apps and prevent idle timeout from ever prompting.
  const events = ['keydown', 'mousedown', 'touchstart', 'pointerdown'];
  events.forEach(function (evt) {{
    document.addEventListener(evt, function (event) {{
      if (event && event.isTrusted === false) return;
      resetActivity(true, Date.now(), true);
    }}, {{ passive: true, capture: true }});
  }});

  // Keep backend activity fresh without generating synthetic user activity signals.
  window.setInterval(function () {{
    heartbeat(Date.now());
  }}, 30000);

  window.addEventListener('message', function (event) {{
    const payload = event.data || {{}};
    const messageOrigin = normalizeOrigin(event.origin);
    if (payload.source !== 'user') return;
    if (messageOrigin && !isAllowedOrigin(messageOrigin)) return;
    if (messageOrigin) {{
      openerOrigin = messageOrigin;
      rememberAllowedOrigin(messageOrigin);
    }}
    if (payload.type === 'idle-auth') {{
      if (payload.instanceId && payload.instanceId !== instanceId) return;
      if (payload.apiBase) {{
        apiBase = String(payload.apiBase).trim() || apiBase;
        rememberAllowedOrigin(apiBase);
      }}
      if (Array.isArray(payload.allowedOrigins)) {{
        payload.allowedOrigins.forEach(function (value) {{
          rememberAllowedOrigin(value);
        }});
      }}
      return;
    }}
    if (payload.type === 'idle-parent-prompt') {{
      if (payload.instanceId && payload.instanceId !== instanceId) return;
      const endsAt = Number(payload.endsAt);
      const targetEndsAt = Number.isFinite(endsAt) && endsAt > Date.now() ? endsAt : Date.now() + countdownSeconds * 1000;
      ensureOverlay();
      const overlay = document.getElementById('blabs-ct-idle-overlay');
      if (overlay) overlay.style.display = 'flex';
      promptActive = true;
      parentPromptActive = true;
      countdownEndsAt = targetEndsAt;
      if (countdownTimer) clearInterval(countdownTimer);
      countdownTimer = setInterval(updateCountdown, 1000);
      updateCountdown();
      return;
    }}
    if (payload.type === 'idle-parent-clear') {{
      if (payload.instanceId && payload.instanceId !== instanceId) return;
      parentPromptActive = false;
      hidePrompt();
      return;
    }}
    if (payload.type === 'idle-parent-ended') {{
      if (payload.instanceId && payload.instanceId !== instanceId) return;
      parentPromptActive = false;
      showEnded(String(payload.reason || 'idle-timeout'));
      return;
    }}
    if (payload.type === 'idle-focus' || payload.type === 'idle-blur' || payload.type === 'idle-activity') {{
      idleSuspended = false;
      const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
      resetActivity(false, ts, false);
      return;
    }}
  }});
}})();
"""


def _inject_container_idle_bridge_html(
    html_body: bytes,
    *,
    script_url: str,
) -> bytes:
    try:
        document = html_body.decode("utf-8")
    except UnicodeDecodeError:
        return html_body

    marker = "blabs-container-idle-bridge"
    if marker in document:
        return html_body

    script_tag = f'\n<script id="{marker}" src="{html.escape(script_url, quote=True)}" defer></script>\n'
    lower_doc = document.lower()
    body_idx = lower_doc.rfind("</body>")
    if body_idx >= 0:
        injected = document[:body_idx] + script_tag + document[body_idx:]
    else:
        injected = document + script_tag
    return injected.encode("utf-8")


def _container_service_ready(
    instance_id: str,
    namespace: str,
    container_port: int,
    *,
    protocol: str = "tcp",
    healthcheck_path: str = "/",
    expected_http_status: int = 200,
    success_path: str | None = None,
) -> bool:
    host = _container_service_host(instance_id, namespace)
    port = max(1, min(65535, int(container_port or 80)))
    normalized_protocol = str(protocol or "tcp").lower()
    path = _normalize_http_path(healthcheck_path) or "/"
    secondary_path = _normalize_http_path(success_path, allow_blank=True)
    try:
        if normalized_protocol == "http":
            expected = max(100, min(599, int(expected_http_status or 200)))
            conn = HTTPConnection(host, port, timeout=1.8)
            conn.request("GET", path)
            response = conn.getresponse()
            response.read(256)
            if response.status != expected:
                return False
            if secondary_path:
                conn = HTTPConnection(host, port, timeout=1.8)
                conn.request("GET", secondary_path)
                response = conn.getresponse()
                response.read(256)
                if response.status != expected:
                    return False
            return True
        with socket.create_connection((host, port), timeout=1.2):
            return True
    except OSError:
        return False


def _nodeport_ready(
    node_port: int | None,
    *,
    protocol: str = "tcp",
    healthcheck_path: str = "/",
    expected_http_status: int = 200,
    success_path: str | None = None,
) -> bool:
    if not node_port:
        return False
    host = (settings.kube_node_external_host or "").strip()
    if not host:
        return True
    normalized_protocol = str(protocol or "tcp").lower()
    path = _normalize_http_path(healthcheck_path) or "/"
    secondary_path = _normalize_http_path(success_path, allow_blank=True)
    try:
        if normalized_protocol == "http":
            expected = max(100, min(599, int(expected_http_status or 200)))
            conn = HTTPConnection(host, int(node_port), timeout=1.8)
            conn.request("GET", path)
            response = conn.getresponse()
            response.read(256)
            if response.status != expected:
                return False
            if secondary_path:
                conn = HTTPConnection(host, int(node_port), timeout=1.8)
                conn.request("GET", secondary_path)
                response = conn.getresponse()
                response.read(256)
                if response.status != expected:
                    return False
            return True
        with socket.create_connection((host, int(node_port)), timeout=1.2):
            return True
    except OSError:
        return False


def _parse_args(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


def _parse_env(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _template_out(
    record: ContainerTemplateTable,
    *,
    idle_timeout_cap: int | None = None,
    auto_delete_cap: int | None = None,
) -> ContainerTemplateView:
    template_idle = max(
        1,
        int(getattr(record, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes),
    )
    if idle_timeout_cap is not None:
        template_idle = min(template_idle, max(1, int(idle_timeout_cap)))
    template_auto_delete = max(1, int(getattr(record, "auto_delete_minutes", 60) or 60))
    if auto_delete_cap is not None:
        template_auto_delete = min(template_auto_delete, max(1, int(auto_delete_cap)))
    return ContainerTemplateView(
        id=record.id,
        template_key=str(getattr(record, "template_key", record.id) or record.id),
        version=max(1, int(getattr(record, "version", 1) or 1)),
        is_default=bool(getattr(record, "is_default", True)),
        name=record.name,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=GLOBAL_TENANT),
        namespace=_container_template_namespace(record),
        enabled_namespaces=_container_template_enabled_namespaces(record),
        cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
        description=record.description,
        container_image_id=record.container_image_id,
        cpu_millicores=record.cpu_millicores,
        memory_mb=record.memory_mb,
        container_port=max(1, int(getattr(record, "container_port", 80) or 80)),
        healthcheck_protocol=str(getattr(record, "healthcheck_protocol", "tcp") or "tcp"),
        healthcheck_path=str(getattr(record, "healthcheck_path", "/") or "/"),
        readiness_http_status=max(100, min(599, int(getattr(record, "readiness_http_status", 200) or 200))),
        readiness_success_path=_normalize_http_path(getattr(record, "readiness_success_path", None), allow_blank=True),
        startup_timeout_seconds=max(10, int(getattr(record, "startup_timeout_seconds", 300) or 300)),
        dependency_checks=_parse_dependency_checks(getattr(record, "dependency_checks_json", "[]")),
        expose_strategy=str(getattr(record, "expose_strategy", "nodeport") or "nodeport"),
        network_mode=str(getattr(record, "network_mode", "bridge") or "bridge"),
        run_as_non_root=bool(getattr(record, "run_as_non_root", False)),
        read_only_root_filesystem=bool(getattr(record, "read_only_root_filesystem", False)),
        command=record.command,
        args=_parse_args(record.args_json),
        env=_parse_env(record.env_json),
        auto_delete_minutes=template_auto_delete,
        idle_timeout_minutes=template_idle,
        enabled=record.enabled,
        created_at=record.created_at,
    )


def _instance_out(
    record: ContainerInstanceTable,
    *,
    stage: str | None = None,
    detail: str | None = None,
    access_url: str | None = None,
    container_port: int | None = None,
    launch_diagnostics: list[str] | None = None,
) -> ContainerInstanceView:
    resolved_stage, resolved_detail = _status_feedback(record.status, None)
    return ContainerInstanceView(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        tenant=normalize_tenant(getattr(record, "tenant", None), default="default"),
        namespace=str(getattr(record, "namespace", "") or settings.kube_namespace),
        cluster_id=_container_instance_cluster_id(record),
        status=record.status,
        status_stage=stage or resolved_stage,
        status_detail=detail or resolved_detail,
        pod_name=record.pod_name,
        access_url=access_url,
        container_port=container_port,
        queue_attempts=max(0, int(getattr(record, "queue_attempts", 0) or 0)),
        queue_not_before=getattr(record, "queue_not_before", None),
        queue_reason=(getattr(record, "queue_reason", None) or None),
        launch_diagnostics=list(launch_diagnostics or []),
        started_at=record.started_at,
        last_active_at=record.last_active_at,
    )


def _active_workload_count(session: Session) -> int:
    total_vm_active = session.exec(select(Instance).where(Instance.status.in_(["pending", "running"]))).all()
    total_container_active = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.status.in_(["pending", "running"]))
    ).all()
    return len(total_vm_active) + len(total_container_active)


def _queued_container_count_for_namespace(session: Session, namespace: str) -> int:
    normalized_namespace = normalize_namespace(namespace)
    if not normalized_namespace:
        return 0
    return int(
        session.exec(
            select(func.count())
            .select_from(ContainerInstanceTable)
            .where(ContainerInstanceTable.namespace == normalized_namespace)
            .where(ContainerInstanceTable.status == "queued")
        ).one()
        or 0
    )


def _active_container_template_count(
    session: Session,
    template_id: str,
    *,
    exclude_instance_id: str | None = None,
) -> int:
    rows = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.template_id == template_id)
        .where(ContainerInstanceTable.status.in_(["queued", "pending", "running"]))
    ).all()
    if exclude_instance_id:
        rows = [row for row in rows if row.id != exclude_instance_id]
    return len(rows)


def _template_limit(template: ContainerTemplateTable) -> int:
    return max(0, int(getattr(template, "max_active_instances", 2) or 0))


def _container_headroom_error(template: ContainerTemplateTable) -> str | None:
    return check_launch_headroom(
        request_cpu_m=max(1, int(getattr(template, "cpu_millicores", 0) or 0)),
        request_memory_mb=max(1, int(getattr(template, "memory_mb", 0) or 0)),
    )


def _mark_queued(record: ContainerInstanceTable, reason: str, *, increment_attempt: bool = True) -> None:
    attempts = max(0, int(getattr(record, "queue_attempts", 0) or 0))
    if increment_attempt:
        attempts += 1
    delay_seconds = _queue_backoff_seconds(attempts)
    queue_reason = _humanize_queue_reason(reason)
    record.status = "queued"
    record.queue_attempts = attempts
    record.queue_reason = queue_reason[:255]
    record.queue_not_before = utc_now() + timedelta(seconds=delay_seconds)
    record.last_active_at = utc_now()


def _normalized_template_command(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Guard against legacy rows accidentally storing Python None/null as text.
    if raw.lower() in {"none", "null"}:
        return None
    return raw


def _container_launch_request(
    instance_id: str,
    owner: str,
    template: ContainerTemplateTable,
    image_ref: str,
    namespace: str,
) -> ContainerPodRequest:
    return ContainerPodRequest(
        instance_id=instance_id,
        owner=owner,
        image_ref=image_ref,
        namespace=namespace,
        cpu_millicores=template.cpu_millicores,
        memory_mb=template.memory_mb,
        container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
        healthcheck_protocol=str(getattr(template, "healthcheck_protocol", "tcp") or "tcp"),
        healthcheck_path=str(getattr(template, "healthcheck_path", "/") or "/"),
        readiness_http_status=max(100, min(599, int(getattr(template, "readiness_http_status", 200) or 200))),
        readiness_success_path=_normalize_http_path(
            getattr(template, "readiness_success_path", None), allow_blank=True
        ),
        startup_timeout_seconds=max(10, int(getattr(template, "startup_timeout_seconds", 300) or 300)),
        dependency_checks=_parse_dependency_checks(getattr(template, "dependency_checks_json", "[]")),
        expose_strategy=str(getattr(template, "expose_strategy", "nodeport") or "nodeport"),
        network_mode=str(getattr(template, "network_mode", "bridge") or "bridge"),
        run_as_non_root=bool(getattr(template, "run_as_non_root", False)),
        read_only_root_filesystem=bool(getattr(template, "read_only_root_filesystem", False)),
        command=_normalized_template_command(template.command),
        args=_parse_args(template.args_json),
        env=_parse_env(template.env_json),
    )


def _attach_connect_session_cookie(response: Response, request: Request, instance_id: str, token_value: str) -> None:
    base_path = f"/user/containers/{instance_id}/connect/"
    response.set_cookie(
        key=_CONNECT_SESSION_COOKIE_NAME,
        value=token_value,
        max_age=max(60, int(settings.connect_session_ttl_seconds or 3600)),
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
        path=base_path,
    )
    response.delete_cookie(
        key=_CONNECT_GRANT_COOKIE_NAME,
        path=base_path,
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
    )


@router.post("/containers/{instance_id}/connect-token")
def issue_container_connect_token(
    instance_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    if record.status not in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container is not running")
    grant_token = issue_connect_token(
        session,
        username=user.username,
        instance_id=instance_id,
        resource_type="container",
        token_type="grant",
        ttl_seconds=max(15, int(settings.connect_grant_ttl_seconds or 120)),
    )
    connect_url = f"{_request_base_url(request).rstrip('/')}/user/containers/{instance_id}/connect/"
    template = session.get(ContainerTemplateTable, record.template_id)
    if _is_kasm_template(session, template):
        ws_path = f"user/containers/{instance_id}/connect/websockify"
        connect_url = f"{connect_url}?{urlencode({'path': ws_path})}"
    response = Response(
        content=json.dumps({"connect_url": connect_url}),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    response.set_cookie(
        key=_CONNECT_GRANT_COOKIE_NAME,
        value=grant_token,
        max_age=max(15, int(settings.connect_grant_ttl_seconds or 120)),
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
        path=f"/user/containers/{instance_id}/connect/",
    )
    return response


@router.get(
    "/containers/{instance_id}/connect-readiness",
    response_model=ContainerConnectReadiness,
)
async def container_connect_readiness(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerConnectReadiness:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")

    if record.status != "running":
        stage, detail = _status_feedback(record.status, None)
        return ContainerConnectReadiness(
            ready=False,
            detail=detail or f"container is {stage}",
            checked_at=utc_now(),
        )

    cached = _readiness_cache_get(record.id)
    if cached is not None:
        ready, detail = cached
        return ContainerConnectReadiness(ready=ready, detail=detail, checked_at=utc_now())

    template = session.get(ContainerTemplateTable, record.template_id)
    if template is None:
        readiness = ContainerConnectReadiness(
            ready=False,
            detail="container template not found for readiness check",
            checked_at=utc_now(),
        )
        _readiness_cache_set(record.id, ready=readiness.ready, detail=readiness.detail)
        return readiness

    instance_namespace = _container_instance_namespace(record, user)
    record_cluster_id = _container_instance_cluster_id(record)
    runtime_kube = _kube_for_container_cluster(session, record_cluster_id)
    container_port = max(1, int(getattr(template, "container_port", 80) or 80))
    healthcheck_protocol = str(getattr(template, "healthcheck_protocol", "tcp") or "tcp")
    healthcheck_path = str(getattr(template, "healthcheck_path", "/") or "/")
    readiness_http_status = max(100, min(599, int(getattr(template, "readiness_http_status", 200) or 200)))
    readiness_success_path = _normalize_http_path(getattr(template, "readiness_success_path", None), allow_blank=True)
    expose_strategy = str(getattr(template, "expose_strategy", "nodeport") or "nodeport")
    ingress_enabled = (
        expose_strategy == "ingress"
        and settings.container_ingress_enabled
        and bool((settings.container_ingress_base_domain or "").strip())
    )
    service_type = "ClusterIP" if ingress_enabled else "NodePort"

    try:
        node_port = runtime_kube.ensure_container_service(
            record.id,
            record.owner,
            container_port,
            service_type=service_type,
            namespace=instance_namespace,
        )
        ingress_host = None
        if ingress_enabled:
            ingress_host = runtime_kube.ensure_container_ingress(
                record.id,
                f"ctsvc-{record.id[:8]}",
                container_port,
                namespace=instance_namespace,
            )
            if ingress_host is None:
                node_port = runtime_kube.ensure_container_service(
                    record.id,
                    record.owner,
                    container_port,
                    service_type="NodePort",
                    namespace=instance_namespace,
                )
    except ApiException as exc:
        if exc.status == 404:
            readiness = ContainerConnectReadiness(
                ready=False,
                detail="container service is not available yet",
                checked_at=utc_now(),
            )
            _readiness_cache_set(record.id, ready=readiness.ready, detail=readiness.detail)
            return readiness
        raise

    service_ready = _container_service_ready(
        record.id,
        instance_namespace,
        container_port,
        protocol=healthcheck_protocol,
        healthcheck_path=healthcheck_path,
        expected_http_status=readiness_http_status,
        success_path=readiness_success_path,
    )
    externally_ready = ingress_host is not None or _nodeport_ready(
        node_port,
        protocol=healthcheck_protocol,
        healthcheck_path=healthcheck_path,
        expected_http_status=readiness_http_status,
        success_path=readiness_success_path,
    )
    if not service_ready or not externally_ready:
        readiness = ContainerConnectReadiness(
            ready=False,
            detail="container app is still starting",
            checked_at=utc_now(),
        )
        _readiness_cache_set(record.id, ready=readiness.ready, detail=readiness.detail)
        return readiness

    if not _container_requires_ws_probe(session, template):
        readiness = ContainerConnectReadiness(ready=True, detail="ready", checked_at=utc_now())
        _readiness_cache_set(record.id, ready=readiness.ready, detail=readiness.detail)
        return readiness

    ws_ready, ws_detail = await _probe_container_connect_websocket(
        session=session,
        template=template,
        instance_id=record.id,
        namespace=instance_namespace,
        container_port=container_port,
    )
    readiness = ContainerConnectReadiness(ready=ws_ready, detail=ws_detail, checked_at=utc_now())
    _readiness_cache_set(record.id, ready=readiness.ready, detail=readiness.detail)
    return readiness


@router.api_route(
    "/containers/{instance_id}/connect",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
    operation_id="container_connect_proxy_root",
)
@router.api_route(
    "/containers/{instance_id}/connect/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
    operation_id="container_connect_proxy_path",
)
async def proxy_container_connect(
    instance_id: str,
    request: Request,
    proxy_path: str = "",
    session: Session = Depends(get_session),
) -> Response:
    base_path = f"/user/containers/{instance_id}/connect"
    if request.url.path == base_path:
        query = str(request.url.query or "").strip()
        target = f"{base_path}/"
        if query:
            target = f"{target}?{query}"
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    issued_connect_session = ""
    user: User | None = None
    connect_session_token = _extract_connect_session_token(request)
    if connect_session_token:
        try:
            user = validate_connect_session(
                session,
                token_value=connect_session_token,
                instance_id=instance_id,
                resource_type="container",
            )
        except HTTPException:
            user = None
    if user is None:
        grant_token = _extract_connect_grant_token(request)
        if not grant_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing connect token")
        user = consume_connect_grant(
            session,
            token_value=grant_token,
            instance_id=instance_id,
            resource_type="container",
        )
        issued_connect_session = issue_connect_token(
            session,
            username=user.username,
            instance_id=instance_id,
            resource_type="container",
            token_type="session",
            ttl_seconds=max(60, int(settings.connect_session_ttl_seconds or 3600)),
        )

    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    if record.status not in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container is not running")
    instance_namespace = _container_instance_namespace(record, user)
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()

    template = session.get(ContainerTemplateTable, record.template_id)
    is_kasm = _is_kasm_template(session, template)
    container_port = max(1, int(getattr(template, "container_port", 80) or 80)) if template else 80
    idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), instance_namespace)
    template_idle_minutes = max(
        1,
        int(getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes),
    )
    if idle_cap is not None:
        template_idle_minutes = min(template_idle_minutes, max(1, int(idle_cap)))

    if proxy_path.strip("/") == "__blabs_idle_bridge.js":
        bridge_script = _container_idle_bridge_javascript(
            instance_id=record.id,
            idle_minutes=template_idle_minutes,
        )
        response = Response(content=bridge_script, media_type="application/javascript")
        response.headers["cache-control"] = "no-store, max-age=0"
        if issued_connect_session:
            _attach_connect_session_cookie(response, request, instance_id, issued_connect_session)
        return response

    upstream_host = _container_service_host(record.id, instance_namespace)
    upstream_path = _normalized_upstream_path(proxy_path)
    query_items = [(key, value) for key, value in request.query_params.multi_items() if key != "ct"]
    upstream_query = urlencode(query_items, doseq=True)

    forwarded_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered in {
            "host",
            "authorization",
            "cookie",
            "content-length",
            "accept-encoding",
        }:
            continue
        forwarded_headers[key] = value
    upstream_auth_header = _upstream_basic_auth_header(session, template)
    if upstream_auth_header:
        forwarded_headers["Authorization"] = upstream_auth_header
    forwarded_headers["X-Forwarded-Proto"] = "https"
    forwarded_headers["X-Forwarded-Host"] = str(request.headers.get("host") or "")
    forwarded_headers["X-Forwarded-Prefix"] = f"/user/containers/{instance_id}/connect"

    body = await request.body()
    upstream: requests.Response | None = None
    last_exc: requests.RequestException | None = None
    attempted_urls: list[str] = []
    for scheme in _container_http_schemes(template, container_port):
        upstream_url = f"{scheme}://{upstream_host}:{container_port}{upstream_path}"
        if upstream_query:
            upstream_url = f"{upstream_url}?{upstream_query}"
        attempted_urls.append(upstream_url)
        verify_tls = scheme != "https"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                candidate = requests.request(
                    method=request.method,
                    url=upstream_url,
                    headers=forwarded_headers,
                    data=body if body else None,
                    cookies=request.cookies,
                    allow_redirects=False,
                    stream=False,
                    timeout=_PROXY_TIMEOUT_SECONDS,
                    verify=verify_tls,
                )
            if scheme == "http" and _upstream_requires_https(candidate):
                continue
            upstream = candidate
            break
        except requests.RequestException as exc:
            last_exc = exc
            continue
    if upstream is None:
        detail = str(last_exc) if last_exc else "no upstream response"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"container connect proxy failed: {detail}; attempted={', '.join(attempted_urls)}",
        ) from last_exc

    response_headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS:
            continue
        if lowered == "location":
            response_headers[key] = _rewrite_upstream_location(instance_id, value)
            continue
        if lowered in {"content-length", "content-encoding"}:
            continue
        response_headers[key] = value
    response_content = upstream.content
    content_type = str(upstream.headers.get("content-type") or "").lower()
    if request.method.upper() != "HEAD" and "text/html" in content_type:
        bridge_script_url = f"{base_path}/__blabs_idle_bridge.js"
        response_content = _inject_container_idle_bridge_html(
            response_content,
            script_url=bridge_script_url,
        )
    response = Response(
        content=response_content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
    if issued_connect_session:
        _attach_connect_session_cookie(response, request, instance_id, issued_connect_session)
    return response


@router.websocket("/containers/{instance_id}/connect/{proxy_path:path}")
async def proxy_container_connect_ws(
    instance_id: str,
    websocket: WebSocket,
    proxy_path: str,
    session: Session = Depends(get_session),
) -> None:
    resource_type = "container"
    token_value = _extract_connect_session_token_ws(websocket)
    if not token_value:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4401")
        await websocket.close(code=4401, reason="missing connect session")
        return
    try:
        user = validate_connect_session(
            session,
            token_value=token_value,
            instance_id=instance_id,
            resource_type="container",
        )
    except HTTPException:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4401")
        await websocket.close(code=4401, reason="invalid connect session")
        return

    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4404")
        await websocket.close(code=4404, reason="container not found")
        return
    if record.status not in {"pending", "running"}:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4409")
        await websocket.close(code=4409, reason="container not running")
        return
    instance_namespace = _container_instance_namespace(record, user)
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()

    template = session.get(ContainerTemplateTable, record.template_id)
    is_kasm = _is_kasm_template(session, template)
    container_port = max(1, int(getattr(template, "container_port", 80) or 80)) if template else 80
    upstream_host = _container_service_host(record.id, instance_namespace)
    upstream_path = _normalized_upstream_path(proxy_path)
    query_items = [(key, value) for key, value in websocket.query_params.multi_items() if key != "ct"]
    upstream_query = urlencode(query_items, doseq=True)

    protocols = [
        part.strip() for part in str(websocket.headers.get("sec-websocket-protocol") or "").split(",") if part.strip()
    ]
    upstream_auth_header = _upstream_basic_auth_header(session, template)
    upstream_origin = str(websocket.headers.get("origin") or "").strip()
    if not upstream_origin:
        forwarded_host = str(websocket.headers.get("host") or "").strip()
        if forwarded_host:
            public_scheme = (settings.public_scheme or "https").strip() or "https"
            upstream_origin = f"{public_scheme}://{forwarded_host}"
    upstream_ws_headers: dict[str, str] = {}
    if upstream_auth_header:
        upstream_ws_headers["Authorization"] = upstream_auth_header
    # KasmVNC/Websockify expects legacy Sec-WebSocket-Origin. For non-Kasm containers,
    # send only standard Origin to avoid duplicate-origin handshake failures.
    if upstream_origin and is_kasm:
        upstream_ws_headers["Sec-WebSocket-Origin"] = upstream_origin
    if not upstream_ws_headers:
        upstream_ws_headers = None

    selected_subprotocol = protocols[0] if protocols else None
    await websocket.accept(subprotocol=selected_subprotocol)

    attempted_urls: list[str] = []
    attempt_errors: list[str] = []
    last_exc: Exception | None = None
    for scheme in _container_ws_schemes(template, container_port):
        upstream_url = f"{scheme}://{upstream_host}:{container_port}{upstream_path}"
        if upstream_query:
            upstream_url = f"{upstream_url}?{upstream_query}"
        attempted_urls.append(upstream_url)
        ssl_context = _tls_client_context() if scheme == "wss" else None
        try:
            async with websockets.connect(
                upstream_url,
                subprotocols=protocols or None,
                additional_headers=upstream_ws_headers,
                origin=None if is_kasm else (upstream_origin or None),
                open_timeout=15,
                close_timeout=5,
                max_size=None,
                ssl=ssl_context,
            ) as upstream:
                ws_metrics.record_handshake(resource_type, success=True)
                connection_started_at = ws_metrics.mark_connection_open(resource_type)

                async def client_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        kind = message.get("type")
                        if kind == "websocket.disconnect":
                            ws_metrics.record_disconnect(
                                resource_type,
                                direction="downstream",
                                code=message.get("code"),
                            )
                            await upstream.close()
                            return
                        payload_bytes = message.get("bytes")
                        if payload_bytes is not None:
                            await upstream.send(payload_bytes)
                            continue
                        payload_text = message.get("text")
                        if payload_text is not None:
                            await upstream.send(payload_text)

                async def upstream_to_client() -> None:
                    while True:
                        try:
                            payload = await upstream.recv()
                        except Exception as exc:
                            ws_metrics.record_disconnect(
                                resource_type,
                                direction="upstream",
                                code=ws_metrics.extract_close_code(exc),
                            )
                            raise
                        if isinstance(payload, bytes):
                            await websocket.send_bytes(payload)
                        else:
                            await websocket.send_text(payload)

                try:
                    task_client = asyncio.create_task(client_to_upstream())
                    task_upstream = asyncio.create_task(upstream_to_client())
                    done, pending = await asyncio.wait(
                        {task_client, task_upstream},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await asyncio.gather(*done, return_exceptions=True)
                    return
                finally:
                    ws_metrics.mark_connection_close(resource_type, connection_started_at)
        except Exception as exc:
            last_exc = exc
            attempt_errors.append(f"{upstream_url} -> {type(exc).__name__}: {exc}")
            continue

    ws_metrics.record_handshake(resource_type, success=False)
    ws_metrics.record_disconnect(
        resource_type,
        direction="upstream",
        code=ws_metrics.extract_close_code(last_exc),
    )
    exc_info = (type(last_exc), last_exc, last_exc.__traceback__) if last_exc else None
    logger.warning(
        "Container websocket proxy failed for instance %s path %s attempted=%s errors=%s",
        instance_id,
        proxy_path,
        ", ".join(attempted_urls),
        " | ".join(attempt_errors),
        exc_info=exc_info,
    )
    try:
        await websocket.close(code=1011, reason="upstream websocket error")
    except Exception:
        pass


def _create_container_runtime(
    *,
    instance_id: str,
    owner: str,
    team: str | None,
    namespace: str,
    runtime_kube,
    template: ContainerTemplateTable,
    image_ref: str,
) -> tuple[PodStatus, str | None, int]:
    ensure_team_runtime_namespace(runtime_kube, team=team, namespace=namespace)
    pod_status = runtime_kube.create_container_pod(
        _container_launch_request(instance_id, owner, template, image_ref, namespace)
    )
    container_port = max(1, int(getattr(template, "container_port", 80) or 80))
    expose_strategy = str(getattr(template, "expose_strategy", "nodeport") or "nodeport")
    ingress_enabled = (
        expose_strategy == "ingress"
        and settings.container_ingress_enabled
        and bool((settings.container_ingress_base_domain or "").strip())
    )
    service_type = "ClusterIP" if ingress_enabled else "NodePort"
    try:
        node_port = runtime_kube.ensure_container_service(
            instance_id,
            owner,
            container_port,
            service_type=service_type,
            namespace=namespace,
        )
        ingress_host = None
        if ingress_enabled:
            ingress_host = runtime_kube.ensure_container_ingress(
                instance_id,
                f"ctsvc-{instance_id[:8]}",
                container_port,
                namespace=namespace,
            )
            if ingress_host is None:
                node_port = runtime_kube.ensure_container_service(
                    instance_id,
                    owner,
                    container_port,
                    service_type="NodePort",
                    namespace=namespace,
                )
        access_url = _container_access_url_for_target(node_port=node_port, ingress_host=ingress_host)
        return pod_status, access_url, container_port
    except Exception:
        try:
            runtime_kube.delete_container_pod(instance_id, owner, namespace=namespace)
            runtime_kube.delete_container_service(instance_id, namespace=namespace)
        except Exception:
            pass
        raise


@router.get("/container-templates", response_model=list[ContainerTemplateView])
def list_user_container_templates(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerTemplateView]:
    selected_namespace = _resolve_selected_namespace(session, user, request)
    runtime_namespace = _container_runtime_namespace(user, namespace=selected_namespace)
    namespace_policy = get_namespace_runtime_policy(session, selected_namespace)
    team_idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), runtime_namespace)
    tenant_scope = {
        "default",
        GLOBAL_TENANT,
    }
    rows = session.exec(
        select(ContainerTemplateTable)
        .where(ContainerTemplateTable.enabled == True)  # noqa: E712
        .where(ContainerTemplateTable.is_default == True)  # noqa: E712
        .where(ContainerTemplateTable.tenant.in_(tenant_scope))
    ).all()
    rows = [row for row in rows if _container_template_enabled_for_namespace(row, selected_namespace)]
    rows.sort(key=lambda item: item.created_at, reverse=True)
    effective_idle_cap = (
        min(team_idle_cap, namespace_policy.idle_timeout_minutes_default)
        if team_idle_cap
        else int(namespace_policy.idle_timeout_minutes_default)
    )
    return [
        _template_out(
            row,
            idle_timeout_cap=effective_idle_cap,
            auto_delete_cap=namespace_policy.container_auto_delete_minutes_default,
        )
        for row in rows
    ]


@router.get("/containers", response_model=list[ContainerInstanceView])
def list_user_containers(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ContainerInstanceView]:
    selected_namespace = _resolve_selected_namespace(session, user, request)
    config = session.get(Config, 1) or Config()
    max_concurrency = int(config.max_concurrent_vms)
    active_count = _active_workload_count(session)
    instances = session.exec(
        select(ContainerInstanceTable)
        .where(ContainerInstanceTable.owner == user.username)
        .where(ContainerInstanceTable.namespace == selected_namespace)
    ).all()
    templates = {row.id: row for row in session.exec(select(ContainerTemplateTable)).all()}
    images = {row.id: row for row in session.exec(select(ContainerImageTable)).all()}
    changed = False
    feedback: dict[str, tuple[str, str]] = {}
    access_map: dict[str, str | None] = {}
    port_map: dict[str, int | None] = {}
    diagnostics_map: dict[str, list[str]] = {}
    to_delete: list[ContainerInstanceTable] = []

    for record in instances:
        record_namespace = _container_instance_namespace(record, user)
        record_cluster_id = _container_instance_cluster_id(record)
        runtime_kube = _kube_for_container_cluster(session, record_cluster_id)
        if record.status in {"running", "pending"}:
            # Match VM behavior so active user polling keeps running labs from being reaped.
            record.last_active_at = utc_now()
            session.add(record)
            changed = True
        tmpl = templates.get(record.template_id)
        container_port = max(1, int(getattr(tmpl, "container_port", 80) or 80)) if tmpl else 80
        healthcheck_protocol = str(getattr(tmpl, "healthcheck_protocol", "tcp") or "tcp") if tmpl else "tcp"
        healthcheck_path = str(getattr(tmpl, "healthcheck_path", "/") or "/") if tmpl else "/"
        readiness_http_status = (
            max(100, min(599, int(getattr(tmpl, "readiness_http_status", 200) or 200))) if tmpl else 200
        )
        readiness_success_path = (
            _normalize_http_path(getattr(tmpl, "readiness_success_path", None), allow_blank=True) if tmpl else None
        )
        expose_strategy = str(getattr(tmpl, "expose_strategy", "nodeport") or "nodeport") if tmpl else "nodeport"
        ingress_enabled = (
            expose_strategy == "ingress"
            and settings.container_ingress_enabled
            and bool((settings.container_ingress_base_domain or "").strip())
        )
        service_type = "ClusterIP" if ingress_enabled else "NodePort"
        port_map[record.id] = container_port
        if record.status == "queued":
            if not settings.container_start_queue_enabled or not tmpl:
                feedback[record.id] = (
                    "queued",
                    record.queue_reason or "Queued for retry when resources are available.",
                )
                access_map[record.id] = None
            else:
                now = utc_now()
                not_before = getattr(record, "queue_not_before", None)
                should_try = not_before is None or now >= not_before
                image = images.get(tmpl.container_image_id)
                if should_try and image and active_count < max_concurrency:
                    template_limit = _template_limit(tmpl)
                    template_active_count = _active_container_template_count(
                        session,
                        tmpl.id,
                        exclude_instance_id=record.id,
                    )
                    if template_limit and template_active_count >= template_limit:
                        _mark_queued(
                            record,
                            f"Template concurrency limit reached ({template_limit}).",
                            increment_attempt=False,
                        )
                        feedback[record.id] = ("queued", record.queue_reason or "Queued for retry.")
                        access_map[record.id] = None
                        session.add(record)
                        changed = True
                        diagnostics_map[record.id] = []
                        continue
                    headroom_error = _container_headroom_error(tmpl)
                    if headroom_error:
                        _mark_queued(record, headroom_error, increment_attempt=False)
                        feedback[record.id] = ("queued", record.queue_reason or "Queued for retry.")
                        access_map[record.id] = None
                        session.add(record)
                        changed = True
                        diagnostics_map[record.id] = []
                        continue
                    try:
                        pod_status, access_url, _ = _create_container_runtime(
                            instance_id=record.id,
                            owner=record.owner,
                            team="default",
                            namespace=record_namespace,
                            runtime_kube=runtime_kube,
                            template=tmpl,
                            image_ref=image.image_ref,
                        )
                        record.status = "pending"
                        record.pod_name = runtime_kube.container_pod_name(instance_id=record.id, owner=record.owner)
                        record.started_at = now
                        record.last_active_at = now
                        record.queue_not_before = None
                        record.queue_reason = None
                        stage, detail = _status_feedback("pending", pod_status)
                        feedback[record.id] = (stage, detail)
                        access_map[record.id] = access_url
                        session.add(record)
                        changed = True
                        active_count += 1
                    except Exception as exc:
                        _mark_queued(record, str(exc) or "Waiting for available resources.")
                        feedback[record.id] = ("queued", record.queue_reason or "Queued for retry.")
                        access_map[record.id] = None
                        session.add(record)
                        changed = True
                else:
                    retry_in = 0
                    if not_before:
                        retry_in = max(0, int((not_before - now).total_seconds()))
                    reason = record.queue_reason or "Waiting for available resources."
                    if retry_in > 0:
                        reason = f"{reason} Retrying in {retry_in}s."
                    feedback[record.id] = ("queued", reason)
                    access_map[record.id] = None
            diagnostics_map[record.id] = []
            continue

        pod_status: PodStatus | None = None
        try:
            pod_status = runtime_kube.get_container_status(record.id, record.owner, namespace=record_namespace)
            mapped = _phase_to_status(pod_status.phase)
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise

        stage, detail = _status_feedback(mapped, pod_status)
        feedback[record.id] = (stage, detail)
        try:
            diagnostics_map[record.id] = runtime_kube.get_container_launch_diagnostics(
                record.id,
                record.owner,
                namespace=record_namespace,
            )
        except Exception:
            diagnostics_map[record.id] = []
        if mapped in {"pending", "running"} and tmpl:
            try:
                node_port = runtime_kube.ensure_container_service(
                    record.id,
                    record.owner,
                    container_port,
                    service_type=service_type,
                    namespace=record_namespace,
                )
                ingress_host = None
                if ingress_enabled:
                    ingress_host = runtime_kube.ensure_container_ingress(
                        record.id,
                        f"ctsvc-{record.id[:8]}",
                        container_port,
                        namespace=record_namespace,
                    )
                    if ingress_host is None:
                        node_port = runtime_kube.ensure_container_service(
                            record.id,
                            record.owner,
                            container_port,
                            service_type="NodePort",
                            namespace=record_namespace,
                        )
                if (
                    mapped == "running"
                    and _container_service_ready(
                        record.id,
                        record_namespace,
                        container_port,
                        protocol=healthcheck_protocol,
                        healthcheck_path=healthcheck_path,
                        expected_http_status=readiness_http_status,
                        success_path=readiness_success_path,
                    )
                    and (
                        ingress_host is not None
                        or _nodeport_ready(
                            node_port,
                            protocol=healthcheck_protocol,
                            healthcheck_path=healthcheck_path,
                            expected_http_status=readiness_http_status,
                            success_path=readiness_success_path,
                        )
                    )
                ):
                    access_map[record.id] = _container_access_url_for_target(
                        node_port=node_port, ingress_host=ingress_host
                    )
                elif mapped == "running":
                    feedback[record.id] = (
                        "starting",
                        "Container pod is running; waiting for application startup.",
                    )
                    access_map[record.id] = None
                else:
                    access_map[record.id] = None
            except ApiException as exc:
                if exc.status != 404:
                    raise
                access_map[record.id] = None
        else:
            access_map[record.id] = None
            try:
                runtime_kube.delete_container_service(record.id, namespace=record_namespace)
            except Exception:
                pass

        if mapped == "pending" and settings.container_start_queue_enabled:
            reason_text = " ".join(
                [
                    (pod_status.waiting_reason or "").lower() if pod_status else "",
                    (pod_status.reason or "").lower() if pod_status else "",
                    (pod_status.message or "").lower() if pod_status else "",
                ]
            )
            if "unschedulable" in reason_text or "failedscheduling" in reason_text:
                try:
                    runtime_kube.delete_container_pod(record.id, record.owner, namespace=record_namespace)
                    runtime_kube.delete_container_service(record.id, namespace=record_namespace)
                except Exception:
                    pass
                _mark_queued(record, detail or "Waiting for available resources.")
                mapped = "queued"
                feedback[record.id] = (
                    "queued",
                    record.queue_reason or "Queued for retry when resources are available.",
                )
                access_map[record.id] = None
                session.add(record)
                changed = True
                active_count = max(0, active_count - 1)

        if mapped != record.status:
            record.status = mapped
            record.last_active_at = utc_now()
            session.add(record)
            changed = True

        if tmpl and record.status in {"stopped", "completed"}:
            cutoff = utc_now() - timedelta(
                minutes=_namespace_container_auto_delete_minutes(session, selected_namespace, tmpl)
            )
            if record.last_active_at < cutoff:
                try:
                    runtime_kube.delete_container_pod(record.id, record.owner, namespace=record_namespace)
                    runtime_kube.delete_container_service(record.id, namespace=record_namespace)
                except Exception:
                    pass
                to_delete.append(record)

    if changed:
        session.commit()
    if to_delete:
        for row in to_delete:
            session.delete(row)
        session.commit()
        instances = session.exec(
            select(ContainerInstanceTable)
            .where(ContainerInstanceTable.owner == user.username)
            .where(ContainerInstanceTable.namespace == selected_namespace)
        ).all()

    out: list[ContainerInstanceView] = []
    for row in instances:
        stage, detail = feedback.get(row.id, _status_feedback(row.status, None))
        out.append(
            _instance_out(
                row,
                stage=stage,
                detail=detail,
                access_url=access_map.get(row.id),
                container_port=port_map.get(row.id),
                launch_diagnostics=diagnostics_map.get(row.id, []),
            )
        )
    return out


@router.post(
    "/container-templates/{template_id}/start",
    response_model=ContainerInstanceView,
    status_code=status.HTTP_201_CREATED,
)
def start_container_template(
    template_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    selected_namespace = _resolve_selected_namespace(session, user, request)
    runtime_namespace = _container_runtime_namespace(user, namespace=selected_namespace)
    user_tenant = normalize_tenant(getattr(user, "team", None), default="default")
    template = session.get(ContainerTemplateTable, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    template_namespace = _container_template_namespace(template)
    if not _container_template_enabled_for_namespace(template, selected_namespace):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    template_tenant = normalize_tenant(getattr(template, "tenant", None), default=GLOBAL_TENANT)
    if template_tenant not in {user_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    image = session.get(ContainerImageTable, template.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image missing for template")
    image_namespace = _container_image_namespace(image)
    if image_namespace != template_namespace and not _container_image_shared_catalog(image):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="container template image namespace scope is invalid"
        )
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {template_tenant, GLOBAL_TENANT}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="container template image tenant scope is invalid"
        )
    if not lock_user_launch_slot(session, user.username):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    config = session.get(Config, 1) or Config()
    user_vm_instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    user_container_instances = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)
    ).all()
    for row in [*user_vm_instances, *user_container_instances]:
        if row.status not in {"stopped", "completed", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=SINGLE_LAB_LIMIT_MESSAGE,
            )

    total_vm_active = session.exec(select(Instance).where(Instance.status.in_(["pending", "running"]))).all()
    total_container_active = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.status.in_(["pending", "running"]))
    ).all()
    cluster_full = len(total_vm_active) + len(total_container_active) >= int(config.max_concurrent_vms)
    namespace_policy = get_namespace_runtime_policy(session, selected_namespace)
    template_limit = _template_limit(template)
    template_active_count = _active_container_template_count(session, template.id)
    template_limit_reached = template_limit and template_active_count >= template_limit
    headroom_error = _container_headroom_error(template)
    quota_check = enforce_team_quota(
        session,
        team="default",
        namespace=runtime_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(getattr(template, "cpu_millicores", 500) or 500)),
        requested_memory_mb=max(1, int(getattr(template, "memory_mb", 512) or 512)),
        requested_storage_gib=max(1, int(getattr(template, "storage_gib", 1) or 1)),
        requested_idle_timeout_minutes=_namespace_container_idle_timeout_minutes(session, selected_namespace, template),
    )
    try:
        placement = select_cluster_for_launch(
            session,
            team="default",
            workload_kind="container",
            template_cluster_id=str(getattr(template, "cluster_id", "") or ""),
        )
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    selected_cluster_id = str(placement.cluster_id or local_cluster_id()).strip() or local_cluster_id()
    runtime_kube = _kube_for_container_cluster(session, selected_cluster_id)
    try:
        ensure_team_runtime_namespace(runtime_kube, team="default", namespace=runtime_namespace)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    node_admission_ok, node_admission_detail = evaluate_node_launch_admission(runtime_kube)
    if not node_admission_ok:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=node_admission_detail)

    instance_id = str(uuid4())
    now = utc_now()
    namespace_queue_depth = _queued_container_count_for_namespace(session, runtime_namespace)
    queue_limit_reached = namespace_queue_depth >= int(namespace_policy.queue_max_pending)
    queue_reason: str | None = None
    if template_limit_reached:
        queue_reason = f"Template concurrency limit reached ({template_limit})."
    elif quota_check.error_detail:
        queue_reason = quota_check.error_detail
    elif headroom_error:
        queue_reason = headroom_error
    elif cluster_full:
        queue_reason = "Cluster concurrency limit reached. Waiting for available resources."

    if queue_reason and settings.container_start_queue_enabled:
        if queue_limit_reached:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"namespace queue limit reached ({namespace_policy.queue_max_pending}); "
                    "wait for queued launches to drain"
                ),
            )
        record = ContainerInstanceTable(
            id=instance_id,
            template_id=template.id,
            owner=user.username,
            tenant=user_tenant,
            namespace=runtime_namespace,
            cluster_id=selected_cluster_id,
            status="queued",
            pod_name=runtime_kube.container_pod_name(instance_id=instance_id, owner=user.username),
            queue_attempts=0,
            queue_reason=_humanize_queue_reason(queue_reason),
            queue_not_before=now + timedelta(seconds=_queue_backoff_seconds(0)),
            started_at=now,
            last_active_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return _instance_out(
            record,
            stage="queued",
            detail=record.queue_reason,
            access_url=None,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            launch_diagnostics=[],
        )
    if template_limit_reached:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"template concurrency limit reached ({template_limit})",
        )
    if quota_check.error_detail:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=quota_check.error_detail)
    if headroom_error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=headroom_error)
    if cluster_full:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")

    pod_name = runtime_kube.container_pod_name(instance_id=instance_id, owner=user.username)
    try:
        pod_status, access_url, container_port = _create_container_runtime(
            instance_id=instance_id,
            owner=user.username,
            team="default",
            namespace=runtime_namespace,
            runtime_kube=runtime_kube,
            template=template,
            image_ref=image.image_ref,
        )
        record = ContainerInstanceTable(
            id=instance_id,
            template_id=template.id,
            owner=user.username,
            tenant=user_tenant,
            namespace=runtime_namespace,
            cluster_id=selected_cluster_id,
            status="pending",
            pod_name=pod_name,
            started_at=now,
            last_active_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        stage, detail = _status_feedback(record.status, pod_status)
        diagnostics = runtime_kube.get_container_launch_diagnostics(
            record.id,
            record.owner,
            namespace=runtime_namespace,
        )
        return _instance_out(
            record,
            stage=stage,
            detail=detail,
            access_url=access_url,
            container_port=container_port,
            launch_diagnostics=diagnostics,
        )
    except Exception as exc:
        if settings.container_start_queue_enabled:
            queue_reason = _humanize_queue_reason(str(exc))
            record = ContainerInstanceTable(
                id=instance_id,
                template_id=template.id,
                owner=user.username,
                tenant=user_tenant,
                namespace=runtime_namespace,
                cluster_id=selected_cluster_id,
                status="queued",
                pod_name=pod_name,
                queue_attempts=1,
                queue_reason=queue_reason[:255],
                queue_not_before=now + timedelta(seconds=_queue_backoff_seconds(1)),
                started_at=now,
                last_active_at=now,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return _instance_out(
                record,
                stage="queued",
                detail=record.queue_reason,
                access_url=None,
                container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
                launch_diagnostics=[],
            )
        raise


@router.post("/containers/{instance_id}/stop", response_model=ContainerInstanceView)
def stop_container(
    instance_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    selected_namespace = _resolve_selected_namespace(session, user, request)
    instance_namespace = _container_instance_namespace(record, user)
    if normalize_namespace(instance_namespace) != selected_namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    runtime_kube = _kube_for_container_cluster(session, _container_instance_cluster_id(record))

    if record.status != "queued":
        runtime_kube.stop_container_pod(instance_id, user.username, namespace=instance_namespace)
        try:
            runtime_kube.delete_container_service(instance_id, namespace=instance_namespace)
        except Exception:
            pass
    record.status = "stopped"
    record.queue_not_before = None
    record.queue_reason = None
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    template = session.get(ContainerTemplateTable, record.template_id)
    container_port = max(1, int(getattr(template, "container_port", 80) or 80)) if template else None
    return _instance_out(record, container_port=container_port)


@router.post("/containers/{instance_id}/start", response_model=ContainerInstanceView, status_code=status.HTTP_200_OK)
def restart_container(
    instance_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ContainerInstanceView:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    selected_namespace = _resolve_selected_namespace(session, user, request)
    runtime_namespace = _container_instance_namespace(record, user)
    if normalize_namespace(runtime_namespace) != selected_namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    user_tenant = normalize_tenant(getattr(user, "team", None), default="default")
    record.tenant = user_tenant
    record.namespace = runtime_namespace

    template = session.get(ContainerTemplateTable, record.template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    template_namespace = _container_template_namespace(template)
    if not _container_template_enabled_for_namespace(template, selected_namespace):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    template_tenant = normalize_tenant(getattr(template, "tenant", None), default=GLOBAL_TENANT)
    if template_tenant not in {user_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container template not found or disabled")
    image = session.get(ContainerImageTable, template.container_image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container image missing for template")
    image_namespace = _container_image_namespace(image)
    if image_namespace != template_namespace and not _container_image_shared_catalog(image):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="container template image namespace scope is invalid"
        )
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {template_tenant, GLOBAL_TENANT}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="container template image tenant scope is invalid"
        )
    config = session.get(Config, 1) or Config()
    namespace_policy = get_namespace_runtime_policy(session, selected_namespace)
    active_count = _active_workload_count(session)
    is_already_active = str(record.status or "").lower() in {"queued", "pending", "running"}
    cluster_full = (not is_already_active) and active_count >= int(config.max_concurrent_vms)

    template_limit = _template_limit(template)
    template_active_count = _active_container_template_count(
        session,
        template.id,
        exclude_instance_id=record.id,
    )
    if template_limit and template_active_count >= template_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"template concurrency limit reached ({template_limit})",
        )
    if cluster_full and settings.container_start_queue_enabled:
        if _queued_container_count_for_namespace(session, runtime_namespace) >= int(namespace_policy.queue_max_pending):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"namespace queue limit reached ({namespace_policy.queue_max_pending}); "
                    "wait for queued launches to drain"
                ),
            )
        record.status = "queued"
        record.queue_attempts = max(0, int(getattr(record, "queue_attempts", 0) or 0))
        record.queue_reason = _humanize_queue_reason(
            "Cluster concurrency limit reached. Waiting for available resources."
        )
        record.queue_not_before = utc_now() + timedelta(seconds=_queue_backoff_seconds(record.queue_attempts))
        record.last_active_at = utc_now()
        session.add(record)
        session.commit()
        session.refresh(record)
        return _instance_out(
            record,
            stage="queued",
            detail=record.queue_reason,
            access_url=None,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            launch_diagnostics=[],
        )
    if cluster_full:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")
    quota_check = enforce_team_quota(
        session,
        team="default",
        namespace=runtime_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(getattr(template, "cpu_millicores", 500) or 500)),
        requested_memory_mb=max(1, int(getattr(template, "memory_mb", 512) or 512)),
        requested_storage_gib=max(1, int(getattr(template, "storage_gib", 1) or 1)),
        requested_idle_timeout_minutes=_namespace_container_idle_timeout_minutes(session, selected_namespace, template),
        exclude_container_instance_id=record.id,
    )
    try:
        placement = select_cluster_for_launch(
            session,
            team="default",
            workload_kind="container",
            template_cluster_id=str(getattr(template, "cluster_id", "") or ""),
        )
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    selected_cluster_id = str(placement.cluster_id or local_cluster_id()).strip() or local_cluster_id()
    runtime_kube = _kube_for_container_cluster(session, selected_cluster_id)

    if quota_check.error_detail and settings.container_start_queue_enabled:
        if _queued_container_count_for_namespace(session, runtime_namespace) >= int(namespace_policy.queue_max_pending):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"namespace queue limit reached ({namespace_policy.queue_max_pending}); "
                    "wait for queued launches to drain"
                ),
            )
        record.status = "queued"
        record.queue_attempts = max(0, int(getattr(record, "queue_attempts", 0) or 0))
        record.queue_reason = quota_check.error_detail[:255]
        record.queue_not_before = utc_now() + timedelta(seconds=_queue_backoff_seconds(record.queue_attempts))
        record.last_active_at = utc_now()
        session.add(record)
        session.commit()
        session.refresh(record)
        return _instance_out(
            record,
            stage="queued",
            detail=record.queue_reason,
            access_url=None,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            launch_diagnostics=[],
        )
    if quota_check.error_detail:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=quota_check.error_detail)
    headroom_error = _container_headroom_error(template)
    if headroom_error and settings.container_start_queue_enabled:
        if _queued_container_count_for_namespace(session, runtime_namespace) >= int(namespace_policy.queue_max_pending):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"namespace queue limit reached ({namespace_policy.queue_max_pending}); "
                    "wait for queued launches to drain"
                ),
            )
        record.status = "queued"
        record.queue_attempts = max(0, int(getattr(record, "queue_attempts", 0) or 0))
        record.queue_reason = _humanize_queue_reason(headroom_error)
        record.queue_not_before = utc_now() + timedelta(seconds=_queue_backoff_seconds(record.queue_attempts))
        record.last_active_at = utc_now()
        session.add(record)
        session.commit()
        session.refresh(record)
        return _instance_out(
            record,
            stage="queued",
            detail=record.queue_reason,
            access_url=None,
            container_port=max(1, int(getattr(template, "container_port", 80) or 80)),
            launch_diagnostics=[],
        )
    if headroom_error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=headroom_error)

    try:
        runtime_kube.delete_container_pod(instance_id, user.username, namespace=runtime_namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise

    pod_status, access_url, container_port = _create_container_runtime(
        instance_id=record.id,
        owner=user.username,
        team="default",
        namespace=runtime_namespace,
        runtime_kube=runtime_kube,
        template=template,
        image_ref=image.image_ref,
    )

    record.status = "pending"
    record.tenant = user_tenant
    record.namespace = runtime_namespace
    record.cluster_id = selected_cluster_id
    record.pod_name = runtime_kube.container_pod_name(instance_id=record.id, owner=user.username)
    record.queue_attempts = 0
    record.queue_not_before = None
    record.queue_reason = None
    record.started_at = utc_now()
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, pod_status)
    diagnostics = runtime_kube.get_container_launch_diagnostics(record.id, record.owner, namespace=runtime_namespace)
    return _instance_out(
        record,
        stage=stage,
        detail=detail,
        access_url=access_url,
        container_port=container_port,
        launch_diagnostics=diagnostics,
    )


@router.post("/containers/{instance_id}/activity", status_code=status.HTTP_204_NO_CONTENT)
def record_container_activity(
    instance_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    if record.status not in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="container is not running")
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()


@router.delete("/containers/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_container(
    instance_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    record = session.get(ContainerInstanceTable, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    selected_namespace = _resolve_selected_namespace(session, user, request)
    instance_namespace = _container_instance_namespace(record, user)
    if normalize_namespace(instance_namespace) != selected_namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="container instance not found")
    runtime_kube = _kube_for_container_cluster(session, _container_instance_cluster_id(record))

    runtime_kube.delete_container_pod(instance_id, user.username, namespace=instance_namespace)
    try:
        runtime_kube.delete_container_service(instance_id, namespace=instance_namespace)
    except Exception:
        pass
    session.delete(record)
    session.commit()
