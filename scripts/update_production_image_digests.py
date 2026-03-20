#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

TAGGED_DIGEST_REF_RE = re.compile(r"^[^@\s]+:v?[0-9]+(\.[0-9]+){2}([-.+][0-9A-Za-z.-]+)?@sha256:[0-9a-f]{64}$")


def _validate_digest_ref(name: str, value: str) -> str:
    ref = str(value or "").strip()
    if not TAGGED_DIGEST_REF_RE.match(ref):
        raise ValueError(f"{name} must be a release-tagged digest ref (<repo>:vX.Y.Z@sha256:...) (found {value!r})")
    return ref


def _replace_key(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*).*$", flags=re.MULTILINE)
    if not pattern.search(text):
        raise ValueError(f"key {key!r} not found in values file")
    return pattern.sub(rf"\1{value}", text, count=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update production image digest refs in values-production.yaml.")
    parser.add_argument(
        "--values-file",
        default="deploy/helm/values-production.yaml",
        help="Path to values file (default: deploy/helm/values-production.yaml).",
    )
    parser.add_argument("--backend-image", required=True, help="Digest-pinned backend image ref.")
    parser.add_argument("--backend-admin-image", required=True, help="Digest-pinned backend admin image ref.")
    parser.add_argument("--frontend-image", required=True, help="Digest-pinned frontend image ref.")
    parser.add_argument("--runner-image", required=True, help="Digest-pinned runner image ref.")
    args = parser.parse_args()

    backend_ref = _validate_digest_ref("backend-image", args.backend_image)
    backend_admin_ref = _validate_digest_ref("backend-admin-image", args.backend_admin_image)
    frontend_ref = _validate_digest_ref("frontend-image", args.frontend_image)
    runner_ref = _validate_digest_ref("runner-image", args.runner_image)

    values_path = Path(args.values_file).resolve()
    if not values_path.exists():
        raise SystemExit(f"values file not found: {values_path}")

    original = values_path.read_text(encoding="utf-8")
    updated = original
    updated = _replace_key(updated, "BACKEND_IMAGE", backend_ref)
    updated = _replace_key(updated, "BACKEND_ADMIN_IMAGE", backend_admin_ref)
    updated = _replace_key(updated, "FRONTEND_IMAGE", frontend_ref)
    updated = _replace_key(updated, "RUNNER_IMAGE", runner_ref)

    if updated != original:
        values_path.write_text(updated, encoding="utf-8")
        print(f"Updated production image digests in {values_path}.")
    else:
        print("No changes needed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
