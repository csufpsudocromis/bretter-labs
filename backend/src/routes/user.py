import asyncio
import json
import logging
import math
import ssl
from datetime import datetime, timedelta
from pathlib import Path
import secrets
import warnings
from uuid import uuid4
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import FileResponse, RedirectResponse
from kubernetes import client as k8s_client
from kubernetes.client import ApiException
from sqlmodel import Session, select
from urllib3.exceptions import InsecureRequestWarning

from ..auth import consume_connect_grant, issue_connect_token, require_user, validate_connect_session
from ..console_providers import normalize_vm_console_provider
from ..config import settings
from ..db import get_session
from ..models import (
    SiteSettings,
    SSOSettings,
    VMInstance,
    VMTemplate,
    VMTemplateLaunchPreflight,
    VMTemplateLaunchPreflightCheck,
)
from ..network_modes import normalize_vm_network_mode
from ..secret_codec import decrypt_secret, secret_is_configured
from ..services.launch_lock import lock_user_launch_slot
from ..services.labinstance_crd import (
    delete_vm_labinstance,
    delete_vm_labinstance_best_effort,
    patch_vm_labinstance_desired_state,
    upsert_vm_labinstance,
    vm_orchestration_uses_legacy_path,
    vm_orchestration_writes_crd,
)
from ..services.kubernetes import PodRequest, PodStatus, kube
from ..services.launch_admission import evaluate_node_launch_admission, evaluate_vm_storage_launch_admission
from ..services.multi_cluster import (
    PlacementError,
    kube_service_for_cluster,
    local_cluster_id,
    select_cluster_for_launch,
)
from ..services.resource_guard import check_launch_headroom
from ..services.team_quotas import enforce_team_quota_or_raise, team_idle_timeout_cap
from ..services.tenant_namespace_bootstrap import ensure_team_runtime_namespace
from ..services.tenant_context import (
    GLOBAL_TENANT,
    normalize_tenant,
    tenant_namespace_for_user,
    vm_launch_requires_privileged_runtime,
    vm_runtime_namespace_for_user,
)
from ..services import ws_metrics
from ..tables import Config, ContainerInstance as ContainerInstanceTable, Image, Instance, Template, User
from ..time_utils import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)
SINGLE_LAB_LIMIT_MESSAGE = "You already have a virtual lab running. Delete the current lab before starting a new one."
_VM_CONNECT_GRANT_COOKIE_NAME = "blabs_connect_grant"
_VM_CONNECT_SESSION_COOKIE_NAME = "blabs_connect_session"
_VM_PROXY_TIMEOUT_SECONDS = 45
_VM_RDP_READY_TIMEOUT_SECONDS = 2
_VM_BUILD_TIMEOUT_MINUTES = 15
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


def _resolve_template_rdp_defaults(template: Template) -> tuple[str | None, str | None]:
    username = str(getattr(template, "rdp_default_username", "") or "").strip()[:128]
    encrypted_password = str(getattr(template, "rdp_default_password", "") or "")
    password = ""
    if encrypted_password:
        try:
            password = decrypt_secret(encrypted_password).strip()
        except Exception as exc:
            logger.error("Failed to decrypt template RDP password for template %s", template.id, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="template RDP credentials are unavailable",
            ) from exc
    return username or None, (password or None)


def _phase_to_instance_status(phase: str) -> str:
    return {
        "pending": "pending",
        "running": "running",
        "succeeded": "completed",
        "failed": "failed",
        "unknown": "unknown",
    }.get((phase or "").lower(), "unknown")


def _elapsed_hint(started_at: datetime | None, *, timeout_minutes: int = _VM_BUILD_TIMEOUT_MINUTES) -> str:
    if started_at is None:
        return ""
    try:
        elapsed_seconds = max(0, int((utc_now() - started_at).total_seconds()))
    except Exception:
        return ""
    elapsed_minutes, rem_seconds = divmod(elapsed_seconds, 60)
    if elapsed_minutes > 0:
        elapsed = f"{elapsed_minutes}m {rem_seconds:02d}s"
    else:
        elapsed = f"{rem_seconds}s"
    return f"Elapsed: {elapsed}."


def _status_feedback(
    status: str, pod_status: PodStatus | None, *, started_at: datetime | None = None
) -> tuple[str, str]:
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
        detail_text = detail.lower()
        if (
            "unbound immediate persistentvolumeclaims" in detail_text
            or "persistentvolumeclaim" in detail_text
            or "attachvolume" in detail_text
            or "mountvolume" in detail_text
        ):
            elapsed_hint = _elapsed_hint(started_at)
            base = detail or "Preparing VM disk and container."
            return "building", f"{base} {elapsed_hint}".strip()
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
        if any(token in reason_text for token in build_reason_keywords) or any(
            token in detail_text for token in build_detail_keywords
        ):
            elapsed_hint = _elapsed_hint(started_at)
            base = detail or "Preparing VM disk and container."
            return "building", f"{base} {elapsed_hint}".strip()
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


