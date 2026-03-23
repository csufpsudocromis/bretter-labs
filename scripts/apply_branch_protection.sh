#!/usr/bin/env bash
set -euo pipefail

REPO_SLUG="${REPO_SLUG:-}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"
REQUIRED_CHECKS="${REQUIRED_CHECKS:-guardrails,userflow-smoke,synthetic,restore-drill,rdp-smoke}"
DRY_RUN="${DRY_RUN:-0}"
REQUIRE_APPROVING_REVIEWS="${REQUIRE_APPROVING_REVIEWS:-1}"
REQUIRED_APPROVING_REVIEW_COUNT="${REQUIRED_APPROVING_REVIEW_COUNT:-1}"
ENFORCE_ADMINS="${ENFORCE_ADMINS:-1}"
REQUIRE_CONVERSATION_RESOLUTION="${REQUIRE_CONVERSATION_RESOLUTION:-1}"

is_true() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

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

IFS=',' read -r -a check_contexts <<<"$REQUIRED_CHECKS"
checks_json="$(python3 - "${check_contexts[@]}" <<'PY'
import json
import sys
contexts = [str(item).strip() for item in sys.argv[1:] if str(item).strip()]
print(json.dumps(contexts))
PY
)"

payload="$(python3 - "$checks_json" "$REQUIRE_APPROVING_REVIEWS" "$REQUIRED_APPROVING_REVIEW_COUNT" "$ENFORCE_ADMINS" "$REQUIRE_CONVERSATION_RESOLUTION" <<'PY'
import json
import sys

contexts = json.loads(sys.argv[1])
require_reviews = str(sys.argv[2]).strip().lower() in {"1", "true", "yes", "on"}
review_count = int(str(sys.argv[3]).strip() or "1")
enforce_admins = str(sys.argv[4]).strip().lower() in {"1", "true", "yes", "on"}
require_convo = str(sys.argv[5]).strip().lower() in {"1", "true", "yes", "on"}

payload = {
    "required_status_checks": {"strict": True, "contexts": contexts},
    "enforce_admins": enforce_admins,
    "required_linear_history": True,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "block_creations": False,
    "required_conversation_resolution": require_convo,
    "lock_branch": False,
}
if require_reviews:
    payload["required_pull_request_reviews"] = {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": True,
        "required_approving_review_count": max(1, review_count),
    }
else:
    payload["required_pull_request_reviews"] = None
payload["restrictions"] = None
print(json.dumps(payload))
PY
)"

api_url="https://api.github.com/repos/${REPO_SLUG}/branches/${TARGET_BRANCH}/protection"
if is_true "$DRY_RUN"; then
  echo "DRY_RUN: would apply branch protection to ${REPO_SLUG}:${TARGET_BRANCH}"
  echo "$payload"
  exit 0
fi

curl -fsSL -X PUT "$api_url" \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "$payload" >/dev/null

echo "PASS: branch protection applied for ${REPO_SLUG}:${TARGET_BRANCH}"
echo "required checks: ${REQUIRED_CHECKS}"
