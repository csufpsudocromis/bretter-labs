#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOKEN_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
]

SKIP_PREFIXES = (
    ".git/",
    ".venv/",
    "frontend-vite/node_modules/",
)


def tracked_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if rel.startswith(SKIP_PREFIXES):
            continue
        out.append(ROOT / rel)
    return out


def _is_skipped_path(path_value: str) -> bool:
    normalized = str(path_value or "").strip().lstrip("./")
    return normalized.startswith(SKIP_PREFIXES)


def scan_tracked_files() -> int:
    failures = 0
    for path in tracked_files():
        rel = str(path.relative_to(ROOT))
        if _is_skipped_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            print(f"ERROR: unable to read {path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            for pattern in TOKEN_PATTERNS:
                if pattern.search(line):
                    print(f"ERROR: potential plaintext token in {rel}:{idx}", file=sys.stderr)
                    failures += 1
                    break
    return failures


def scan_git_history(*, max_findings: int) -> int:
    failures = 0
    current_commit = ""
    current_path = ""
    proc = subprocess.Popen(
        ["git", "log", "--all", "-p", "--pretty=format:__COMMIT__%H"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        if line.startswith("__COMMIT__"):
            current_commit = line.replace("__COMMIT__", "", 1).strip()
            current_path = ""
            continue
        if line.startswith("+++ b/"):
            current_path = line.replace("+++ b/", "", 1).strip()
            continue
        if line.startswith("--- a/"):
            continue
        if not line.startswith(("+", "-")):
            continue
        if line.startswith(("+++", "---")):
            continue
        if _is_skipped_path(current_path):
            continue
        payload = line[1:]
        for pattern in TOKEN_PATTERNS:
            if pattern.search(payload):
                location = current_path or "<unknown>"
                commit = current_commit or "<unknown>"
                print(
                    f"ERROR: potential plaintext token in git history commit={commit} path={location}",
                    file=sys.stderr,
                )
                failures += 1
                break
        if failures >= max_findings:
            print(
                f"ERROR: stopping history scan after {max_findings} findings (increase --history-max-findings to see more).",
                file=sys.stderr,
            )
            proc.kill()
            break
    stderr = proc.communicate()[1]
    if proc.returncode not in {0, -9}:
        detail = (stderr or "").strip()
        print(f"ERROR: git history scan failed: {detail or 'git log failed'}", file=sys.stderr)
        return failures + 1
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail if plaintext GitHub-style tokens are present.")
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan full git history (git log --all -p) for token patterns",
    )
    parser.add_argument(
        "--history-max-findings",
        type=int,
        default=100,
        help="max number of history findings to print before stopping (default: 100)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures = scan_tracked_files()
    if args.history:
        failures += scan_git_history(max_findings=max(1, int(args.history_max_findings or 100)))
    if failures:
        print(f"ERROR: plaintext token scan failed with {failures} finding(s).", file=sys.stderr)
        return 1
    print("Plaintext token scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
