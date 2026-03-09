import asyncio
import json
import logging
import math
import ssl
from datetime import timedelta
from pathlib import Path
import secrets
import warnings
from uuid import uuid4
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import FileResponse, RedirectResponse
from kubernetes.client import ApiException
from sqlmodel import Session, select
from urllib3.exceptions import InsecureRequestWarning

from ..auth import consume_connect_grant, issue_connect_token, require_user, validate_connect_session
from ..config import settings
from ..db import get_session
from ..models import SiteSettings, SSOSettings, VMInstance, VMTemplate
from ..network_modes import normalize_vm_network_mode
from ..services.launch_lock import lock_user_launch_slot
from ..services.kubernetes import PodRequest, PodStatus, kube
from ..services.resource_guard import check_launch_headroom
from ..services.team_quotas import enforce_team_quota_or_raise, team_idle_timeout_cap
from ..tables import Config, ContainerInstance as ContainerInstanceTable, Image, Instance, Template, User
from ..time_utils import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)
SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."
_VM_CONNECT_GRANT_COOKIE_NAME = "blabs_connect_grant"
_VM_CONNECT_SESSION_COOKIE_NAME = "blabs_connect_session"
_VM_PROXY_TIMEOUT_SECONDS = 45
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


def _public_scheme() -> str:
    scheme = (settings.public_scheme or "https").strip().lower()
    return scheme if scheme in {"http", "https"} else "https"


def _generate_spice_password() -> str:
    length = max(12, min(64, int(getattr(settings, "vm_console_ticket_length", 24) or 24)))
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _phase_to_instance_status(phase: str) -> str:
    return {
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _status_feedback(status: str, pod_status: PodStatus | None) -> tuple[str, str]:
    normalized = (status or "unknown").lower()
    if normalized == "running":
        if pod_status and not pod_status.ready:
            detail = (pod_status.waiting_message or pod_status.message or "").strip()
            return "starting", detail or "VM process started; waiting for readiness."
        return "running", "VM is running."
    if normalized == "pending":
        if not pod_status:
            return "pending", "Scheduling VM pod."
        reason_text = " ".join(
            [
                (pod_status.waiting_reason or "").lower(),
                (pod_status.reason or "").lower(),
            ]
        )
        detail = (pod_status.waiting_message or pod_status.message or "").strip()
        if "unschedulable" in reason_text or "failedscheduling" in reason_text:
            return "pending", detail or "Waiting for available node resources."
        build_reason_keywords = (
            "containercreating",
            "podinitializing",
            "createcontainer",
            "pulling",
            "errimagepull",
            "imagepullbackoff",
            "mountvolume",
            "attachvolume",
        )
        build_detail_keywords = (
            "persistentvolumeclaim",
            "volume",
            "mount",
            "attach",
            "pulling image",
            "creating container",
            "initializing",
        )
        detail_text = detail.lower()
        if any(token in reason_text for token in build_reason_keywords) or any(
            token in detail_text for token in build_detail_keywords
        ):
            return "building", detail or "Preparing VM disk and container."
        return "pending", detail or "Waiting in scheduler queue."
    if normalized == "completed":
        return "completed", "VM completed and stopped."
    if normalized == "stopped":
        return "stopped", "VM is stopped."
    if normalized == "failed":
        return "failed", "VM failed to start or run."
    return "unknown", "VM status is unknown."


def _require_clone_ready(image: Image) -> None:
    if not settings.kube_vm_storage_class:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="clone-based VM launch is required; configure BLABS_KUBE_VM_STORAGE_CLASS",
        )
    if not image.source_pvc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="image is not prepared for clone-based storage; re-import the image from admin",
        )


def _vm_storage_request_gib(image: Image | None) -> int:
    size_bytes = max(0, int(getattr(image, "size_bytes", 0) or 0))
    if size_bytes <= 0:
        return 20
    return max(1, int(math.ceil(size_bytes / float(1024**3))))


def _public_console_host() -> str:
    return (settings.kube_node_external_host or "").strip() or "127.0.0.1"


def _public_api_base() -> str:
    return f"{_public_scheme()}://{_public_console_host()}:30080"