def _vm_preflight(
    *,
    runtime_kube,
    runtime_namespace: str,
    privileged_runtime_namespace: bool,
    template: Template,
    image: Image,
    team: str | None,
    include_runner_pull_check: bool = True,
) -> VMTemplateLaunchPreflight:
    checks: list[VMTemplateLaunchPreflightCheck] = []
    blocking_reason = ""

    def add_check(key: str, status_text: str, detail: str) -> None:
        nonlocal blocking_reason
        checks.append(VMTemplateLaunchPreflightCheck(key=key, status=status_text, detail=detail))
        if not blocking_reason and status_text == "error":
            blocking_reason = detail

    try:
        ensure_team_runtime_namespace(
            runtime_kube,
            team=team,
            namespace=runtime_namespace,
            privileged_runtime=privileged_runtime_namespace,
        )
        add_check("namespace", "ok", f"Runtime namespace {runtime_namespace} is ready.")
    except Exception as exc:
        add_check("namespace", "error", str(exc))
        return VMTemplateLaunchPreflight(
            template_id=template.id,
            namespace=runtime_namespace,
            cluster_id=str(getattr(template, "cluster_id", "") or local_cluster_id()),
            ready=False,
            blocking_reason=blocking_reason,
            checks=checks,
        )

    source_pvc_name = str(getattr(image, "source_pvc", "") or "").strip()
    source_storage_class = ""
    if not source_pvc_name:
        add_check(
            "source_pvc",
            "error",
            "Image is not prepared for clone-based launch. Re-import the image from admin.",
        )
    else:
        try:
            source_pvc, source_ns = runtime_kube.resolve_vm_source_pvc(
                image_source_pvc=source_pvc_name,
                runtime_namespace=runtime_namespace,
            )
            source_storage_class = str(getattr(source_pvc.spec, "storage_class_name", "") or "").strip()
            add_check(
                "source_pvc",
                "ok",
                f"Source PVC {source_pvc_name} is available in namespace {source_ns}.",
            )
        except Exception as exc:
            add_check("source_pvc", "error", str(exc))

    desired_storage_class = str(settings.kube_vm_storage_class or source_storage_class or "").strip()
    if not desired_storage_class:
        add_check(
            "storage_class",
            "error",
            "BLABS_KUBE_VM_STORAGE_CLASS is not configured and source PVC has no storage class.",
        )
    else:
        try:
            storage_api = k8s_client.StorageV1Api(runtime_kube._client().api_client)
            storage_api.read_storage_class(name=desired_storage_class)
            add_check("storage_class", "ok", f"StorageClass {desired_storage_class} is available.")
        except ApiException as exc:
            add_check(
                "storage_class",
                "error",
                f"StorageClass {desired_storage_class} lookup failed: {exc.reason or exc.status}",
            )
        except Exception as exc:
            add_check("storage_class", "error", f"StorageClass {desired_storage_class} check failed: {exc}")

    node_ok, node_detail = evaluate_node_launch_admission(runtime_kube)
    add_check("node_admission", "ok" if node_ok else "error", node_detail)

    pvc_ok, pvc_detail = evaluate_vm_storage_launch_admission(runtime_kube, namespace=runtime_namespace)
    add_check("pvc_admission", "ok" if pvc_ok else "error", pvc_detail)

    if include_runner_pull_check:
        pull_ok, pull_detail = runtime_kube.check_vm_runner_image_pullability(
            namespace=runtime_namespace,
            timeout_seconds=30,
        )
        add_check("runner_image", "ok" if pull_ok else "error", pull_detail)
    else:
        add_check("runner_image", "ok", "Runner image pullability probe skipped in launch fast-path.")

    selected_cluster_id = str(getattr(template, "cluster_id", "") or local_cluster_id())
    return VMTemplateLaunchPreflight(
        template_id=template.id,
        namespace=runtime_namespace,
        cluster_id=selected_cluster_id,
        ready=not blocking_reason,
        blocking_reason=(blocking_reason or None),
        checks=checks,
    )


def _public_console_host() -> str:
    return (settings.kube_node_external_host or "").strip() or "127.0.0.1"


def _public_api_base() -> str:
    return f"{_public_scheme()}://{_public_console_host()}:30073"


def _request_console_base(request: Request | None) -> tuple[str, str, str]:
    if request is None:
        return _public_api_base(), _public_console_host(), "30073"
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded_proto or str(request.url.scheme or _public_scheme()).strip().lower()
    if scheme not in {"http", "https"}:
        scheme = _public_scheme()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host_header = forwarded_host or str(request.headers.get("host") or "").strip()
    parsed_host = urlparse(f"//{host_header}") if host_header else None
    host = ""
    if parsed_host and parsed_host.hostname:
        host = parsed_host.hostname
    if not host:
        host = _public_console_host()

    port = ""
    if parsed_host and parsed_host.port:
        port = str(parsed_host.port)
    if not port:
        parsed_base = urlparse(str(request.base_url))
        if parsed_base.port:
            port = str(parsed_base.port)
    if not port:
        port = "443" if scheme == "https" else "80"

    base = f"{scheme}://{host}"
    default_port = (scheme == "https" and port == "443") or (scheme == "http" and port == "80")
    if not default_port:
        base = f"{base}:{port}"
    return base, host, port


def _connect_cookie_samesite() -> str:
    raw = str(getattr(settings, "connect_cookie_samesite", "lax") or "lax").strip().lower()
    if raw not in {"lax", "strict", "none"}:
        return "lax"
    return raw


def _connect_cookie_secure(request: Request) -> bool:
    configured_secure = bool(getattr(settings, "connect_cookie_secure", True))
    if configured_secure:
        return True
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
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


def _vm_runtime_namespace(user: User) -> str:
    return vm_runtime_namespace_for_user(user)


