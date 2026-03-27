import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlparse

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlmodel import Session, select

from .auth import hash_password
from .config import settings
from .db import engine, init_db
from .logging_utils import configure_capped_error_file_logging
from .rbac import Role
from .routes import admin, admin_containers, admin_multicluster, admin_namespaces, auth, user, user_containers
from .services.kubernetes import kube
from .services.multi_cluster import ensure_local_cluster, process_artifact_replication_queue
from .tables import Config, ContainerImage, User
from .time_utils import utc_now
from .version import APP_VERSION

configure_capped_error_file_logging(settings.error_log_file_path, settings.error_log_max_bytes)

logger = logging.getLogger(__name__)

_reaper_task: asyncio.Task | None = None
ENTERPRISE_CORS_DEFAULT_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ENTERPRISE_CORS_DEFAULT_HEADERS = ["Accept", "Content-Type", "Authorization"]
ENTERPRISE_CORS_ALLOWED_METHODS = set(ENTERPRISE_CORS_DEFAULT_METHODS)
INSECURE_BOOTSTRAP_PASSWORDS = {"admin", "password", "changeme", "admin123"}
WEAK_SECRET_VALUES = INSECURE_BOOTSTRAP_PASSWORDS | {"secret", "default"}
IMMUTABLE_CODE_MOUNT_PREFIXES = ("/app/backend/src", "/app/backend/backend/src")


def _resolve_admin_bootstrap_password() -> str:
    configured = str(getattr(settings, "admin_default_password", "") or "").strip()
    if configured:
        return configured
    raise RuntimeError(
        "No admin user exists and BLABS_ADMIN_DEFAULT_PASSWORD is empty. "
        "Provide a one-time bootstrap secret during first deployment."
    )


