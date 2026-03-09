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


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"failed to parse JSON: {path}: {exc}") from exc


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    version_path = root / "VERSION"
    changelog_path = root / "CHANGELOG.md"
    frontend_pkg_path = root / "frontend-vite" / "package.json"
    frontend_lock_path = root / "frontend-vite" / "package-lock.json"

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

    if errors:
        print("Release/version discipline checks failed:", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"Release/version discipline checks passed (version {version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
