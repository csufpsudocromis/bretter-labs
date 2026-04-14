#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FLOW_LINE_RE = re.compile(r"PASS:\s+synthetic validation succeeded\s+\((.*?)\)\.", re.IGNORECASE)
DEFAULT_REQUIRED_FLOW = [
    "login",
    "VM launch",
    "Guacamole RDP readiness/frame",
    "VM teardown",
    "container launch/websocket readiness/connect/delete",
]


def _latest_report(glob_pattern: str) -> Path | None:
    matches = sorted(Path().glob(glob_pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        return None
    return matches[0]


def _validate_report_content(
    content: str,
    *,
    required_flow_steps: list[str],
    require_image_upload_check: bool,
) -> list[str]:
    errors: list[str] = []
    if "PASS: post-deploy synthetic user flow check" not in content:
        errors.append("missing summary gate line: PASS: post-deploy synthetic user flow check")

    match = FLOW_LINE_RE.search(content)
    if not match:
        errors.append("missing detailed flow line from post_deploy_synthetic_check.py output")
        return errors

    flow_text = str(match.group(1) or "")
    flow_parts = [part.strip() for part in flow_text.split("->") if part.strip()]
    for step in required_flow_steps:
        if step not in flow_parts:
            errors.append(f"missing required synthetic flow step: {step}")

    if require_image_upload_check and "admin image upload/finalize/delete" not in flow_parts:
        errors.append("missing required synthetic image upload/finalize/delete step")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify go-live/deploy report includes required synthetic post-deploy gate "
            "and detailed end-to-end flow coverage."
        )
    )
    parser.add_argument(
        "--report",
        default="",
        help="Path to report file. If omitted, latest file from --report-glob is used.",
    )
    parser.add_argument(
        "--report-glob",
        default="artifacts/go-live/production-go-live-*.txt",
        help="Glob used to find latest report when --report is not set.",
    )
    parser.add_argument(
        "--require-flow-step",
        action="append",
        default=[],
        help="Additional flow step label that must appear in synthetic success line. Can be repeated.",
    )
    parser.add_argument(
        "--require-image-upload-check",
        action="store_true",
        help="Require synthetic flow to include admin image upload/finalize/delete step.",
    )
    args = parser.parse_args()

    if args.report:
        report_path = Path(args.report)
    else:
        latest = _latest_report(args.report_glob)
        if latest is None:
            print(f"FAIL: no report files matched {args.report_glob!r}", file=sys.stderr)
            return 1
        report_path = latest

    if not report_path.exists():
        print(f"FAIL: report not found: {report_path}", file=sys.stderr)
        return 1

    content = report_path.read_text(encoding="utf-8", errors="replace")
    required_flow_steps = list(DEFAULT_REQUIRED_FLOW)
    required_flow_steps.extend(str(item).strip() for item in args.require_flow_step if str(item).strip())
    errors = _validate_report_content(
        content,
        required_flow_steps=required_flow_steps,
        require_image_upload_check=bool(args.require_image_upload_check),
    )
    if errors:
        print(f"FAIL: synthetic gate verification failed for {report_path}", file=sys.stderr)
        for item in errors:
            print(f" - {item}", file=sys.stderr)
        return 1

    print(f"PASS: synthetic gate verified in {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