def _validate_startup_config() -> None:
    admin_username = str(getattr(settings, "admin_default_username", "") or "").strip()
    if not admin_username:
        raise RuntimeError("BLABS_ADMIN_DEFAULT_USERNAME cannot be empty.")

    bootstrap_password = str(getattr(settings, "admin_default_password", "") or "").strip()
    if bootstrap_password and bootstrap_password.lower() in INSECURE_BOOTSTRAP_PASSWORDS:
        raise RuntimeError(
            "BLABS_ADMIN_DEFAULT_PASSWORD cannot use weak defaults "
            "(admin/password/changeme/admin123). Provide a one-time bootstrap secret."
        )

    orchestration_backend = str(getattr(settings, "orchestration_backend", "db") or "db").strip().lower()
    if orchestration_backend not in {"db", "dual", "crd"}:
        raise RuntimeError("BLABS_ORCHESTRATION_BACKEND must be one of: db, dual, crd.")
    if orchestration_backend in {"dual", "crd"}:
        if not str(getattr(settings, "labinstance_crd_group", "") or "").strip():
            raise RuntimeError("BLABS_LABINSTANCE_CRD_GROUP must be set when orchestration backend is dual/crd.")
        if not str(getattr(settings, "labinstance_crd_version", "") or "").strip():
            raise RuntimeError("BLABS_LABINSTANCE_CRD_VERSION must be set when orchestration backend is dual/crd.")
        if not str(getattr(settings, "labinstance_crd_plural", "") or "").strip():
            raise RuntimeError("BLABS_LABINSTANCE_CRD_PLURAL must be set when orchestration backend is dual/crd.")

    image_import_backend = str(getattr(settings, "image_import_backend", "db") or "db").strip().lower()
    if image_import_backend not in {"db", "dual", "crd"}:
        raise RuntimeError("BLABS_IMAGE_IMPORT_BACKEND must be one of: db, dual, crd.")
    if image_import_backend in {"dual", "crd"}:
        if not str(getattr(settings, "labimageimport_crd_group", "") or "").strip():
            raise RuntimeError("BLABS_LABIMAGEIMPORT_CRD_GROUP must be set when image import backend is dual/crd.")
        if not str(getattr(settings, "labimageimport_crd_version", "") or "").strip():
            raise RuntimeError("BLABS_LABIMAGEIMPORT_CRD_VERSION must be set when image import backend is dual/crd.")
        if not str(getattr(settings, "labimageimport_crd_plural", "") or "").strip():
            raise RuntimeError("BLABS_LABIMAGEIMPORT_CRD_PLURAL must be set when image import backend is dual/crd.")

    team_namespace_mode = str(getattr(settings, "team_namespace_mode", "shared") or "shared").strip().lower()
    if team_namespace_mode not in {"shared", "per_team"}:
        raise RuntimeError("BLABS_TEAM_NAMESPACE_MODE must be one of: shared, per_team.")
    if team_namespace_mode == "per_team":
        if not str(getattr(settings, "team_namespace_prefix", "") or "").strip():
            raise RuntimeError("BLABS_TEAM_NAMESPACE_PREFIX must be set when BLABS_TEAM_NAMESPACE_MODE=per_team.")

    db_pool_size = max(0, int(getattr(settings, "database_pool_size", 0) or 0))
    db_pool_timeout_seconds = max(0, int(getattr(settings, "database_pool_timeout_seconds", 0) or 0))
    db_pool_recycle_seconds = max(0, int(getattr(settings, "database_pool_recycle_seconds", 0) or 0))
    db_statement_timeout_ms = max(0, int(getattr(settings, "database_statement_timeout_ms", 0) or 0))
    db_slow_query_ms = max(0, int(getattr(settings, "database_slow_query_ms", 0) or 0))
    if db_pool_size < 1:
        raise RuntimeError("BLABS_DATABASE_POOL_SIZE must be >= 1.")
    if db_pool_timeout_seconds < 1:
        raise RuntimeError("BLABS_DATABASE_POOL_TIMEOUT_SECONDS must be >= 1.")
    if db_pool_recycle_seconds < 30:
        raise RuntimeError("BLABS_DATABASE_POOL_RECYCLE_SECONDS must be >= 30.")
    if db_statement_timeout_ms < 100:
        raise RuntimeError("BLABS_DATABASE_STATEMENT_TIMEOUT_MS must be >= 100.")
    if db_slow_query_ms < 1:
        raise RuntimeError("BLABS_DATABASE_SLOW_QUERY_MS must be >= 1.")

    if not bool(getattr(settings, "production_profile", False)):
        return

    errors: list[str] = []
    if orchestration_backend == "db":
        errors.append("BLABS_ORCHESTRATION_BACKEND must be dual or crd when BLABS_PRODUCTION_PROFILE=true.")
    if str(getattr(settings, "public_scheme", "https") or "").strip().lower() != "https":
        errors.append("BLABS_PUBLIC_SCHEME must be https when BLABS_PRODUCTION_PROFILE=true.")
    if not bool(getattr(settings, "auth_cookie_secure", True)):
        errors.append("BLABS_AUTH_COOKIE_SECURE must be true when BLABS_PRODUCTION_PROFILE=true.")
    if not bool(getattr(settings, "connect_cookie_secure", True)):
        errors.append("BLABS_CONNECT_COOKIE_SECURE must be true when BLABS_PRODUCTION_PROFILE=true.")
    if bool(getattr(settings, "api_docs_enabled", False)):
        errors.append("BLABS_API_DOCS_ENABLED must be false when BLABS_PRODUCTION_PROFILE=true.")
    if bool(getattr(settings, "vm_connect_insecure_tls", False)):
        errors.append("BLABS_VM_CONNECT_INSECURE_TLS must be false when BLABS_PRODUCTION_PROFILE=true.")
    if bool(getattr(settings, "container_connect_insecure_tls", False)):
        errors.append("BLABS_CONTAINER_CONNECT_INSECURE_TLS must be false when BLABS_PRODUCTION_PROFILE=true.")
    if not bool(getattr(settings, "container_signature_verification_enabled", False)):
        errors.append("BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED must be true when BLABS_PRODUCTION_PROFILE=true.")
    signature_key_ref = str(getattr(settings, "container_signature_key_ref", "") or "").strip()
    if not signature_key_ref:
        errors.append("BLABS_CONTAINER_SIGNATURE_KEY_REF must be set when BLABS_PRODUCTION_PROFILE=true.")
    elif signature_key_ref.startswith("/etc/bretter-signing/"):
        if not str(getattr(settings, "container_signature_key_secret_name", "") or "").strip():
            errors.append(
                "BLABS_CONTAINER_SIGNATURE_KEY_SECRET_NAME must be set when BLABS_CONTAINER_SIGNATURE_KEY_REF uses /etc/bretter-signing/."
            )
    if not bool(getattr(settings, "cors_enterprise_profile", False)):
        errors.append("BLABS_CORS_ENTERPRISE_PROFILE must be true when BLABS_PRODUCTION_PROFILE=true.")
    if team_namespace_mode not in {"shared", "per_team"}:
        errors.append("BLABS_TEAM_NAMESPACE_MODE must be shared or per_team when BLABS_PRODUCTION_PROFILE=true.")
    if not bool(getattr(settings, "team_namespace_bootstrap_enabled", True)):
        errors.append("BLABS_TEAM_NAMESPACE_BOOTSTRAP_ENABLED must be true when BLABS_PRODUCTION_PROFILE=true.")
    if not _configured_cors_origins():
        errors.append("BLABS_CORS_ALLOWED_ORIGINS must be set when BLABS_PRODUCTION_PROFILE=true.")
    cors_origins_raw = str(getattr(settings, "cors_allowed_origins", "") or "").strip().lower()
    if "localhost" in cors_origins_raw or "127.0.0.1" in cors_origins_raw:
        errors.append("BLABS_CORS_ALLOWED_ORIGINS must not include localhost/127.0.0.1 in production.")
    if not str(getattr(settings, "kube_node_selector_value", "") or "").strip():
        errors.append("BLABS_KUBE_NODE_SELECTOR_VALUE must be set when BLABS_PRODUCTION_PROFILE=true.")
    if not str(getattr(settings, "kube_vm_storage_class", "") or "").strip():
        errors.append("BLABS_KUBE_VM_STORAGE_CLASS must be set when BLABS_PRODUCTION_PROFILE=true.")
    if db_pool_size < 5:
        errors.append("BLABS_DATABASE_POOL_SIZE must be >= 5 when BLABS_PRODUCTION_PROFILE=true.")
    if db_statement_timeout_ms < 1000:
        errors.append("BLABS_DATABASE_STATEMENT_TIMEOUT_MS must be >= 1000 when BLABS_PRODUCTION_PROFILE=true.")
    if db_slow_query_ms > 2000:
        errors.append("BLABS_DATABASE_SLOW_QUERY_MS must be <= 2000 when BLABS_PRODUCTION_PROFILE=true.")

    vm_requires_privileged_runtime = bool(
        getattr(settings, "vm_runner_privileged", False)
        or getattr(settings, "kube_use_kvm", True)
        or str(getattr(settings, "vm_net_backend", "user") or "user").strip().lower() == "tap-nat"
    )
    vm_privileged_runtime_isolation_enabled = bool(getattr(settings, "vm_privileged_runtime_isolation_enabled", True))
    vm_privileged_namespace_prefix = str(getattr(settings, "vm_privileged_namespace_prefix", "") or "").strip()
    if vm_requires_privileged_runtime and not vm_privileged_runtime_isolation_enabled:
        errors.append(
            "BLABS_VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED must be true when privileged VM runners are required."
        )
    if vm_privileged_runtime_isolation_enabled:
        if not vm_privileged_namespace_prefix:
            errors.append(
                "BLABS_VM_PRIVILEGED_NAMESPACE_PREFIX must be set when BLABS_VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED=true."
            )
        elif not vm_privileged_namespace_prefix.endswith("-"):
            errors.append("BLABS_VM_PRIVILEGED_NAMESPACE_PREFIX must end with '-' when configured.")

    secrets_encryption_key = str(getattr(settings, "secrets_encryption_key", "") or "").strip()
    if not secrets_encryption_key:
        errors.append("BLABS_SECRETS_ENCRYPTION_KEY must be set when BLABS_PRODUCTION_PROFILE=true.")
    elif secrets_encryption_key.lower() in WEAK_SECRET_VALUES:
        errors.append("BLABS_SECRETS_ENCRYPTION_KEY cannot use weak default values in production.")
    elif len(secrets_encryption_key) < 24:
        errors.append("BLABS_SECRETS_ENCRYPTION_KEY must be at least 24 characters in production.")

    if errors:
        raise RuntimeError("Invalid production startup configuration:\n- " + "\n- ".join(errors))
    _validate_immutable_code_mounts()


