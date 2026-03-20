#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path


def _load_app():
    root = Path(__file__).resolve().parents[1]
    backend_dir = root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from src.main import app  # noqa: PLC0415

    return app


def _normalized_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Fail when checked-in OpenAPI snapshot drifts from backend app.")
    parser.add_argument(
        "-f",
        "--file",
        default=str(root / "backend" / "openapi" / "openapi.json"),
        help="Path to checked-in OpenAPI JSON snapshot.",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.file)
    if not snapshot_path.is_absolute():
        snapshot_path = (root / snapshot_path).resolve()
    if not snapshot_path.exists():
        print(
            f"ERROR: OpenAPI snapshot file not found: {snapshot_path}\n"
            f"Run: python3 scripts/export_openapi_schema.py -o {snapshot_path}",
            file=sys.stderr,
        )
        return 1

    try:
        expected_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse snapshot JSON: {snapshot_path}: {exc}", file=sys.stderr)
        return 1

    app = _load_app()
    current_payload = app.openapi()

    expected_text = _normalized_json(expected_payload)
    current_text = _normalized_json(current_payload)
    if expected_text == current_text:
        print("OpenAPI snapshot is up to date.")
        return 0

    diff = difflib.unified_diff(
        expected_text.splitlines(),
        current_text.splitlines(),
        fromfile=f"{snapshot_path} (checked-in)",
        tofile="runtime-openapi (generated)",
        lineterm="",
    )
    print("OpenAPI schema drift detected. Regenerate and commit updated artifacts:", file=sys.stderr)
    print("  python3 scripts/export_openapi_schema.py", file=sys.stderr)
    print("  npm --prefix frontend-vite run generate:api-types", file=sys.stderr)
    print("", file=sys.stderr)
    for line in diff:
        print(line, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
