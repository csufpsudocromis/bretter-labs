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

    expected_neutral_defaults = {
        "CONTROL_NODE": "",
        "NODE_EXTERNAL_HOST": "",
        "CORS_ALLOWED_ORIGINS": "https://localhost:30073",
    }
    for key, expected in expected_neutral_defaults.items():
        actual = _extract_yaml_scalar(values_production, key)
        if actual != expected:
            errors.append(
                "deploy/helm/values-production.yaml should keep neutral repo defaults "
                f"for {key}: expected {expected!r}, found {actual!r}"
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

    if errors:
        print("Release/version discipline checks failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Release/version discipline checks passed (version {version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