def _vm_quota_namespace(user: User) -> str:
    return tenant_namespace_for_user(user)


def _vm_uses_privileged_runtime() -> bool:
    return vm_launch_requires_privileged_runtime()


def _resolve_vm_runtime_namespace_for_image(runtime_kube, desired_namespace: str, image: Image) -> str:
    runtime_namespace = str(desired_namespace or "").strip() or str(settings.kube_namespace or "labs").strip() or "labs"
    source_pvc_name = str(getattr(image, "source_pvc", "") or "").strip()
    if not source_pvc_name:
        return runtime_namespace
    try:
        source_pvc, source_namespace = runtime_kube.resolve_vm_source_pvc(
            image_source_pvc=source_pvc_name,
            runtime_namespace=runtime_namespace,
        )
    except Exception:
        return runtime_namespace
    source_namespace = str(source_namespace or "").strip()
    if not source_namespace or source_namespace == runtime_namespace:
        return runtime_namespace

    source_request = None
    if source_pvc.spec and source_pvc.spec.resources and source_pvc.spec.resources.requests:
        source_request = source_pvc.spec.resources.requests.get("storage")
    source_storage_class = str(getattr(source_pvc.spec, "storage_class_name", "") or "").strip() or None
    try:
        if runtime_kube.supports_cross_namespace_pvc_clone(
            source_pvc_name=source_pvc_name,
            source_namespace=source_namespace,
            target_namespace=runtime_namespace,
            storage_request=source_request,
            storage_class_name=source_storage_class,
        ):
            return runtime_namespace
    except Exception:
        logger.warning(
            "Cross-namespace PVC clone capability probe failed for source %s (%s -> %s)",
            source_pvc_name,
            source_namespace,
            runtime_namespace,
            exc_info=True,
        )

    logger.warning(
        "Cross-namespace PVC clone unsupported for source %s (%s -> %s); falling back VM runtime namespace to %s.",
        source_pvc_name,
        source_namespace,
        runtime_namespace,
        source_namespace,
    )
    return source_namespace


def _instance_namespace(record: Instance, user: User | None = None) -> str:
    explicit = str(getattr(record, "namespace", "") or "").strip()
    if explicit:
        return explicit
    if user is not None:
        return _vm_runtime_namespace(user)
    return str(settings.kube_namespace or "labs").strip() or "labs"


def _instance_cluster_id(record: Instance) -> str:
    return str(getattr(record, "cluster_id", "") or local_cluster_id()).strip() or local_cluster_id()


def _kube_for_instance_cluster(session: Session, cluster_id: str):
    try:
        return kube_service_for_cluster(session, cluster_id)
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def _vm_service_host(instance_id: str, namespace: str) -> str:
    return f"svc-{instance_id[:8]}.{namespace}.svc.cluster.local"


def _vm_rdp_ready_status(instance_id: str, namespace: str) -> tuple[bool, str]:
    upstream_host = _vm_service_host(instance_id, namespace)
    for scheme in _vm_http_schemes():
        upstream_url = f"{scheme}://{upstream_host}:6080/rdp-ready"
        verify_tls = scheme != "https"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = requests.get(
                    upstream_url,
                    timeout=_VM_RDP_READY_TIMEOUT_SECONDS,
                    verify=verify_tls,
                )
            if scheme == "http" and _upstream_requires_https(response):
                continue
            if response.status_code != 200:
                return False, "VM process started; waiting for RDP service."
            try:
                payload = response.json()
            except ValueError:
                return False, "VM process started; waiting for RDP service."
            if bool(payload.get("ready")):
                return True, "VM is running."
            return False, "VM process started; waiting for RDP service."
        except requests.RequestException:
            continue
    return False, "VM process started; waiting for RDP service."


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


def _console_provider_from_url(console_url: str | None) -> str:
    raw = str(console_url or "").strip().lower()
    if "/rdp.html" in raw:
        return "guacamole_rdp"
    if "/vnc.html" in raw:
        return "guacamole"
    return "spice"


def _vm_console_embed_url(
    instance_id: str,
    title: str,
    idle_minutes: int,
    spice_password: str,
    request: Request | None = None,
) -> str:
    base, host, port = _request_console_base(request)
    secure_param = 1 if base.startswith("https://") else 0
    ws_path = f"/user/pods/{instance_id}/connect/websockify"
    query = urlencode(
        {
            "host": host,
            "port": port,
            "secure": str(secure_param),
            "title": title,
            "instance_id": instance_id,
            "idle_minutes": str(max(1, int(idle_minutes))),
            "ws_path": ws_path,
            "password": spice_password,
        }
    )
    return f"{base}/user/pods/{instance_id}/connect/spice-embed.html?{query}#password={quote(spice_password, safe='')}"


def _vm_console_vnc_url(
    instance_id: str,
    title: str,
    idle_minutes: int,
    request: Request | None = None,
) -> str:
    base, host, port = _request_console_base(request)
    encrypt = "true" if base.startswith("https://") else "false"
    query = urlencode(
        {
            "host": host,
            "port": port,
            "encrypt": encrypt,
            "title": title,
            "instance_id": instance_id,
            "idle_minutes": str(max(1, int(idle_minutes))),
            "autoconnect": "true",
            "resize": "remote",
            "reconnect": "true",
            "path": f"user/pods/{instance_id}/connect/websockify",
        }
    )
    return f"{base}/user/pods/{instance_id}/connect/vnc.html?{query}"


