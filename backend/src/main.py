import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .auth import hash_password
from .config import settings
from .db import engine, init_db
from .logging_utils import configure_capped_error_file_logging
from .rbac import Role
from .routes import admin, admin_containers, auth, user, user_containers
from .services.kubernetes import kube
from .tables import Config, ContainerImage, User
from .time_utils import utc_now

configure_capped_error_file_logging(settings.error_log_file_path, settings.error_log_max_bytes)

logger = logging.getLogger(__name__)

_reaper_task: asyncio.Task | None = None

def _normalize_origin(origin: str) -> str | None:
    value = str(origin or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _configured_cors_origins() -> list[str]:
    configured = str(getattr(settings, "cors_allowed_origins", "") or "").strip()
    if not configured:
        return []
    allowlist: list[str] = []
    seen: set[str] = set()
    for raw in configured.split(","):
        normalized = _normalize_origin(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        allowlist.append(normalized)
    return allowlist


def _configured_cors_origin_regex() -> str | None:
    raw = str(getattr(settings, "cors_allowed_origin_regex", "") or "").strip()
    return raw or None


def _default_cors_origins() -> list[str]:
    schemes = ["https"]
    if settings.cors_allow_http:
        schemes.append("http")
    allowlist: list[str] = []
    seen: set[str] = set()
    for scheme in schemes:
        for host in ("localhost:5173", "127.0.0.1:5173"):
            origin = f"{scheme}://{host}"
            if origin not in seen:
                seen.add(origin)
                allowlist.append(origin)
    node_host = str(settings.kube_node_external_host or "").strip()
    if node_host:
        public_scheme = str(settings.public_scheme or "https").strip().lower()
        if public_scheme not in {"http", "https"}:
            public_scheme = "https"
        node_origin = f"{public_scheme}://{node_host}:30080"
        if node_origin not in seen:
            allowlist.append(node_origin)
    return allowlist


ALLOWED_ORIGINS = _configured_cors_origins() or _default_cors_origins()
ALLOWED_ORIGIN_REGEX = _configured_cors_origin_regex()


def _apply_storage_overrides(config: Config) -> None:
    if config.storage_root_override is not None:
        value = str(config.storage_root_override).strip()
        if value:
            settings.storage_root = value
    if config.kube_image_pvc_override is not None:
        value = str(config.kube_image_pvc_override).strip()
        if value:
            settings.kube_image_pvc = value
    if config.kube_vm_storage_class_override is not None:
        settings.kube_vm_storage_class = str(config.kube_vm_storage_class_override).strip()


async def reaper_loop() -> None:
    while True:
        try:
            with Session(engine) as session:
                kube.reaper_tick(session)
                _scan_due_container_image(session)
        except Exception as exc:
            logger.warning("Reaper loop error: %s", exc)
        await asyncio.sleep(settings.reaper_interval_seconds)


def _scan_due_container_image(session: Session) -> None:
    if not settings.container_scan_enabled:
        return
    interval_minutes = max(15, int(settings.container_scan_interval_minutes or 360))
    now = utc_now()
    due_rows = session.exec(select(ContainerImage)).all()
    due_rows.sort(key=lambda row: (row.last_scan_at or datetime.min, row.created_at))
    for row in due_rows:
        last_scan_at = row.last_scan_at
        if last_scan_at and (now - last_scan_at).total_seconds() < interval_minutes * 60:
            continue
        status_text, summary_text = kube.scan_container_image(
            image_ref=row.image_ref,
            severity=settings.container_scan_severity,
        )
        row.last_scan_at = now
        row.last_scan_status = status_text
        row.last_scan_summary = summary_text[:512]
        session.add(row)
        session.commit()
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with Session(engine) as session:
        config = session.get(Config, 1)
        if not config:
            config = Config(
                id=1,
                max_concurrent_vms=settings.max_concurrent_vms,
                per_user_vm_limit=settings.per_user_vm_limit,
                idle_timeout_minutes=settings.idle_timeout_minutes,
            )
            session.add(config)
        _apply_storage_overrides(config)
        admin_user = session.get(User, settings.admin_default_username)
        if not admin_user:
            session.add(
                User(
                    username=settings.admin_default_username,
                    password_hash=hash_password(settings.admin_default_password),
                    role=Role.PLATFORM_ADMIN,
                    is_admin=True,
                    force_password_change=True,
                )
            )
        session.commit()
    global _reaper_task
    _reaper_task = asyncio.create_task(reaper_loop())
    try:
        yield
    finally:
        if _reaper_task:
            _reaper_task.cancel()
            try:
                await _reaper_task
            except asyncio.CancelledError:
                pass
            _reaper_task = None


app = FastAPI(
    title="Bretter Labs API",
    version="0.3.0",
    lifespan=lifespan,
    openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    docs_url="/docs" if settings.api_docs_enabled else None,
    redoc_url="/redoc" if settings.api_docs_enabled else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(admin_containers.router, prefix="/admin", tags=["admin"])
app.include_router(user.router, prefix="/user", tags=["user"])
app.include_router(user_containers.router, prefix="/user", tags=["user"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
