import asyncio
import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from .auth import hash_password
from .config import settings
from .db import engine, init_db
from .routes import admin, auth, user
from .services.kubernetes import kube
from .tables import Config, User

logger = logging.getLogger(__name__)

app = FastAPI(title="Bretter Labs API", version="0.3.0")
_reaper_task: asyncio.Task | None = None

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://10.68.48.169:5173",
    "http://10.68.49.229:5173",
    "http://10.68.48.105:5173",
    "http://10.68.48.105:30073",
    "http://10.68.48.169:30073",
]
origin_host = settings.kube_node_external_host or "127.0.0.1"
origin_regex = rf"^http://{re.escape(origin_host)}:\d+$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def reaper_loop() -> None:
    while True:
        try:
            with Session(engine) as session:
                kube.reaper_tick(session)
        except Exception as exc:
            logger.warning("Reaper loop error: %s", exc)
        await asyncio.sleep(settings.reaper_interval_seconds)


@app.on_event("startup")
def bootstrap_defaults() -> None:
    init_db()
    with Session(engine) as session:
        config = session.get(Config, 1)
        if not config:
            session.add(
                Config(
                    id=1,
                    max_concurrent_vms=settings.max_concurrent_vms,
                    per_user_vm_limit=settings.per_user_vm_limit,
                    idle_timeout_minutes=settings.idle_timeout_minutes,
                )
            )
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
app.include_router(user.router, prefix="/user", tags=["user"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
