#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
VALID_BUMPS = {"major", "minor", "patch"}


def _parse_version(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid semantic version: {value}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _bump(current: str, kind: str) -> str:
    major, minor, patch = _parse_version(current)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unsupported bump kind: {kind}")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: dict) -> None:
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def _insert_changelog_section(changelog: str, new_version: str) -> str:
    heading = f"## [{new_version}]"
    if heading in changelog:
        return changelog

    marker = "## [Unreleased]"
    if marker not in changelog:
        raise ValueError("CHANGELOG.md is missing '## [Unreleased]' section")

    release_block = (
        f"{marker}\n\n"
        f"{heading} - {date.today().isoformat()}\n\n"
        "### Changed\n\n"
        "- Describe release changes.\n\n"
    )
    return changelog.replace(marker, release_block, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump project semantic version and keep release files in sync.")
    parser.add_argument(
        "target",
        help="major|minor|patch or an explicit semantic version (X.Y.Z)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing files.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    version_path = root / "VERSION"
    changelog_path = root / "CHANGELOG.md"
    frontend_pkg_path = root / "frontend-vite" / "package.json"
    frontend_lock_path = root / "frontend-vite" / "package-lock.json"

    current_version = version_path.read_text(encoding="utf-8").strip()
    _parse_version(current_version)

    target = args.target.strip().lower()
    if target in VALID_BUMPS:
        new_version = _bump(current_version, target)
    else:
        _parse_version(target)
        new_version = target

    if new_version == current_version:
        print(f"Version unchanged: {current_version}")
        return 0

    frontend_pkg = _load_json(frontend_pkg_path)
    frontend_lock = _load_json(frontend_lock_path)
    changelog = changelog_path.read_text(encoding="utf-8")
    next_changelog = _insert_changelog_section(changelog, new_version)

    print(f"Current version: {current_version}")
    print(f"Next version:    {new_version}")

    if args.dry_run:
        print("Dry-run: no files written.")
        return 0

    version_path.write_text(f"{new_version}\n", encoding="utf-8")

    frontend_pkg["version"] = new_version
    _dump_json(frontend_pkg_path, frontend_pkg)

    frontend_lock["version"] = new_version
    packages = frontend_lock.setdefault("packages", {})
    root_package = packages.setdefault("", {})
    root_package["version"] = new_version
    _dump_json(frontend_lock_path, frontend_lock)

    changelog_path.write_text(next_changelog, encoding="utf-8")

    print("Updated:")
    print(f"- {version_path}")
    print(f"- {frontend_pkg_path}")
    print(f"- {frontend_lock_path}")
    print(f"- {changelog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
