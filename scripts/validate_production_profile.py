#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

DIGEST_PIN_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
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
    return bool(DIGEST_PIN_RE.match(image_ref))


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

    for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE"):
        image_ref = get_text(key)
        if not image_ref:
            errors.append(f"{key} is required.")
            continue
        if not _is_digest_pinned(image_ref):
            errors.append(f"{key} must be digest-pinned with @sha256 (found: {image_ref!r}).")

    if get_bool("ALLOW_MUTABLE_IMAGE_TAGS", default=False):
        errors.append("ALLOW_MUTABLE_IMAGE_TAGS must be disabled for production.")
    if not get_bool("PRODUCTION_PROFILE", default=False):
        errors.append("PRODUCTION_PROFILE must be enabled for production.")
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

    public_scheme = get_text("PUBLIC_SCHEME").lower() or "https"
    if public_scheme != "https":
        errors.append("PUBLIC_SCHEME must be https for production.")
    if not get_text("TLS_SECRET_NAME"):
        errors.append("TLS_SECRET_NAME is required for production.")

    cors_origins = get_text("CORS_ALLOWED_ORIGINS")
    if not cors_origins:
        errors.append("CORS_ALLOWED_ORIGINS must be explicitly set for production.")
    elif "localhost" in cors_origins.lower() or "127.0.0.1" in cors_origins:
        message = "CORS_ALLOWED_ORIGINS contains localhost/127.0.0.1; replace with real UI origins."
        if strict:
            errors.append(message)
        else:
            warnings.append(message)

    if get_text("CORS_ALLOWED_ORIGIN_REGEX"):
        errors.append("CORS_ALLOWED_ORIGIN_REGEX must be empty in production profile.")

    required_override_keys = ("CONTROL_NODE", "NODE_EXTERNAL_HOST", "VM_STORAGE_CLASS")
    for key in required_override_keys:
        value = get_text(key)
        if not _looks_placeholder(value):
            continue
        message = f"{key} appears unset/placeholder and should be overridden for production."
        if strict:
            errors.append(message)
        else:
            warnings.append(message)

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