def _connect_cookie_samesite() -> str:
    raw = str(getattr(settings, "connect_cookie_samesite", "lax") or "lax").strip().lower()
    if raw not in {"lax", "strict", "none"}:
        return "lax"
    return raw


def _connect_cookie_secure(request: Request) -> bool:
    configured_secure = bool(getattr(settings, "connect_cookie_secure", True))
    if configured_secure:
        return True
    return request.url.scheme == "https"


def _extract_vm_connect_grant_token(request: Request) -> str:
    cookie_value = request.cookies.get(_VM_CONNECT_GRANT_COOKIE_NAME)
    if cookie_value:
        return cookie_value
    for key in ("vt", "connect_token"):
        value = str(request.query_params.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_vm_connect_session_token(request: Request) -> str:
    cookie_value = request.cookies.get(_VM_CONNECT_SESSION_COOKIE_NAME)
    if cookie_value:
        return cookie_value
    auth_header = str(request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _extract_vm_connect_session_token_ws(websocket: WebSocket) -> str:
    cookie_value = websocket.cookies.get(_VM_CONNECT_SESSION_COOKIE_NAME)
    if cookie_value:
        return cookie_value
    for key in ("cs", "ct", "connect_token"):
        value = str(websocket.query_params.get(key) or "").strip()
        if value:
            return value
    auth_header = str(websocket.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _attach_vm_connect_session_cookie(response: Response, request: Request, instance_id: str, token_value: str) -> None:
    base_path = f"/user/pods/{instance_id}/connect/"
    response.set_cookie(
        key=_VM_CONNECT_SESSION_COOKIE_NAME,
        value=token_value,
        max_age=max(60, int(settings.connect_session_ttl_seconds or 3600)),
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
        path=base_path,
    )
    response.delete_cookie(
        key=_VM_CONNECT_GRANT_COOKIE_NAME,
        path=base_path,
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
    )


def _vm_service_host(instance_id: str) -> str:
    return f"svc-{instance_id[:8]}.{settings.kube_namespace}.svc.cluster.local"


def _extract_spice_password(console_url: str | None) -> str:
    raw = str(console_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    query_password = parse_qs(parsed.query or "").get("password", [])
    if query_password:
        return query_password[0]
    fragment_password = parse_qs(parsed.fragment or "").get("password", [])
    if fragment_password:
        return fragment_password[0]
    return ""


def _vm_console_embed_url(instance_id: str, title: str, idle_minutes: int, spice_password: str) -> str:
    base = _public_api_base()
    host = _public_console_host()
    secure_param = 1 if _public_scheme() == "https" else 0
    ws_path = f"/user/pods/{instance_id}/connect/websockify"
    query = urlencode(
        {
            "host": host,
            "port": "30080",
            "secure": str(secure_param),
            "title": title,
            "instance_id": instance_id,
            "idle_minutes": str(max(1, int(idle_minutes))),
            "ws_path": ws_path,
            "password": spice_password,
        }
    )
    return f"{base}/user/pods/{instance_id}/connect/spice-embed.html?{query}#password={quote(spice_password, safe='')}"


def _upstream_requires_https(response: requests.Response) -> bool:
    if response.status_code not in {400, 403, 426, 495, 496, 497}:
        return False
    message = (response.text or "").lower()
    return "https" in message or "tls" in message or "ssl" in message or "plain http request" in message


def _vm_ws_schemes() -> tuple[str, str]:
    if _public_scheme() == "https":
        return ("wss", "ws")
    return ("ws", "wss")


def _vm_http_schemes() -> tuple[str, str]:
    if _public_scheme() == "https":
        return ("https", "http")
    return ("http", "https")


def _tls_client_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.get("/templates", response_model=list[VMTemplate])
def list_available_templates(user: User = Depends(require_user), session: Session = Depends(get_session)) -> list[VMTemplate]:
    team_idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), settings.kube_namespace)
    templates = session.exec(select(Template).where(Template.enabled == True)).all()  # noqa: E712
    return [
        VMTemplate(
            id=record.id,
            name=record.name,
            description=record.description,
            os_type=record.os_type,
            image_id=record.image_id,
            cpu_cores=record.cpu_cores,
            ram_mb=record.ram_mb,
            auto_delete_minutes=record.auto_delete_minutes,
            idle_timeout_minutes=min(
                max(1, int(getattr(record, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes)),
                team_idle_cap if team_idle_cap is not None else 1440,
            ),
            preclone_pool_size=getattr(record, "preclone_pool_size", 0),
            preclone_pool_max=getattr(record, "preclone_pool_max", getattr(record, "preclone_pool_size", 0)),
            max_active_instances=max(0, int(getattr(record, "max_active_instances", 2) or 0)),
            enabled=record.enabled,
            network_mode=normalize_vm_network_mode(getattr(record, "network_mode", "bridge")),
            created_at=record.created_at,
        )
        for record in templates
    ]


@router.get("/pods", response_model=list[VMInstance])
def list_user_pods(user: User = Depends(require_user), session: Session = Depends(get_session)) -> list[VMInstance]:
    instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    templates = {t.id: t for t in session.exec(select(Template)).all()}
    changed = False
    to_delete: list[Instance] = []
    feedback: dict[str, tuple[str, str]] = {}
    for record in instances:
        # Treat every poll from the user as activity so the idle reaper doesn't reclaim a live VM.
        if record.status in {"running", "pending"}:
            record.last_active_at = utc_now()
            session.add(record)
            changed = True
        pod_status: PodStatus | None = None
        try:
            pod_status = kube.get_status(record.id, record.owner)
            mapped = _phase_to_instance_status(pod_status.phase)
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise
        feedback[record.id] = _status_feedback(mapped, pod_status)
        if mapped != record.status:
            record.status = mapped
            record.last_active_at = utc_now()
            session.add(record)
            changed = True
        # Auto-delete stopped/completed instances based on template setting.
        tmpl = templates.get(record.template_id)
        if tmpl and record.status in {"stopped", "completed"}:
            cutoff = utc_now() - timedelta(minutes=tmpl.auto_delete_minutes)
            if record.last_active_at < cutoff:
                try:
                    kube.delete_pod(record.id, record.owner, disk_pvc=record.disk_pvc)
                except Exception:
                    pass
                to_delete.append(record)
    if changed:
        session.commit()
    if to_delete:
        for rec in to_delete:
            session.delete(rec)
        session.commit()
        # refresh instances list without deleted ones
        instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()

    items: list[VMInstance] = []
    for record in instances:
        stage, detail = feedback.get(record.id, _status_feedback(record.status, None))
        items.append(
            VMInstance(
                id=record.id,
                template_id=record.template_id,
                owner=record.owner,
                status=record.status,
                status_stage=stage,
                status_detail=detail,
                started_at=record.started_at,
                last_active_at=record.last_active_at,
                console_url=record.console_url,
            )
        )
    return items


@router.post("/pods/{instance_id}/activity", status_code=status.HTTP_204_NO_CONTENT)
def record_vm_activity(
    instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> None:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()


@router.post("/pods/{instance_id}/connect-token")
def issue_vm_connect_token(
    instance_id: str,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    if record.status not in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="instance is not running")
    template = session.get(Template, record.template_id)
    idle_minutes = max(1, int(getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes))
    idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), settings.kube_namespace)
    if idle_cap is not None:
        idle_minutes = min(idle_minutes, idle_cap)
    spice_password = _extract_spice_password(record.console_url)
    if not spice_password:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="console credentials are not available")
    connect_url = _vm_console_embed_url(
        instance_id=record.id,
        title=(template.name if template else "VM"),
        idle_minutes=idle_minutes,
        spice_password=spice_password,
    )
    grant_token = issue_connect_token(
        session,
        username=user.username,
        instance_id=record.id,
        resource_type="vm",
        token_type="grant",
        ttl_seconds=max(15, int(settings.connect_grant_ttl_seconds or 120)),
    )
    response = Response(
        content=json.dumps({"connect_url": connect_url}),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    response.set_cookie(
        key=_VM_CONNECT_GRANT_COOKIE_NAME,
        value=grant_token,
        max_age=max(15, int(settings.connect_grant_ttl_seconds or 120)),
        httponly=True,
        samesite=_connect_cookie_samesite(),
        secure=_connect_cookie_secure(request),
        path=f"/user/pods/{record.id}/connect/",
    )
    return response


@router.api_route(
    "/pods/{instance_id}/connect",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
    operation_id="vm_connect_proxy_root",
)
@router.api_route(
    "/pods/{instance_id}/connect/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
    operation_id="vm_connect_proxy_path",
)
async def proxy_vm_console(
    instance_id: str,
    request: Request,
    proxy_path: str = "",
    session: Session = Depends(get_session),
) -> Response:
    base_path = f"/user/pods/{instance_id}/connect"
    if request.url.path == base_path:
        query = str(request.url.query or "").strip()
        target = f"{base_path}/"
        if query:
            target = f"{target}?{query}"
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    issued_connect_session = ""
    user: User | None = None
    connect_session_token = _extract_vm_connect_session_token(request)
    if connect_session_token:
        try:
            user = validate_connect_session(
                session,
                token_value=connect_session_token,
                instance_id=instance_id,
                resource_type="vm",
            )
        except HTTPException:
            user = None
    if user is None:
        grant_token = _extract_vm_connect_grant_token(request)
        if not grant_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing connect token")
        user = consume_connect_grant(
            session,
            token_value=grant_token,
            instance_id=instance_id,
            resource_type="vm",
        )
        issued_connect_session = issue_connect_token(
            session,
            username=user.username,
            instance_id=instance_id,
            resource_type="vm",
            token_type="session",
            ttl_seconds=max(60, int(settings.connect_session_ttl_seconds or 3600)),
        )

    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    if record.status not in {"pending", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="instance is not running")
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()

    normalized_path = proxy_path.strip("/")
    if not normalized_path:
        template = session.get(Template, record.template_id)
        idle_minutes = max(
            1,
            int(getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes),
        )
        idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), settings.kube_namespace)
        if idle_cap is not None:
            idle_minutes = min(idle_minutes, idle_cap)
        spice_password = _extract_spice_password(record.console_url)
        embed_url = _vm_console_embed_url(
            instance_id=record.id,
            title=(template.name if template else "VM"),
            idle_minutes=idle_minutes,
            spice_password=spice_password,
        )
        response = RedirectResponse(url=embed_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        if issued_connect_session:
            _attach_vm_connect_session_cookie(response, request, instance_id, issued_connect_session)
        return response

    upstream_host = _vm_service_host(instance_id)
    upstream_path = f"/{normalized_path}"
    query_items = [(key, value) for key, value in request.query_params.multi_items() if key not in {"vt", "connect_token"}]
    upstream_query = urlencode(query_items, doseq=True)

    forwarded_headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered in {"host", "authorization", "cookie", "content-length", "accept-encoding"}:
            continue
        forwarded_headers[key] = value
    forwarded_headers["X-Forwarded-Proto"] = "https"
    forwarded_headers["X-Forwarded-Host"] = str(request.headers.get("host") or "")
    forwarded_headers["X-Forwarded-Prefix"] = f"/user/pods/{instance_id}/connect"

    body = await request.body()
    upstream: requests.Response | None = None
    attempted_urls: list[str] = []
    last_exc: requests.RequestException | None = None
    for scheme in _vm_http_schemes():
        upstream_url = f"{scheme}://{upstream_host}:6080{upstream_path}"
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
                    timeout=_VM_PROXY_TIMEOUT_SECONDS,
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
        if last_exc is not None:
            detail = f"vm console proxy failed: {last_exc}"
        else:
            detail = f"vm console proxy failed: unable to reach upstream ({', '.join(attempted_urls)})"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    response_headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered == "content-length":
            continue
        response_headers[key] = value
    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
    if issued_connect_session:
        _attach_vm_connect_session_cookie(response, request, instance_id, issued_connect_session)
    return response


@router.websocket("/pods/{instance_id}/connect/{proxy_path:path}")
async def proxy_vm_console_ws(
    websocket: WebSocket,
    instance_id: str,
    proxy_path: str,
    session: Session = Depends(get_session),
) -> None:
    token_value = _extract_vm_connect_session_token_ws(websocket)
    if not token_value:
        await websocket.close(code=4401, reason="missing connect token")
        return
    try:
        user = validate_connect_session(
            session,
            token_value=token_value,
            instance_id=instance_id,
            resource_type="vm",
        )
    except HTTPException as exc:
        await websocket.close(code=4401, reason=str(exc.detail))
        return
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        await websocket.close(code=4404, reason="instance not found")
        return
    if record.status not in {"pending", "running"}:
        await websocket.close(code=4409, reason="instance is not running")
        return
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    await websocket.accept()

    upstream_host = _vm_service_host(instance_id)
    upstream_path = "/" + proxy_path.lstrip("/")
    query_items = [(key, value) for key, value in websocket.query_params.multi_items() if key not in {"cs", "ct", "connect_token"}]
    upstream_query = urlencode(query_items, doseq=True)

    forwarded_headers: dict[str, str] = {}
    for key, value in websocket.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered in {"host", "authorization", "cookie"}:
            continue
        forwarded_headers[key] = value
    forwarded_headers["X-Forwarded-Proto"] = "https"
    forwarded_headers["X-Forwarded-Host"] = str(websocket.headers.get("host") or "")
    forwarded_headers["X-Forwarded-Prefix"] = f"/user/pods/{instance_id}/connect"
    upstream_ws_headers = [(key, value) for key, value in forwarded_headers.items()]

    attempted_urls: list[str] = []
    last_exc: Exception | None = None
    for ws_scheme in _vm_ws_schemes():
        upstream_url = f"{ws_scheme}://{upstream_host}:6080{upstream_path}"
        if upstream_query:
            upstream_url = f"{upstream_url}?{upstream_query}"
        attempted_urls.append(upstream_url)
        ssl_context = _tls_client_context() if ws_scheme == "wss" else None
        try:
            async with websockets.connect(
                upstream_url,
                additional_headers=upstream_ws_headers,
                origin=None,
                open_timeout=15,
                close_timeout=5,
                max_size=None,
                ssl=ssl_context,
            ) as upstream:
                async def client_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        kind = message.get("type")
                        if kind == "websocket.disconnect":
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
                        payload = await upstream.recv()
                        if isinstance(payload, bytes):
                            await websocket.send_bytes(payload)
                        else:
                            await websocket.send_text(payload)

                task_client = asyncio.create_task(client_to_upstream())
                task_upstream = asyncio.create_task(upstream_to_client())
                done, pending = await asyncio.wait({task_client, task_upstream}, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.gather(*done, return_exceptions=True)
                return
        except Exception as exc:
            last_exc = exc
            continue

    exc_info = (type(last_exc), last_exc, last_exc.__traceback__) if last_exc else None
    logger.warning(
        "VM websocket proxy failed for instance %s path %s attempted=%s",
        instance_id,
        proxy_path,
        ", ".join(attempted_urls),
        exc_info=exc_info,
    )
    try:
        await websocket.close(code=1011, reason="upstream websocket error")
    except Exception:
        pass


@router.get("/settings/site", response_model=SiteSettings)
def site_settings(session: Session = Depends(get_session)) -> SiteSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return SiteSettings(
        site_title=cfg.site_title,
        site_tagline=cfg.site_tagline,
        theme_bg_color=cfg.theme_bg_color,
        theme_text_color=cfg.theme_text_color,
        theme_button_color=cfg.theme_button_color,
        theme_button_text_color=cfg.theme_button_text_color,
        theme_bg_image=cfg.theme_bg_image,
        theme_bg_image_overlay_opacity=cfg.theme_bg_image_overlay_opacity,
        theme_contrast_body=cfg.theme_contrast_body,
        theme_contrast_button=cfg.theme_contrast_button,
        theme_contrast_tile=cfg.theme_contrast_tile,
        theme_contrast_tile_border=cfg.theme_contrast_tile_border,
        theme_font_family=cfg.theme_font_family,
        theme_font_size_base=cfg.theme_font_size_base,
        theme_font_size_h1=cfg.theme_font_size_h1,
        theme_font_size_h2=cfg.theme_font_size_h2,
        theme_tile_bg=cfg.theme_tile_bg,
        theme_tile_border=cfg.theme_tile_border,
        theme_tile_opacity=cfg.theme_tile_opacity,
        theme_tile_border_opacity=cfg.theme_tile_border_opacity,
    )


@router.get("/site-assets/{filename}")
def get_site_asset(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    asset_path = Path(settings.site_assets_dir) / safe_name
    if not asset_path.exists() or not asset_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return FileResponse(
        path=asset_path,
        headers={"cache-control": "public, max-age=300"},
    )


@router.get("/settings/sso", response_model=SSOSettings)
def sso_settings(session: Session = Depends(get_session)) -> SSOSettings:
    cfg = session.get(Config, 1) or Config(id=1)
    session.add(cfg)
    session.commit()
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret="",
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
    )


@router.post("/templates/{template_id}/start", response_model=VMInstance, status_code=status.HTTP_201_CREATED)
def start_vm(
    template_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> VMInstance:
    template = session.get(Template, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    _require_clone_ready(image)
    if not lock_user_launch_slot(session, user.username):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    config = session.get(Config, 1) or Config()
    total_running = session.exec(select(Instance).where(Instance.status == "running")).all()
    user_vm_instances = session.exec(select(Instance).where(Instance.owner == user.username)).all()
    user_container_instances = session.exec(
        select(ContainerInstanceTable).where(ContainerInstanceTable.owner == user.username)
    ).all()
    # Block if any of the user's labs are not stopped/completed/failed.
    for inst in [*user_vm_instances, *user_container_instances]:
        if inst.status not in {"stopped", "completed", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=SINGLE_LAB_LIMIT_MESSAGE,
            )
    if len(total_running) >= config.max_concurrent_vms:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="cluster concurrency limit reached")
    template_limit = max(0, int(getattr(template, "max_active_instances", 2) or 0))
    if template_limit:
        template_active = session.exec(
            select(Instance)
            .where(Instance.template_id == template.id)
            .where(Instance.status.in_(["pending", "running"]))
        ).all()
        if len(template_active) >= template_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"template concurrency limit reached ({template_limit})",
            )
    # Enforce per-user limit against any non-stopped labs.
    active_count = sum(
        1 for inst in [*user_vm_instances, *user_container_instances] if inst.status not in {"stopped", "completed", "failed"}
    )
    if active_count >= config.per_user_vm_limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="per-user concurrency limit reached")
    headroom_error = check_launch_headroom(
        request_cpu_m=max(1, int(template.cpu_cores or 1)) * 1000,
        request_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
    )
    if headroom_error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=headroom_error)

    idle_minutes = enforce_team_quota_or_raise(
        session,
        team=getattr(user, "team", None),
        namespace=settings.kube_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(template.cpu_cores or 1)) * 1000,
        requested_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
        requested_storage_gib=_vm_storage_request_gib(image),
        requested_idle_timeout_minutes=template.idle_timeout_minutes or settings.idle_timeout_minutes,
    )

    instance_id = str(uuid4())
    try:
        warm_pool_pvc = kube.reserve_warm_pool_pvc(template.id, instance_id, user.username)
    except Exception:
        warm_pool_pvc = None
    spice_password = _generate_spice_password()
    pod_request = PodRequest(
        instance_id=instance_id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        image_source_pvc=image.source_pvc,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
        network_mode=normalize_vm_network_mode(getattr(template, "network_mode", "bridge")),
        instance_disk_pvc=warm_pool_pvc,
        spice_password=spice_password,
    )
    try:
        pod_status = kube.create_pod(pod_request)
    except Exception:
        if warm_pool_pvc:
            try:
                kube._client().delete_namespaced_persistent_volume_claim(
                    name=warm_pool_pvc,
                    namespace=settings.kube_namespace,
                )
            except Exception:
                pass
        raise
    # Keep VM console service internal; browser access goes through authenticated backend proxy.
    service_name = f"svc-{instance_id[:8]}"
    kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name, service_type="ClusterIP")
    console_url = _vm_console_embed_url(
        instance_id=instance_id,
        title=template.name,
        idle_minutes=idle_minutes,
        spice_password=spice_password,
    )

    instance = Instance(
        id=instance_id,
        template_id=template.id,
        owner=user.username,
        status="pending",
        disk_pvc=pod_status.disk_pvc,
        started_at=utc_now(),
        last_active_at=utc_now(),
        console_url=console_url,
    )
    session.add(instance)
    session.commit()
    session.refresh(instance)
    stage, detail = _status_feedback(instance.status, pod_status)
    return VMInstance(
        id=instance.id,
        template_id=instance.template_id,
        owner=instance.owner,
        status=instance.status,
        status_stage=stage,
        status_detail=detail,
        started_at=instance.started_at,
        last_active_at=instance.last_active_at,
        console_url=instance.console_url,
    )