def _vm_console_rdp_url(
    instance_id: str,
    title: str,
    idle_minutes: int,
    request: Request | None = None,
) -> str:
    base, host, port = _request_console_base(request)
    encrypt = "true" if base.startswith("https://") else "false"
    query = urlencode(
        {
            "host": host,
            "port": port,
            "encrypt": encrypt,
            "title": title,
            "instance_id": instance_id,
            "idle_minutes": str(max(1, int(idle_minutes))),
            "autoconnect": "true",
            "path": f"user/pods/{instance_id}/connect/rdp-tunnel",
        }
    )
    return f"{base}/user/pods/{instance_id}/connect/rdp.html?{query}"


def _vm_console_connect_url(
    instance_id: str,
    title: str,
    idle_minutes: int,
    console_provider: str,
    request: Request | None = None,
    spice_password: str = "",
) -> str:
    provider = normalize_vm_console_provider(console_provider)
    if provider == "guacamole_rdp":
        return _vm_console_rdp_url(
            instance_id=instance_id,
            title=title,
            idle_minutes=idle_minutes,
            request=request,
        )
    if provider == "guacamole":
        return _vm_console_vnc_url(
            instance_id=instance_id,
            title=title,
            idle_minutes=idle_minutes,
            request=request,
        )
    return _vm_console_embed_url(
        instance_id=instance_id,
        title=title,
        idle_minutes=idle_minutes,
        spice_password=spice_password,
        request=request,
    )


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
    if bool(getattr(settings, "vm_connect_insecure_tls", False)):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.get("/templates", response_model=list[VMTemplate])
def list_available_templates(
    user: User = Depends(require_user), session: Session = Depends(get_session)
) -> list[VMTemplate]:
    quota_namespace = _vm_quota_namespace(user)
    team_idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), quota_namespace)
    tenant_scope = {
        normalize_tenant(getattr(user, "team", None), default="default"),
        GLOBAL_TENANT,
    }
    templates = session.exec(
        select(Template).where(Template.enabled == True).where(Template.tenant.in_(tenant_scope))  # noqa: E712
    ).all()
    return [
        VMTemplate(
            id=record.id,
            name=record.name,
            cluster_id=str(getattr(record, "cluster_id", "") or local_cluster_id()),
            description=record.description,
            os_type=record.os_type,
            image_id=record.image_id,
            cpu_cores=record.cpu_cores,
            ram_mb=record.ram_mb,
            auto_delete_minutes=record.auto_delete_minutes,
            idle_timeout_minutes=min(
                max(
                    1,
                    int(
                        getattr(record, "idle_timeout_minutes", settings.idle_timeout_minutes)
                        or settings.idle_timeout_minutes
                    ),
                ),
                team_idle_cap if team_idle_cap is not None else 1440,
            ),
            preclone_pool_size=getattr(record, "preclone_pool_size", 0),
            preclone_pool_max=getattr(record, "preclone_pool_max", getattr(record, "preclone_pool_size", 0)),
            max_active_instances=max(0, int(getattr(record, "max_active_instances", 2) or 0)),
            enabled=record.enabled,
            network_mode=normalize_vm_network_mode(getattr(record, "network_mode", "bridge")),
            console_provider=normalize_vm_console_provider(getattr(record, "console_provider", "spice")),
            created_at=record.created_at,
        )
        for record in templates
    ]


