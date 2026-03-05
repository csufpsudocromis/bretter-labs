import asyncio
import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .auth import hash_password
from .config import settings
from .db import engine, init_db
from .logging_utils import configure_capped_error_file_logging
from .routes import admin, admin_containers, auth, user, user_containers
from .services.kubernetes import kube
from .tables import Config, ContainerImage, User

configure_capped_error_file_logging(settings.error_log_file_path, settings.error_log_max_bytes)

logger = logging.getLogger(__name__)

app = FastAPI(title="Bretter Labs API", version="0.3.0")
_reaper_task: asyncio.Task | None = None

ALLOWED_ORIGINS = [
    "https://localhost:5173",
    "https://127.0.0.1:5173",
]
if settings.cors_allow_http:
    ALLOWED_ORIGINS.extend(
        [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
origin_schemes = "https|http" if settings.cors_allow_http else "https"
# Allow any browser origin (http/https) so login/API calls are not tied to specific node IPs.
origin_regex = rf"^({origin_schemes})://[^/]+$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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


@app.on_event("startup")
def bootstrap_defaults() -> None:
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
                    is_admin=True,
                    force_password_change=True,
                )
            )
        session.commit()
    global _reaper_task
    loop = asyncio.get_event_loop()
    _reaper_task = loop.create_task(reaper_loop())


@app.on_event("shutdown")
def stop_reaper() -> None:
    global _reaper_task
    if _reaper_task:
        _reaper_task.cancel()
        _reaper_task = None


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(admin_containers.router, prefix="/admin", tags=["admin"])
app.include_router(user.router, prefix="/user", tags=["user"])
app.include_router(user_containers.router, prefix="/user", tags=["user"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
