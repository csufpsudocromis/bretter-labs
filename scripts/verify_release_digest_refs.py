#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KEYS = ("BACKEND_IMAGE", "BACKEND_ADMIN_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE")
REF_RE = re.compile(
    r"^(?P<repo>[^@\s:]+(?:/[^@\s:]+)+):(?P<tag>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})$",
    re.IGNORECASE,
)


def _extract_value(text: str, key: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}:\s*(.*?)\s*$", flags=re.MULTILINE)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"missing key {key}")
    return str(match.group(1) or "").strip().strip('"').strip("'")


def _normalize_expected_tag(raw: str) -> tuple[str, ...]:
    expected = str(raw or "").strip()
    if not expected:
        return tuple()
    values = {expected}
    if expected.startswith("v"):
        values.add(expected[1:])
    else:
        values.add(f"v{expected}")
    return tuple(sorted(values))


def _tag_matches_expected(tag: str, expected_values: tuple[str, ...]) -> bool:
    if not expected_values:
        return True
    tag = str(tag or "").strip()
    for expected in expected_values:
        if tag == expected:
            return True
        if tag.startswith(f"{expected}-"):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify production image refs are immutable digest pins for a release tag."
    )
    parser.add_argument(
        "--values-file",
        default="deploy/helm/values-production.yaml",
        help="Path to Helm values file to validate.",
    )
    parser.add_argument(
        "--require-tag",
        default="",
        help="Optional release tag to enforce (for example: v0.3.1). "
        "Each image tag must match or start with this tag prefix.",
    )
    args = parser.parse_args()

    values_path = Path(args.values_file).resolve()
    if not values_path.exists():
        print(f"FAIL: values file not found: {values_path}", file=sys.stderr)
        return 1
    text = values_path.read_text(encoding="utf-8")

    expected_tags = _normalize_expected_tag(args.require_tag)
    errors: list[str] = []
    for key in KEYS:
        try:
            value = _extract_value(text, key)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        match = REF_RE.match(value)
        if not match:
            errors.append(f"{key} is not a digest-pinned tagged ref: {value!r}")
            continue
        tag = str(match.group("tag") or "").strip()
        if not _tag_matches_expected(tag, expected_tags):
            errors.append(
                f"{key} tag {tag!r} does not match required release tag {args.require_tag!r} "
                "(exact or '<tag>-<suffix>' allowed)."
            )

    if errors:
        print("FAIL: release digest verification failed:", file=sys.stderr)
        for item in errors:
            print(f" - {item}", file=sys.stderr)
        return 1

    print(f"PASS: digest-pinned release refs validated in {values_path}")
    if args.require_tag:
        print(f"PASS: required release tag matched: {args.require_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
