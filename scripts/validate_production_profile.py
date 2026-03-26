#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

TAGGED_DIGEST_PIN_RE = re.compile(r"^[^@\s]+:v?[0-9]+(\.[0-9]+){2}([-.+][0-9A-Za-z.-]+)?@sha256:[0-9a-f]{64}$")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off", ""}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"values file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"failed to parse yaml: {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"values file must contain a mapping at top-level: {path}")
    return raw


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
            continue
        merged[key] = value
    return merged


def _normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return (
        not value
        or "<" in value
        or ">" in value
        or "changeme" in lowered
        or "example" in lowered
        or lowered in {"tbd", "todo"}
    )


def _is_digest_pinned(image_ref: str) -> bool:
    return bool(TAGGED_DIGEST_PIN_RE.match(image_ref))


def _looks_local_image_ref(image_ref: str) -> bool:
    lowered = str(image_ref or "").strip().lower()
    if not lowered:
        return False
    return lowered.startswith("localhost/") or ":local" in lowered or "local-" in lowered


def _split_csv_values(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for item in str(raw or "").split(","):
        value = str(item or "").strip()
        if not value:
            continue
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        values.append(value)
    return values


def _validate(values: dict[str, Any], *, strict: bool) -> tuple[list[str], list[str]]:
    app_values = values.get("appTemplateValues")
    if not isinstance(app_values, dict):
        return ["values must contain appTemplateValues mapping"], []

    errors: list[str] = []
    warnings: list[str] = []

    def get_text(key: str) -> str:
        return _normalized_text(app_values.get(key, ""))

    def get_bool(key: str, *, default: bool = False) -> bool:
        normalized = _normalize_bool(app_values.get(key, default))
        if normalized is None:
            errors.append(f"{key} must be a boolean-like value (0/1/true/false).")
            return default
        return normalized

    def get_uint(key: str) -> int:
        raw = get_text(key)
        if not raw:
            errors.append(f"{key} is required.")
            return 0
        try:
            parsed = int(raw)
        except ValueError:
            errors.append(f"{key} must be an integer >= 1 (found: {raw!r}).")
            return 0
        if parsed < 1:
            errors.append(f"{key} must be an integer >= 1 (found: {raw!r}).")
        return parsed

    def get_non_negative_int(key: str, *, default: int = 0) -> int:
        raw = get_text(key)
        if not raw:
            return default
        try:
            parsed = int(raw)
        except ValueError:
            errors.append(f"{key} must be an integer >= 0 (found: {raw!r}).")
            return default
        if parsed < 0:
            errors.append(f"{key} must be an integer >= 0 (found: {raw!r}).")
            return default
        return parsed

    def get_percent(key: str) -> int:
        parsed = get_uint(key)
        if parsed > 100:
            errors.append(f"{key} must be an integer in 1-100 (found: {parsed!r}).")
        return parsed

    def validate_hpa(component: str, replicas: int) -> None:
        min_key = f"{component}_HPA_MIN_REPLICAS"
        max_key = f"{component}_HPA_MAX_REPLICAS"
        target_key = f"{component}_HPA_TARGET_CPU_UTILIZATION_PERCENT"
        min_replicas = get_uint(min_key)
        max_replicas = get_uint(max_key)
        get_percent(target_key)
        if max_replicas and min_replicas and max_replicas < min_replicas:
            errors.append(f"{max_key} must be >= {min_key}.")
        if replicas and min_replicas and max_replicas and (replicas < min_replicas or replicas > max_replicas):
            errors.append(f"{component}_REPLICAS must be between {min_key} and {max_key}.")

    for key in ("BACKEND_IMAGE", "BACKEND_ADMIN_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE"):
        image_ref = get_text(key)
        if not image_ref:
            errors.append(f"{key} is required.")
            continue
        if not _is_digest_pinned(image_ref):
            errors.append(
                f"{key} must be release-tagged and digest-pinned (<repo>:vX.Y.Z@sha256:...) " f"(found: {image_ref!r})."
            )
        if _looks_local_image_ref(image_ref):
            errors.append(f"{key} must not use local/dev image references in production (found: {image_ref!r}).")

    backend_replicas = get_uint("BACKEND_REPLICAS")
    frontend_replicas = get_uint("FRONTEND_REPLICAS")
    validate_hpa("BACKEND", backend_replicas)
    validate_hpa("FRONTEND", frontend_replicas)

    uvicorn_workers = get_uint("UVICORN_WORKERS")
    if uvicorn_workers > 32:
        errors.append("UVICORN_WORKERS must be <= 32.")

    if get_bool("ALLOW_MUTABLE_IMAGE_TAGS", default=False):
        errors.append("ALLOW_MUTABLE_IMAGE_TAGS must be disabled for production.")
    if get_bool("ALLOW_CODE_MOUNT_OVERRIDES", default=False):
        errors.append("ALLOW_CODE_MOUNT_OVERRIDES must be disabled for production.")
    if not get_bool("PRODUCTION_PROFILE", default=False):
        errors.append("PRODUCTION_PROFILE must be enabled for production.")
    if not get_bool("REQUIRE_SCHEMA_READY", default=True):
        errors.append("REQUIRE_SCHEMA_READY must be enabled for production.")
    expected_revision = get_text("EXPECTED_ALEMBIC_REVISION")
    if expected_revision and not re.match(r"^[A-Za-z0-9_]+$", expected_revision):
        errors.append("EXPECTED_ALEMBIC_REVISION must be empty or an alphanumeric Alembic revision id.")
    if get_bool("BACKEND_NODEPORT_ENABLED", default=False):
        errors.append("BACKEND_NODEPORT_ENABLED must be disabled for production.")
    if get_bool("VM_CONNECT_INSECURE_TLS", default=False):
        errors.append("VM_CONNECT_INSECURE_TLS must be false for production.")
    if get_bool("CONTAINER_CONNECT_INSECURE_TLS", default=False):
        errors.append("CONTAINER_CONNECT_INSECURE_TLS must be false for production.")
    if get_bool("METRICS_SERVER_INSECURE_TLS", default=False):
        errors.append("METRICS_SERVER_INSECURE_TLS must be false for production.")
    if not get_bool("CORS_ENTERPRISE_PROFILE", default=False):
        errors.append("CORS_ENTERPRISE_PROFILE must be enabled for production.")
    orchestration_backend = get_text("ORCHESTRATION_BACKEND").lower() or "db"
    if orchestration_backend not in {"db", "dual", "crd"}:
        errors.append("ORCHESTRATION_BACKEND must be one of: db, dual, crd.")
    if orchestration_backend == "db":
        errors.append("ORCHESTRATION_BACKEND must be dual or crd for production.")
    if orchestration_backend in {"dual", "crd"}:
        if not get_text("LABINSTANCE_CRD_GROUP"):
            errors.append("LABINSTANCE_CRD_GROUP is required when ORCHESTRATION_BACKEND is dual/crd.")
        if not get_text("LABINSTANCE_CRD_VERSION"):
            errors.append("LABINSTANCE_CRD_VERSION is required when ORCHESTRATION_BACKEND is dual/crd.")
        if not get_text("LABINSTANCE_CRD_PLURAL"):
            errors.append("LABINSTANCE_CRD_PLURAL is required when ORCHESTRATION_BACKEND is dual/crd.")
        if not get_text("LABINSTANCE_CRD_FINALIZER"):
            errors.append("LABINSTANCE_CRD_FINALIZER is required when ORCHESTRATION_BACKEND is dual/crd.")

    public_scheme = get_text("PUBLIC_SCHEME").lower() or "https"
    if public_scheme != "https":
        errors.append("PUBLIC_SCHEME must be https for production.")
    if not get_text("TLS_SECRET_NAME"):
        errors.append("TLS_SECRET_NAME is required for production.")

    cors_origins = get_text("CORS_ALLOWED_ORIGINS")
    if not cors_origins:
        errors.append("CORS_ALLOWED_ORIGINS must be explicitly set for production.")
    elif "localhost" in cors_origins.lower() or "127.0.0.1" in cors_origins:
        errors.append("CORS_ALLOWED_ORIGINS contains localhost/127.0.0.1; replace with real UI origins.")

    if get_text("CORS_ALLOWED_ORIGIN_REGEX"):
        errors.append("CORS_ALLOWED_ORIGIN_REGEX must be empty in production profile.")

    cors_methods = _split_csv_values(get_text("CORS_ALLOWED_METHODS"))
    if "*" in cors_methods:
        errors.append("CORS_ALLOWED_METHODS cannot include wildcard '*' in production profile.")
    cors_headers = _split_csv_values(get_text("CORS_ALLOWED_HEADERS"))
    if "*" in cors_headers:
        errors.append("CORS_ALLOWED_HEADERS cannot include wildcard '*' in production profile.")

    required_override_keys = ("CONTROL_NODE", "NODE_EXTERNAL_HOST", "RUNNER_NODE_SELECTOR_VALUE", "VM_STORAGE_CLASS")
    for key in required_override_keys:
        value = get_text(key)
        if not _looks_placeholder(value):
            continue
        errors.append(f"{key} appears unset/placeholder and must be overridden for production.")

    kube_use_kvm = get_bool("KUBE_USE_KVM", default=True)
    vm_runner_privileged = get_bool("VM_RUNNER_PRIVILEGED", default=False)
    vm_net_backend = get_text("VM_NET_BACKEND").lower() or "user"
    vm_privileged_runtime_isolation_enabled = get_bool("VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED", default=True)
    vm_privileged_namespace_prefix = get_text("VM_PRIVILEGED_NAMESPACE_PREFIX")
    if vm_privileged_runtime_isolation_enabled and _looks_placeholder(vm_privileged_namespace_prefix):
        errors.append(
            "VM_PRIVILEGED_NAMESPACE_PREFIX must be set when VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED is enabled."
        )
    elif vm_privileged_namespace_prefix and not vm_privileged_namespace_prefix.endswith("-"):
        errors.append("VM_PRIVILEGED_NAMESPACE_PREFIX must end with '-' for deterministic namespace names.")
    if (
        kube_use_kvm or vm_runner_privileged or vm_net_backend == "tap-nat"
    ) and not vm_privileged_runtime_isolation_enabled:
        errors.append(
            "VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED must be enabled when privileged VM runners are required."
        )

    database_pool_size = get_uint("DATABASE_POOL_SIZE")
    database_pool_max_overflow = get_non_negative_int("DATABASE_POOL_MAX_OVERFLOW", default=0)
    database_pool_timeout_seconds = get_uint("DATABASE_POOL_TIMEOUT_SECONDS")
    database_pool_recycle_seconds = get_uint("DATABASE_POOL_RECYCLE_SECONDS")
    database_statement_timeout_ms = get_uint("DATABASE_STATEMENT_TIMEOUT_MS")
    database_slow_query_ms = get_uint("DATABASE_SLOW_QUERY_MS")
    if database_pool_recycle_seconds and database_pool_recycle_seconds < 30:
        errors.append("DATABASE_POOL_RECYCLE_SECONDS must be >= 30.")
    if database_statement_timeout_ms and database_statement_timeout_ms < 1000:
        errors.append("DATABASE_STATEMENT_TIMEOUT_MS must be >= 1000 in production.")
    if database_slow_query_ms and database_slow_query_ms > 2000:
        errors.append("DATABASE_SLOW_QUERY_MS must be <= 2000 in production.")
    if database_pool_size and database_pool_size < 5:
        errors.append("DATABASE_POOL_SIZE must be >= 5 in production.")
    if database_pool_timeout_seconds and database_pool_timeout_seconds < 5:
        errors.append("DATABASE_POOL_TIMEOUT_SECONDS must be >= 5 in production.")
    if database_pool_max_overflow > 100:
        warnings.append("DATABASE_POOL_MAX_OVERFLOW is very high (>100); validate DB connection limits.")

    team_namespace_mode = get_text("TEAM_NAMESPACE_MODE").lower() or "shared"
    if team_namespace_mode != "per_team":
        errors.append("TEAM_NAMESPACE_MODE must be per_team for production namespace isolation.")
    team_namespace_prefix = get_text("TEAM_NAMESPACE_PREFIX")
    if team_namespace_mode == "per_team":
        if _looks_placeholder(team_namespace_prefix):
            errors.append("TEAM_NAMESPACE_PREFIX must be set when TEAM_NAMESPACE_MODE=per_team.")
        elif not team_namespace_prefix.endswith("-"):
            errors.append("TEAM_NAMESPACE_PREFIX must end with '-' for deterministic tenant namespace names.")
    team_namespace_bootstrap_enabled = get_bool("TEAM_NAMESPACE_BOOTSTRAP_ENABLED", default=False)
    if not team_namespace_bootstrap_enabled:
        errors.append("TEAM_NAMESPACE_BOOTSTRAP_ENABLED must be enabled for production.")

    runtime_secret_name = get_text("RUNTIME_SECRETS_SECRET_NAME")
    if _looks_placeholder(runtime_secret_name):
        errors.append("RUNTIME_SECRETS_SECRET_NAME must be set for production secret injection.")
    runtime_secret_key = get_text("RUNTIME_SECRETS_ENCRYPTION_KEY_KEY")
    if _looks_placeholder(runtime_secret_key):
        errors.append("RUNTIME_SECRETS_ENCRYPTION_KEY_KEY must be set for production secret injection.")
    elif not re.match(r"^[A-Za-z0-9._-]+$", runtime_secret_key):
        errors.append("RUNTIME_SECRETS_ENCRYPTION_KEY_KEY contains invalid characters.")

    secrets_encryption_key = get_text("SECRETS_ENCRYPTION_KEY")
    if secrets_encryption_key:
        errors.append(
            "SECRETS_ENCRYPTION_KEY must be empty in production values; inject via runtime secret "
            "(RUNTIME_SECRETS_SECRET_NAME/RUNTIME_SECRETS_ENCRYPTION_KEY_KEY)."
        )

    signature_verification_enabled = get_bool("CONTAINER_SIGNATURE_VERIFICATION_ENABLED", default=False)
    signature_key_ref = get_text("CONTAINER_SIGNATURE_KEY_REF")
    signature_key_secret_name = get_text("CONTAINER_SIGNATURE_KEY_SECRET_NAME")
    if not signature_verification_enabled:
        errors.append("CONTAINER_SIGNATURE_VERIFICATION_ENABLED must be enabled for production.")
    if signature_verification_enabled and not signature_key_ref:
        errors.append("CONTAINER_SIGNATURE_KEY_REF must be set when signature verification is enabled.")
    if signature_key_ref.startswith("/etc/bretter-signing/") and _looks_placeholder(signature_key_secret_name):
        errors.append(
            "CONTAINER_SIGNATURE_KEY_SECRET_NAME must be set when CONTAINER_SIGNATURE_KEY_REF points to /etc/bretter-signing/."
        )

    kyverno_signature_scope = get_text("KYVERNO_SIGNATURE_SCOPE").lower()
    if kyverno_signature_scope not in {"namespace_first_party", "enforced_label"}:
        errors.append("KYVERNO_SIGNATURE_SCOPE must be one of: namespace_first_party, enforced_label.")
    elif kyverno_signature_scope != "namespace_first_party":
        errors.append("KYVERNO_SIGNATURE_SCOPE must be namespace_first_party for production.")
    if not get_text("KYVERNO_SIGNATURE_IMAGE_PATTERNS"):
        errors.append("KYVERNO_SIGNATURE_IMAGE_PATTERNS must be set for production.")

    postdeploy_auth_secret_name = get_text("POST_DEPLOY_AUTH_SECRET_NAME")
    if _looks_placeholder(postdeploy_auth_secret_name):
        errors.append("POST_DEPLOY_AUTH_SECRET_NAME must be set for production authenticated post-deploy checks.")
    for key in (
        "POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY",
        "POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY",
        "POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY",
        "POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY",
    ):
        if _looks_placeholder(get_text(key)):
            errors.append(f"{key} must be set for production authenticated post-deploy checks.")

    rdp_probe_enabled = get_bool("ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE", default=False)
    if not rdp_probe_enabled:
        errors.append("ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE must be enabled for production.")
    if rdp_probe_enabled:
        if _looks_placeholder(get_text("USERFLOW_SLO_API_AUTH_SECRET_NAME")):
            errors.append("USERFLOW_SLO_API_AUTH_SECRET_NAME must be set when RDP connect latency probe is enabled.")
        if _looks_placeholder(get_text("USERFLOW_SLO_API_AUTH_USERNAME_KEY")):
            errors.append("USERFLOW_SLO_API_AUTH_USERNAME_KEY must be set when RDP connect latency probe is enabled.")
        if _looks_placeholder(get_text("USERFLOW_SLO_API_AUTH_PASSWORD_KEY")):
            errors.append("USERFLOW_SLO_API_AUTH_PASSWORD_KEY must be set when RDP connect latency probe is enabled.")
        if get_bool("USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP", default=True):
            errors.append("USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP must be disabled (0/false) for production.")

    backup_replication_enabled = get_bool("ENABLE_POSTGRES_BACKUP_REPLICATION", default=False)
    if not backup_replication_enabled:
        errors.append("ENABLE_POSTGRES_BACKUP_REPLICATION must be enabled for production.")
    else:
        if _looks_placeholder(get_text("POSTGRES_BACKUP_REPLICATION_BUCKET")):
            errors.append("POSTGRES_BACKUP_REPLICATION_BUCKET must be set when backup replication is enabled.")
        if _looks_placeholder(get_text("POSTGRES_BACKUP_REPLICATION_SECRET_NAME")):
            errors.append("POSTGRES_BACKUP_REPLICATION_SECRET_NAME must be set when backup replication is enabled.")
        if _looks_placeholder(get_text("POSTGRES_BACKUP_REPLICATION_ACCESS_KEY_ID_KEY")):
            errors.append(
                "POSTGRES_BACKUP_REPLICATION_ACCESS_KEY_ID_KEY must be set when backup replication is enabled."
            )
        if _looks_placeholder(get_text("POSTGRES_BACKUP_REPLICATION_SECRET_ACCESS_KEY_KEY")):
            errors.append(
                "POSTGRES_BACKUP_REPLICATION_SECRET_ACCESS_KEY_KEY must be set when backup replication is enabled."
            )
        sse_mode = get_text("POSTGRES_BACKUP_REPLICATION_SSE_MODE").lower()
        if sse_mode not in {"aes256", "aws:kms"}:
            errors.append(
                "POSTGRES_BACKUP_REPLICATION_SSE_MODE must be AES256 or aws:kms when backup replication is enabled."
            )
        if sse_mode == "aws:kms" and _looks_placeholder(get_text("POSTGRES_BACKUP_REPLICATION_SSE_KMS_KEY_ID")):
            errors.append("POSTGRES_BACKUP_REPLICATION_SSE_KMS_KEY_ID must be set when SSE mode is aws:kms.")
        object_lock_mode = get_text("POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE").lower()
        if object_lock_mode not in {"governance", "compliance"}:
            errors.append(
                "POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE must be GOVERNANCE or COMPLIANCE in production."
            )
        object_lock_days = get_uint("POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS")
        if object_lock_days < 7:
            errors.append("POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS must be >= 7 in production.")

    if get_bool("CONTAINER_INGRESS_ENABLED", default=False):
        if not get_text("CONTAINER_INGRESS_CLASS"):
            errors.append("CONTAINER_INGRESS_CLASS is required when CONTAINER_INGRESS_ENABLED is true.")
        if not get_text("CONTAINER_INGRESS_BASE_DOMAIN"):
            errors.append("CONTAINER_INGRESS_BASE_DOMAIN is required when CONTAINER_INGRESS_ENABLED is true.")

    admin_bootstrap = get_text("ADMIN_BOOTSTRAP_PASSWORD")
    if admin_bootstrap and admin_bootstrap.lower() in {"admin", "password", "changeme", "admin123"}:
        errors.append("ADMIN_BOOTSTRAP_PASSWORD uses a weak default value.")

    return errors, warnings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Validate Bretter Labs production profile values.")
    parser.add_argument(
        "-f",
        "--values",
        action="append",
        default=[],
        help="Helm values file to merge (repeatable). Defaults to deploy/helm/values-production.yaml.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when environment-specific placeholders/defaults are still present.",
    )
    args = parser.parse_args()

    value_files = (
        [Path(item) for item in args.values] if args.values else [root / "deploy" / "helm" / "values-production.yaml"]
    )
    merged: dict[str, Any] = {}
    try:
        for path in value_files:
            candidate = path if path.is_absolute() else (root / path)
            merged = _deep_merge(merged, _read_yaml(candidate))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    errors, warnings = _validate(merged, strict=bool(args.strict))
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        print("Production profile validation failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Production profile validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