@router.post("/pods/{instance_id}/stop", response_model=VMInstance)
def stop_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.stop_pod(instance_id, record.owner)
    record.status = "stopped"
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, None)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        status_stage=stage,
        status_detail=detail,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.post("/pods/{instance_id}/start", response_model=VMInstance, status_code=status.HTTP_200_OK)
def restart_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    template = session.get(Template, record.template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    _require_clone_ready(image)
    template_limit = max(0, int(getattr(template, "max_active_instances", 2) or 0))
    if template_limit:
        template_active = session.exec(
            select(Instance)
            .where(Instance.template_id == template.id)
            .where(Instance.status.in_(["pending", "running"]))
        ).all()
        if len(template_active) >= template_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"template concurrency limit reached ({template_limit})",
            )
    headroom_error = check_launch_headroom(
        request_cpu_m=max(1, int(template.cpu_cores or 1)) * 1000,
        request_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
    )
    if headroom_error:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=headroom_error)

    idle_minutes = enforce_team_quota_or_raise(
        session,
        team=getattr(user, "team", None),
        namespace=settings.kube_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(template.cpu_cores or 1)) * 1000,
        requested_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
        requested_storage_gib=_vm_storage_request_gib(image),
        requested_idle_timeout_minutes=template.idle_timeout_minutes or settings.idle_timeout_minutes,
        exclude_vm_instance_id=record.id,
    )

    # Ensure any old pod with the same name is removed before re-create.
    try:
        kube.delete_pod(instance_id, user.username, disk_pvc=record.disk_pvc)
    except ApiException as exc:
        if exc.status != 404:
            raise

    try:
        warm_pool_pvc = kube.reserve_warm_pool_pvc(template.id, record.id, user.username)
    except Exception:
        warm_pool_pvc = None
    spice_password = _generate_spice_password()
    pod_request = PodRequest(
        instance_id=record.id,
        template_id=template.id,
        image_path=Path(image.filename).name,
        image_source_pvc=image.source_pvc,
        os_type=template.os_type,
        cpu_cores=template.cpu_cores,
        ram_mb=template.ram_mb,
        owner=user.username,
        network_mode=normalize_vm_network_mode(getattr(template, "network_mode", "bridge")),
        instance_disk_pvc=warm_pool_pvc,
        spice_password=spice_password,
    )
    try:
        pod_status = kube.create_pod(pod_request)
    except Exception:
        if warm_pool_pvc:
            try:
                kube._client().delete_namespaced_persistent_volume_claim(
                    name=warm_pool_pvc,
                    namespace=settings.kube_namespace,
                )
            except Exception:
                pass
        raise
    service_name = f"svc-{instance_id[:8]}"
    kube.create_service_for_pod(pod_name=kube._pod_name(pod_request), service_name=service_name, service_type="ClusterIP")
    console_url = _vm_console_embed_url(
        instance_id=record.id,
        title=template.name,
        idle_minutes=idle_minutes,
        spice_password=spice_password,
    )

    record.status = "pending"
    record.disk_pvc = pod_status.disk_pvc
    record.started_at = utc_now()
    record.last_active_at = utc_now()
    record.console_url = console_url
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, pod_status)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        status=record.status,
        status_stage=stage,
        status_detail=detail,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.delete("/pods/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vm(instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)) -> None:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    kube.delete_pod(instance_id, record.owner, disk_pvc=record.disk_pvc)
    session.delete(record)
    session.commit()
