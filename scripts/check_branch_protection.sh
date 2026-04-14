#!/usr/bin/env bash
set -euo pipefail

REPO_SLUG="${REPO_SLUG:-}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"
REQUIRED_CHECKS="${REQUIRED_CHECKS:-guardrails,userflow-smoke,synthetic,live-smoke-gate,restore-drill,rdp-smoke}"

if [ -z "${GH_TOKEN:-}" ]; then
  echo "ERROR: GH_TOKEN is required." >&2
  exit 1
fi

if [ -z "$REPO_SLUG" ]; then
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  REPO_SLUG="$(printf '%s' "$remote_url" | sed -E 's#^.*github.com[:/]([^/]+/[^/.]+)(\.git)?$#\1#')"
fi
if [ -z "$REPO_SLUG" ]; then
  echo "ERROR: unable to determine REPO_SLUG (set REPO_SLUG=owner/repo)." >&2
  exit 1
fi

api_url="https://api.github.com/repos/${REPO_SLUG}/branches/${TARGET_BRANCH}/protection"
payload="$(
  curl -fsSL "$api_url" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"

python3 - "$REQUIRED_CHECKS" "$payload" <<'PY'
import json
import sys

required = [item.strip() for item in str(sys.argv[1]).split(",") if item.strip()]
payload = json.loads(sys.argv[2])

contexts = []
for row in ((payload or {}).get("required_status_checks") or {}).get("contexts") or []:
    if isinstance(row, str):
        contexts.append(row)
    elif isinstance(row, dict):
        value = str(row.get("context") or "").strip()
        if value:
            contexts.append(value)
missing = [item for item in required if item not in contexts]
if missing:
    print("FAIL: branch protection missing required status checks:", file=sys.stderr)
    for item in missing:
        print(f"- {item}", file=sys.stderr)
    raise SystemExit(1)
print("PASS: branch protection required checks include all expected contexts.")
PY