@router.get("/templates/{template_id}/preflight", response_model=VMTemplateLaunchPreflight)
def preflight_template_launch(
    template_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> VMTemplateLaunchPreflight:
    runtime_namespace = _vm_runtime_namespace(user)
    privileged_runtime = _vm_uses_privileged_runtime()
    user_tenant = normalize_tenant(getattr(user, "team", None), default="default")
    template = session.get(Template, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    template_tenant = normalize_tenant(getattr(template, "tenant", None), default=GLOBAL_TENANT)
    if template_tenant not in {user_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {template_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="template image tenant scope is invalid")
    try:
        placement = select_cluster_for_launch(
            session,
            team=getattr(user, "team", None),
            workload_kind="vm",
            template_cluster_id=str(getattr(template, "cluster_id", "") or ""),
        )
    except PlacementError as exc:
        return VMTemplateLaunchPreflight(
            template_id=template.id,
            namespace=runtime_namespace,
            cluster_id="",
            ready=False,
            blocking_reason=str(exc),
            checks=[
                VMTemplateLaunchPreflightCheck(
                    key="placement",
                    status="error",
                    detail=str(exc),
                )
            ],
        )
    selected_cluster_id = str(placement.cluster_id or local_cluster_id()).strip() or local_cluster_id()
    runtime_kube = _kube_for_instance_cluster(session, selected_cluster_id)
    result = _vm_preflight(
        runtime_kube=runtime_kube,
        runtime_namespace=runtime_namespace,
        privileged_runtime_namespace=privileged_runtime,
        template=template,
        image=image,
        team=getattr(user, "team", None),
    )
    result.checks.insert(
        0,
        VMTemplateLaunchPreflightCheck(
            key="placement",
            status="ok",
            detail=f"Selected cluster {selected_cluster_id} ({placement.reason}).",
        ),
    )
    result.cluster_id = selected_cluster_id
    return result


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
        tmpl = templates.get(record.template_id)
        record_namespace = _instance_namespace(record, user)
        instance_cluster_id = _instance_cluster_id(record)
        runtime_kube = _kube_for_instance_cluster(session, instance_cluster_id)
        console_provider = normalize_vm_console_provider(
            getattr(tmpl, "console_provider", _console_provider_from_url(record.console_url))
        )
        pod_status: PodStatus | None = None
        try:
            pod_status = runtime_kube.get_status(record.id, record.owner, namespace=record_namespace)
            mapped = _phase_to_instance_status(pod_status.phase)
        except ApiException as exc:
            if exc.status == 404:
                mapped = "stopped"
            else:
                raise
        stage, detail = _status_feedback(mapped, pod_status, started_at=record.started_at)
        if mapped == "running" and console_provider == "guacamole_rdp":
            rdp_ready, rdp_detail = _vm_rdp_ready_status(record.id, record_namespace)
            if not rdp_ready:
                stage = "starting"
                detail = rdp_detail
        feedback[record.id] = (stage, detail)
        if mapped != record.status:
            record.status = mapped
            record.last_active_at = utc_now()
            session.add(record)
            changed = True
        # Auto-delete stopped/completed instances based on template setting.
        if tmpl and record.status in {"stopped", "completed"}:
            cutoff = utc_now() - timedelta(minutes=tmpl.auto_delete_minutes)
            if record.last_active_at < cutoff:
                try:
                    runtime_kube.delete_pod(
                        record.id, record.owner, disk_pvc=record.disk_pvc, namespace=record_namespace
                    )
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
        stage, detail = feedback.get(record.id, _status_feedback(record.status, None, started_at=record.started_at))
        items.append(
            VMInstance(
                id=record.id,
                template_id=record.template_id,
                owner=record.owner,
                tenant=normalize_tenant(
                    getattr(record, "tenant", None), default=normalize_tenant(user.team, default="default")
                ),
                namespace=_instance_namespace(record, user),
                cluster_id=_instance_cluster_id(record),
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
    idle_minutes = max(
        1,
        int(getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes) or settings.idle_timeout_minutes),
    )
    instance_namespace = _instance_namespace(record, user)
    idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), instance_namespace)
    if idle_cap is not None:
        idle_minutes = min(idle_minutes, idle_cap)
    console_provider = normalize_vm_console_provider(
        getattr(template, "console_provider", _console_provider_from_url(record.console_url))
    )
    if console_provider == "guacamole_rdp":
        rdp_ready, rdp_detail = _vm_rdp_ready_status(record.id, instance_namespace)
        if not rdp_ready:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=rdp_detail)
    spice_password = ""
    if console_provider == "spice":
        spice_password = _extract_spice_password(record.console_url)
        if not spice_password:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="console credentials are not available")
    connect_url = _vm_console_connect_url(
        instance_id=record.id,
        title=(template.name if template else "VM"),
        idle_minutes=idle_minutes,
        console_provider=console_provider,
        spice_password=spice_password,
        request=request,
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
    instance_namespace = _instance_namespace(record, user)
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()

    normalized_path = proxy_path.strip("/")
    if not normalized_path:
        template = session.get(Template, record.template_id)
        idle_minutes = max(
            1,
            int(
                getattr(template, "idle_timeout_minutes", settings.idle_timeout_minutes)
                or settings.idle_timeout_minutes
            ),
        )
        idle_cap = team_idle_timeout_cap(session, getattr(user, "team", None), instance_namespace)
        if idle_cap is not None:
            idle_minutes = min(idle_minutes, idle_cap)
        console_provider = normalize_vm_console_provider(
            getattr(template, "console_provider", _console_provider_from_url(record.console_url))
        )
        spice_password = _extract_spice_password(record.console_url) if console_provider == "spice" else ""
        connect_url = _vm_console_connect_url(
            instance_id=record.id,
            title=(template.name if template else "VM"),
            idle_minutes=idle_minutes,
            console_provider=console_provider,
            spice_password=spice_password,
            request=request,
        )
        response = RedirectResponse(url=connect_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        if issued_connect_session:
            _attach_vm_connect_session_cookie(response, request, instance_id, issued_connect_session)
        return response

    upstream_host = _vm_service_host(instance_id, instance_namespace)
    upstream_path = f"/{normalized_path}"
    query_items = [
        (key, value) for key, value in request.query_params.multi_items() if key not in {"vt", "connect_token"}
    ]
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
    resource_type = "vm"
    token_value = _extract_vm_connect_session_token_ws(websocket)
    if not token_value:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4401")
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
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4401")
        await websocket.close(code=4401, reason=str(exc.detail))
        return
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4404")
        await websocket.close(code=4404, reason="instance not found")
        return
    if record.status not in {"pending", "running"}:
        ws_metrics.record_handshake(resource_type, success=False)
        ws_metrics.record_disconnect(resource_type, direction="downstream", code="4409")
        await websocket.close(code=4409, reason="instance is not running")
        return
    instance_namespace = _instance_namespace(record, user)
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    protocols = [
        part.strip() for part in str(websocket.headers.get("sec-websocket-protocol") or "").split(",") if part.strip()
    ]
    selected_subprotocol = protocols[0] if protocols else None
    await websocket.accept(subprotocol=selected_subprotocol)

    upstream_host = _vm_service_host(instance_id, instance_namespace)
    upstream_path = "/" + proxy_path.lstrip("/")
    query_items = [
        (key, value) for key, value in websocket.query_params.multi_items() if key not in {"cs", "ct", "connect_token"}
    ]
    upstream_query = urlencode(query_items, doseq=True)

    upstream_ws_headers = None

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
                subprotocols=protocols or None,
                additional_headers=upstream_ws_headers,
                origin=None,
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
                        {task_client, task_upstream}, return_when=asyncio.FIRST_COMPLETED
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
            continue

    ws_metrics.record_handshake(resource_type, success=False)
    ws_metrics.record_disconnect(resource_type, direction="upstream", code=ws_metrics.extract_close_code(last_exc))
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
    role_mappings: dict[str, str] = {}
    raw_mappings = str(getattr(cfg, "sso_role_mappings_json", "") or "").strip()
    if raw_mappings:
        try:
            parsed = json.loads(raw_mappings)
            if isinstance(parsed, dict):
                for claim_value, role_value in parsed.items():
                    claim_key = str(claim_value or "").strip().lower()
                    role_key = str(role_value or "").strip().lower()
                    if claim_key and role_key:
                        role_mappings[claim_key] = role_key
        except ValueError:
            role_mappings = {}
    return SSOSettings(
        sso_enabled=cfg.sso_enabled,
        sso_provider=cfg.sso_provider,
        sso_client_id=cfg.sso_client_id,
        sso_client_secret_configured=secret_is_configured(cfg.sso_client_secret),
        sso_authorize_url=cfg.sso_authorize_url,
        sso_token_url=cfg.sso_token_url,
        sso_userinfo_url=cfg.sso_userinfo_url,
        sso_redirect_url=cfg.sso_redirect_url,
        sso_role_claim=str(getattr(cfg, "sso_role_claim", "groups") or "groups").strip() or "groups",
        sso_default_role=str(getattr(cfg, "sso_default_role", "user") or "user").strip() or "user",
        sso_role_mappings=role_mappings,
        sso_auto_create_users=bool(getattr(cfg, "sso_auto_create_users", True)),
        sso_sync_roles_on_login=bool(getattr(cfg, "sso_sync_roles_on_login", True)),
    )


@router.post("/templates/{template_id}/start", response_model=VMInstance, status_code=status.HTTP_201_CREATED)
def start_vm(
    template_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> VMInstance:
    runtime_namespace = _vm_runtime_namespace(user)
    quota_namespace = _vm_quota_namespace(user)
    privileged_runtime = _vm_uses_privileged_runtime()
    user_tenant = normalize_tenant(getattr(user, "team", None), default="default")
    template = session.get(Template, template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    template_tenant = normalize_tenant(getattr(template, "tenant", None), default=GLOBAL_TENANT)
    if template_tenant not in {user_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {template_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="template image tenant scope is invalid")
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
        1
        for inst in [*user_vm_instances, *user_container_instances]
        if inst.status not in {"stopped", "completed", "failed"}
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
        namespace=quota_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(template.cpu_cores or 1)) * 1000,
        requested_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
        requested_storage_gib=_vm_storage_request_gib(image),
        requested_idle_timeout_minutes=template.idle_timeout_minutes or settings.idle_timeout_minutes,
    )
    try:
        placement = select_cluster_for_launch(
            session,
            team=getattr(user, "team", None),
            workload_kind="vm",
            template_cluster_id=str(getattr(template, "cluster_id", "") or ""),
        )
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    selected_cluster_id = str(placement.cluster_id or local_cluster_id()).strip() or local_cluster_id()
    runtime_kube = _kube_for_instance_cluster(session, selected_cluster_id)
    runtime_namespace = _resolve_vm_runtime_namespace_for_image(runtime_kube, runtime_namespace, image)
    preflight = _vm_preflight(
        runtime_kube=runtime_kube,
        runtime_namespace=runtime_namespace,
        privileged_runtime_namespace=privileged_runtime,
        template=template,
        image=image,
        team=getattr(user, "team", None),
        include_runner_pull_check=False,
    )
    if not preflight.ready:
        detail = str(preflight.blocking_reason or "VM launch preflight failed.").strip()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"launch preflight failed: {detail}")

    use_legacy_orchestration = vm_orchestration_uses_legacy_path()
    write_crd_shadow = vm_orchestration_writes_crd()
    instance_id = str(uuid4())
    console_provider = normalize_vm_console_provider(getattr(template, "console_provider", "spice"))
    spice_password = _generate_spice_password() if console_provider == "spice" else ""
    rdp_default_username, rdp_default_password = (None, None)
    if console_provider == "guacamole_rdp":
        rdp_default_username, rdp_default_password = _resolve_template_rdp_defaults(template)
    pod_status: PodStatus | None = None
    disk_pvc: str | None = None
    if use_legacy_orchestration:
        try:
            warm_pool_pvc = runtime_kube.reserve_warm_pool_pvc(
                template.id, instance_id, user.username, namespace=runtime_namespace
            )
        except Exception:
            warm_pool_pvc = None
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
            console_provider=console_provider,
            spice_password=(spice_password or None),
            rdp_default_username=rdp_default_username,
            rdp_default_password=rdp_default_password,
            namespace=runtime_namespace,
        )
        try:
            pod_status = runtime_kube.create_pod(pod_request)
        except Exception:
            if warm_pool_pvc:
                try:
                    runtime_kube._client().delete_namespaced_persistent_volume_claim(
                        name=warm_pool_pvc,
                        namespace=runtime_namespace,
                    )
                except Exception:
                    pass
            raise
        disk_pvc = pod_status.disk_pvc
        # Keep VM console service internal; browser access goes through authenticated backend proxy.
        service_name = f"svc-{instance_id[:8]}"
        runtime_kube.create_service_for_pod(
            pod_name=runtime_kube._pod_name(pod_request),
            service_name=service_name,
            service_type="ClusterIP",
            namespace=runtime_namespace,
        )

    console_url = _vm_console_connect_url(
        instance_id=instance_id,
        title=template.name,
        idle_minutes=idle_minutes,
        console_provider=console_provider,
        spice_password=spice_password,
    )

    instance = Instance(
        id=instance_id,
        template_id=template.id,
        owner=user.username,
        tenant=user_tenant,
        namespace=runtime_namespace,
        cluster_id=selected_cluster_id,
        status="pending",
        disk_pvc=disk_pvc,
        started_at=utc_now(),
        last_active_at=utc_now(),
        console_url=console_url,
    )
    session.add(instance)
    session.commit()
    session.refresh(instance)

    if write_crd_shadow:
        try:
            upsert_vm_labinstance(
                instance_id=instance.id,
                owner=instance.owner,
                template=template,
                image=image,
                namespace=runtime_namespace,
                desired_state="running",
                status_phase="Pending",
                status_message="Queued for operator reconciliation.",
            )
        except Exception as exc:
            if use_legacy_orchestration:
                logger.warning("Failed to shadow-write LabInstance CRD for %s: %s", instance.id, exc, exc_info=True)
            else:
                session.delete(instance)
                session.commit()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LabInstance CRD is unavailable; VM launch cannot be queued.",
                ) from exc

    stage, detail = _status_feedback(instance.status, pod_status, started_at=instance.started_at)
    if not use_legacy_orchestration:
        detail = "Queued for operator reconciliation."
    return VMInstance(
        id=instance.id,
        template_id=instance.template_id,
        owner=instance.owner,
        tenant=normalize_tenant(getattr(instance, "tenant", None), default=user_tenant),
        namespace=str(getattr(instance, "namespace", "") or runtime_namespace),
        cluster_id=_instance_cluster_id(instance),
        status=instance.status,
        status_stage=stage,
        status_detail=detail,
        started_at=instance.started_at,
        last_active_at=instance.last_active_at,
        console_url=instance.console_url,
    )


