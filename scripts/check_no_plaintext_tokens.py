#!/usr/bin/env python3
from __future__ import annotations

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


def main() -> int:
    failures = 0
    for path in tracked_files():
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
                    rel = path.relative_to(ROOT)
                    print(f"ERROR: potential plaintext token in {rel}:{idx}", file=sys.stderr)
                    failures += 1
                    break
    if failures:
        print(f"ERROR: plaintext token scan failed with {failures} finding(s).", file=sys.stderr)
        return 1
    print("Plaintext token scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