def _decode_mountinfo_path(raw: str) -> str:
    value = str(raw or "")
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def _find_code_override_mounts() -> list[str]:
    mountinfo_path = "/proc/self/mountinfo"
    try:
        with open(mountinfo_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []

    blocked: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        parts = raw_line.split()
        if len(parts) < 5:
            continue
        mount_path = _decode_mountinfo_path(parts[4]).strip()
        if not mount_path:
            continue
        for prefix in IMMUTABLE_CODE_MOUNT_PREFIXES:
            if mount_path == prefix or mount_path.startswith(f"{prefix}/"):
                if mount_path not in seen:
                    seen.add(mount_path)
                    blocked.append(mount_path)
                break
    return sorted(blocked)


def _validate_immutable_code_mounts() -> None:
    blocked_mounts = _find_code_override_mounts()
    if not blocked_mounts:
        return
    if bool(getattr(settings, "allow_code_mount_overrides", False)):
        logger.warning(
            "Production code-mount override(s) allowed by BLABS_ALLOW_CODE_MOUNT_OVERRIDES=1: %s",
            ", ".join(blocked_mounts),
        )
        return
    raise RuntimeError(
        "Immutable backend deploy violation: code override mount(s) detected in production: "
        + ", ".join(blocked_mounts)
        + ". Remove code-file mounts and redeploy immutable backend images."
    )


def _normalize_origin(origin: str) -> str | None:
    value = str(origin or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _split_csv_values(raw: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in str(raw or "").split(","):
        normalized = str(item or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(normalized)
    return values


def _configured_cors_origins() -> list[str]:
    configured = _split_csv_values(str(getattr(settings, "cors_allowed_origins", "") or ""))
    allowlist: list[str] = []
    seen: set[str] = set()
    for raw in configured:
        normalized = _normalize_origin(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        allowlist.append(normalized)
    return allowlist


def _configured_cors_origin_regex() -> str | None:
    raw = str(getattr(settings, "cors_allowed_origin_regex", "") or "").strip()
    return raw or None


def _configured_cors_methods() -> list[str]:
    configured = _split_csv_values(str(getattr(settings, "cors_allowed_methods", "") or ""))
    methods: list[str] = []
    for method in configured:
        methods.append(method.upper())
    return methods


def _configured_cors_headers() -> list[str]:
    return _split_csv_values(str(getattr(settings, "cors_allowed_headers", "") or ""))


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
        for port in ("30073", "30080"):
            node_origin = f"{public_scheme}://{node_host}:{port}"
            if node_origin not in seen:
                seen.add(node_origin)
                allowlist.append(node_origin)
        root_origin = f"{public_scheme}://{node_host}"
        if root_origin not in seen:
            seen.add(root_origin)
            allowlist.append(root_origin)
    return allowlist


def _resolve_cors_policy() -> tuple[list[str], str | None, list[str], list[str]]:
    configured_origins = _configured_cors_origins()
    configured_regex = _configured_cors_origin_regex()
    enterprise_profile = bool(getattr(settings, "cors_enterprise_profile", False))

    if not enterprise_profile:
        origins = configured_origins or _default_cors_origins()
        return origins, configured_regex, ["*"], ["*"]

    if configured_regex:
        raise RuntimeError(
            "BLABS_CORS_ALLOWED_ORIGIN_REGEX is not permitted when BLABS_CORS_ENTERPRISE_PROFILE=true. "
            "Use explicit BLABS_CORS_ALLOWED_ORIGINS."
        )
    if not configured_origins:
        raise RuntimeError("BLABS_CORS_ALLOWED_ORIGINS must be set when BLABS_CORS_ENTERPRISE_PROFILE=true.")

    configured_methods = _configured_cors_methods()
    methods = configured_methods or ENTERPRISE_CORS_DEFAULT_METHODS
    if "*" in methods:
        raise RuntimeError("Wildcard methods are not permitted when BLABS_CORS_ENTERPRISE_PROFILE=true.")
    invalid_methods = [method for method in methods if method not in ENTERPRISE_CORS_ALLOWED_METHODS]
    if invalid_methods:
        raise RuntimeError(
            "Unsupported BLABS_CORS_ALLOWED_METHODS values in enterprise profile: " + ", ".join(invalid_methods)
        )

    configured_headers = _configured_cors_headers()
    headers = configured_headers or ENTERPRISE_CORS_DEFAULT_HEADERS
    if "*" in headers:
        raise RuntimeError("Wildcard headers are not permitted when BLABS_CORS_ENTERPRISE_PROFILE=true.")

    logger.info(
        "Enterprise CORS profile enabled with explicit origin allowlist (%d origins).",
        len(configured_origins),
    )
    return configured_origins, None, methods, headers


ALLOWED_ORIGINS, ALLOWED_ORIGIN_REGEX, ALLOWED_METHODS, ALLOWED_HEADERS = _resolve_cors_policy()


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
                process_artifact_replication_queue(session, limit=25)
                if bool(getattr(settings, "image_upload_watchdog_enabled", True)):
                    stale_seconds = max(0, int(getattr(settings, "image_upload_watchdog_stale_seconds", 45) or 45))
                    watchdog_stats = admin.run_upload_task_watchdog(
                        session,
                        max_tasks=int(getattr(settings, "image_upload_watchdog_max_tasks", 25) or 25),
                        stale_seconds=stale_seconds,
                    )
                    if watchdog_stats["scanned"] > 0:
                        logger.info(
                            "Upload watchdog (backend fallback) scanned=%s completed=%s failed=%s errors=%s stale_seconds=%s",
                            watchdog_stats["scanned"],
                            watchdog_stats["completed"],
                            watchdog_stats["failed"],
                            watchdog_stats["errors"],
                            stale_seconds,
                        )
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
    _validate_startup_config()
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
            bootstrap_password = _resolve_admin_bootstrap_password()
            session.add(
                User(
                    username=settings.admin_default_username,
                    password_hash=hash_password(bootstrap_password),
                    role=Role.PLATFORM_ADMIN,
                    is_admin=True,
                    force_password_change=True,
                )
            )
            logger.info(
                "Bootstrap admin created for username=%s using configured bootstrap secret. "
                "force_password_change=true",
                settings.admin_default_username,
            )
        ensure_local_cluster(session)
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
    version=APP_VERSION,
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
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(admin_containers.router, prefix="/admin", tags=["admin"])
app.include_router(admin_multicluster.router, prefix="/admin", tags=["admin"])
app.include_router(admin_namespaces.router, prefix="/admin", tags=["admin"])
app.include_router(user.router, prefix="/user", tags=["user"])
app.include_router(user_containers.router, prefix="/user", tags=["user"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