@router.post("/pods/{instance_id}/stop", response_model=VMInstance)
def stop_vm(
    instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    instance_namespace = _instance_namespace(record, user)
    runtime_kube = _kube_for_instance_cluster(session, _instance_cluster_id(record))
    use_legacy_orchestration = vm_orchestration_uses_legacy_path()
    write_crd_shadow = vm_orchestration_writes_crd()
    if use_legacy_orchestration:
        runtime_kube.stop_pod(instance_id, record.owner, namespace=instance_namespace)
    if write_crd_shadow:
        try:
            patch_vm_labinstance_desired_state(instance_id, "stopped", namespace=instance_namespace)
        except Exception as exc:
            if use_legacy_orchestration:
                logger.warning(
                    "Failed to patch LabInstance desired state=stopped for %s: %s",
                    instance_id,
                    exc,
                    exc_info=True,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LabInstance CRD is unavailable; VM stop cannot be queued.",
                ) from exc
    record.status = "stopped"
    record.last_active_at = utc_now()
    session.add(record)
    session.commit()
    session.refresh(record)
    stage, detail = _status_feedback(record.status, None, started_at=record.started_at)
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        tenant=normalize_tenant(
            getattr(record, "tenant", None), default=normalize_tenant(user.team, default="default")
        ),
        namespace=instance_namespace,
        cluster_id=_instance_cluster_id(record),
        status=record.status,
        status_stage=stage,
        status_detail=detail,
        started_at=record.started_at,
        last_active_at=record.last_active_at,
        console_url=record.console_url,
    )


