#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Export backend OpenAPI schema snapshot.")
    parser.add_argument(
        "-o",
        "--output",
        default=str(root / "backend" / "openapi" / "openapi.json"),
        help="Output path for OpenAPI JSON snapshot.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = (root / output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    app = _load_app()
    schema = app.openapi()
    payload = json.dumps(schema, sort_keys=True, indent=2)
    output.write_text(payload + "\n", encoding="utf-8")
    print(f"OpenAPI schema exported to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
