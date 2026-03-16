#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
DIGEST_PIN_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
WEAK_SECRET_VALUES = {"admin", "password", "changeme", "admin123", "secret", "default"}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"failed to parse JSON: {path}: {exc}") from exc


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_yaml_scalar(text: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    if not match:
        return ""
    raw = match.group(1).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1].strip()
    return raw


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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    version_path = root / "VERSION"
    changelog_path = root / "CHANGELOG.md"
    frontend_pkg_path = root / "frontend-vite" / "package.json"
    frontend_lock_path = root / "frontend-vite" / "package-lock.json"
    values_prod_path = root / "deploy" / "helm" / "values-production.yaml"
    setup_script_path = root / "scripts" / "setup.sh"
    frontend_dockerfile_path = root / "frontend-vite" / "Dockerfile"
    backend_dockerfile_path = root / "backend" / "Dockerfile"

    errors: list[str] = []

    version = _read_text(version_path).strip()
    if not version:
        errors.append("VERSION is empty.")
    elif not SEMVER_RE.match(version):
        errors.append(f"VERSION is not valid semantic version: {version}")

    frontend_pkg = _load_json(frontend_pkg_path)
    frontend_pkg_version = str(frontend_pkg.get("version") or "").strip()
    if frontend_pkg_version != version:
        errors.append(
            "frontend-vite/package.json version mismatch: "
            f"expected {version}, found {frontend_pkg_version or '<empty>'}"
        )

    frontend_lock = _load_json(frontend_lock_path)
    lock_version = str(frontend_lock.get("version") or "").strip()
    if lock_version != version:
        errors.append(
            "frontend-vite/package-lock.json version mismatch: "
            f"expected {version}, found {lock_version or '<empty>'}"
        )
    root_pkg = frontend_lock.get("packages", {}).get("", {})
    root_pkg_version = str(root_pkg.get("version") or "").strip()
    if root_pkg_version != version:
        errors.append(
            "frontend-vite/package-lock.json packages[''].version mismatch: "
            f"expected {version}, found {root_pkg_version or '<empty>'}"
        )

    changelog = _read_text(changelog_path)
    if "## [Unreleased]" not in changelog:
        errors.append("CHANGELOG.md missing '## [Unreleased]' section.")
    version_heading = f"## [{version}]"
    if version_heading not in changelog:
        errors.append(f"CHANGELOG.md missing heading for current version: {version_heading}")

    values_production = _read_text(values_prod_path)
    for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE"):
        image_ref = _extract_yaml_scalar(values_production, key)
        if not image_ref:
            errors.append(f"deploy/helm/values-production.yaml missing {key}.")
            continue
        if not DIGEST_PIN_RE.match(image_ref):
            errors.append(
                "deploy/helm/values-production.yaml must pin production images by digest: " f"{key}={image_ref!r}"
            )
    production_profile = _extract_yaml_scalar(values_production, "PRODUCTION_PROFILE")
    if production_profile != "1":
        errors.append(
            "deploy/helm/values-production.yaml must enable backend startup hardening profile: "
            f"PRODUCTION_PROFILE={production_profile!r}"
        )

    required_production_overrides = (
        "CONTROL_NODE",
        "NODE_EXTERNAL_HOST",
        "RUNNER_NODE_SELECTOR_VALUE",
        "VM_STORAGE_CLASS",
    )
    for key in required_production_overrides:
        actual = _extract_yaml_scalar(values_production, key).strip()
        if _looks_placeholder(actual):
            errors.append(
                "deploy/helm/values-production.yaml must define concrete production values "
                f"for {key} (found {actual!r})"
            )

    cors_allowed_origins = _extract_yaml_scalar(values_production, "CORS_ALLOWED_ORIGINS")
    if not cors_allowed_origins:
        errors.append("deploy/helm/values-production.yaml must set CORS_ALLOWED_ORIGINS for production.")
    elif "localhost" in cors_allowed_origins.lower() or "127.0.0.1" in cors_allowed_origins:
        errors.append(
            "deploy/helm/values-production.yaml must not include localhost/127.0.0.1 in CORS_ALLOWED_ORIGINS."
        )

    secrets_encryption_key = _extract_yaml_scalar(values_production, "SECRETS_ENCRYPTION_KEY")
    if _looks_placeholder(secrets_encryption_key) or secrets_encryption_key.lower() in WEAK_SECRET_VALUES:
        errors.append("deploy/helm/values-production.yaml must set a strong SECRETS_ENCRYPTION_KEY.")
    elif len(secrets_encryption_key) < 24:
        errors.append("deploy/helm/values-production.yaml SECRETS_ENCRYPTION_KEY must be at least 24 characters.")

    signature_verification_enabled = (
        _extract_yaml_scalar(values_production, "CONTAINER_SIGNATURE_VERIFICATION_ENABLED").strip().lower()
    )
    if signature_verification_enabled not in {"1", "true", "yes", "on"}:
        errors.append(
            "deploy/helm/values-production.yaml must enable CONTAINER_SIGNATURE_VERIFICATION_ENABLED for production."
        )

    setup_script = _read_text(setup_script_path)
    if 'DEFAULT_IMAGE_TAG="latest"' in setup_script:
        errors.append("scripts/setup.sh must not fall back to DEFAULT_IMAGE_TAG=latest.")
    if re.search(r"METRICS_SERVER_MANIFEST_URL=.*releases/latest", setup_script):
        errors.append("scripts/setup.sh must not default metrics-server manifest URL to releases/latest.")

    frontend_dockerfile = _read_text(frontend_dockerfile_path)
    if "RUN npm ci" not in frontend_dockerfile:
        errors.append("frontend-vite/Dockerfile must use `npm ci` for deterministic installs.")
    if "RUN npm install" in frontend_dockerfile:
        errors.append("frontend-vite/Dockerfile must not use `npm install` in build stage.")

    backend_dockerfile = _read_text(backend_dockerfile_path)
    if "releases/latest" in backend_dockerfile or "contrib/install.sh" in backend_dockerfile:
        errors.append("backend/Dockerfile must pin cosign/trivy downloads to explicit versions.")
    if "kubectl.sha256" not in backend_dockerfile or "sha256sum -c -" not in backend_dockerfile:
        errors.append("backend/Dockerfile must verify kubectl download integrity with checksum validation.")

    if errors:
        print("Release/version discipline checks failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Release/version discipline checks passed (version {version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