@router.post("/pods/{instance_id}/start", response_model=VMInstance, status_code=status.HTTP_200_OK)
def restart_vm(
    instance_id: str, user: User = Depends(require_user), session: Session = Depends(get_session)
) -> VMInstance:
    record = session.get(Instance, instance_id)
    if not record or record.owner != user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="instance not found")
    runtime_namespace = _vm_runtime_namespace(user)
    quota_namespace = _vm_quota_namespace(user)
    privileged_runtime = _vm_uses_privileged_runtime()
    template = session.get(Template, record.template_id)
    if not template or not template.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    template_tenant = normalize_tenant(getattr(template, "tenant", None), default=GLOBAL_TENANT)
    user_tenant = normalize_tenant(getattr(user, "team", None), default="default")
    if template_tenant not in {user_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found or disabled")
    image = session.get(Image, template.image_id)
    if not image:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image missing for template")
    image_tenant = normalize_tenant(getattr(image, "tenant", None), default=GLOBAL_TENANT)
    if image_tenant not in {template_tenant, GLOBAL_TENANT}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="template image tenant scope is invalid")
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
        namespace=quota_namespace,
        requested_labs=1,
        requested_cpu_millicores=max(1, int(template.cpu_cores or 1)) * 1000,
        requested_memory_mb=max(1, int(template.ram_mb or 512)) + max(0, int(settings.vm_memory_overhead_mb or 0)),
        requested_storage_gib=_vm_storage_request_gib(image),
        requested_idle_timeout_minutes=template.idle_timeout_minutes or settings.idle_timeout_minutes,
        exclude_vm_instance_id=record.id,
    )
    try:
        placement = select_cluster_for_launch(
            session,
            team=getattr(user, "team", None),
            workload_kind="vm",
            template_cluster_id=str(getattr(template, "cluster_id", "") or ""),
        )
    except PlacementError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    selected_cluster_id = str(placement.cluster_id or local_cluster_id()).strip() or local_cluster_id()
    runtime_kube = _kube_for_instance_cluster(session, selected_cluster_id)
    runtime_namespace = _resolve_vm_runtime_namespace_for_image(runtime_kube, runtime_namespace, image)
    try:
        ensure_team_runtime_namespace(
            runtime_kube,
            team=getattr(user, "team", None),
            namespace=runtime_namespace,
            privileged_runtime=privileged_runtime,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    use_legacy_orchestration = vm_orchestration_uses_legacy_path()
    write_crd_shadow = vm_orchestration_writes_crd()
    console_provider = normalize_vm_console_provider(getattr(template, "console_provider", "spice"))
    spice_password = _generate_spice_password() if console_provider == "spice" else ""
    rdp_default_username, rdp_default_password = (None, None)
    if console_provider == "guacamole_rdp":
        rdp_default_username, rdp_default_password = _resolve_template_rdp_defaults(template)
    pod_status: PodStatus | None = None
    disk_pvc = record.disk_pvc
    if use_legacy_orchestration:
        # Ensure any old pod with the same name is removed before re-create.
        try:
            runtime_kube.delete_pod(instance_id, user.username, disk_pvc=record.disk_pvc, namespace=runtime_namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

        try:
            warm_pool_pvc = runtime_kube.reserve_warm_pool_pvc(
                template.id, record.id, user.username, namespace=runtime_namespace
            )
        except Exception:
            warm_pool_pvc = None
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
            console_provider=console_provider,
            spice_password=(spice_password or None),
            rdp_default_username=rdp_default_username,
            rdp_default_password=rdp_default_password,
            namespace=runtime_namespace,
        )
        try:
            pod_status = runtime_kube.create_pod(pod_request)
        except Exception:
            if warm_pool_pvc:
                try:
                    runtime_kube._client().delete_namespaced_persistent_volume_claim(
                        name=warm_pool_pvc,
                        namespace=runtime_namespace,
                    )
                except Exception:
                    pass
            raise
        disk_pvc = pod_status.disk_pvc
        service_name = f"svc-{instance_id[:8]}"
        runtime_kube.create_service_for_pod(
            pod_name=runtime_kube._pod_name(pod_request),
            service_name=service_name,
            service_type="ClusterIP",
            namespace=runtime_namespace,
        )
    console_url = _vm_console_connect_url(
        instance_id=record.id,
        title=template.name,
        idle_minutes=idle_minutes,
        console_provider=console_provider,
        spice_password=spice_password,
    )

    record.status = "pending"
    record.tenant = user_tenant
    record.namespace = runtime_namespace
    record.cluster_id = selected_cluster_id
    record.disk_pvc = disk_pvc
    record.started_at = utc_now()
    record.last_active_at = utc_now()
    record.console_url = console_url
    session.add(record)
    session.commit()
    session.refresh(record)
    if write_crd_shadow:
        try:
            upsert_vm_labinstance(
                instance_id=record.id,
                owner=record.owner,
                template=template,
                image=image,
                namespace=runtime_namespace,
                desired_state="running",
                status_phase="Pending",
                status_message="Queued for operator reconciliation.",
            )
        except Exception as exc:
            if use_legacy_orchestration:
                logger.warning("Failed to shadow-write LabInstance CRD for %s: %s", record.id, exc, exc_info=True)
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LabInstance CRD is unavailable; VM restart cannot be queued.",
                ) from exc
    stage, detail = _status_feedback(record.status, pod_status, started_at=record.started_at)
    if not use_legacy_orchestration:
        detail = "Queued for operator reconciliation."
    return VMInstance(
        id=record.id,
        template_id=record.template_id,
        owner=record.owner,
        tenant=normalize_tenant(getattr(record, "tenant", None), default=user_tenant),
        namespace=runtime_namespace,
        cluster_id=_instance_cluster_id(record),
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
    instance_namespace = _instance_namespace(record, user)
    runtime_kube = _kube_for_instance_cluster(session, _instance_cluster_id(record))
    use_legacy_orchestration = vm_orchestration_uses_legacy_path()
    write_crd_shadow = vm_orchestration_writes_crd()
    if use_legacy_orchestration:
        try:
            runtime_kube.delete_pod(instance_id, record.owner, disk_pvc=record.disk_pvc, namespace=instance_namespace)
        except ApiException as exc:
            # VM teardown can race with Kubernetes garbage collection.
            # Treat not-found/conflict during delete as best-effort success.
            if exc.status not in {404, 409, 422}:
                raise
            logger.warning(
                "Best-effort VM delete fallback for instance %s (owner=%s): %s",
                instance_id,
                record.owner,
                exc,
            )
    if write_crd_shadow:
        if use_legacy_orchestration:
            delete_vm_labinstance_best_effort(instance_id, namespace=instance_namespace)
        else:
            try:
                delete_vm_labinstance(instance_id, namespace=instance_namespace, missing_ok=True)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LabInstance CRD is unavailable; VM delete cannot be queued.",
                ) from exc
    session.delete(record)
    session.commit()
