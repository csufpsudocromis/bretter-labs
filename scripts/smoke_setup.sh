#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="$ROOT_DIR/scripts/setup.sh"

run_success() {
  local name="$1"
  shift
  echo "[smoke] ${name}"
  if ! "$@" >/dev/null 2>&1; then
    echo "[smoke] FAIL (expected success): ${name}" >&2
    return 1
  fi
}

run_failure() {
  local name="$1"
  shift
  echo "[smoke] ${name}"
  if "$@" >/dev/null 2>&1; then
    echo "[smoke] FAIL (expected failure): ${name}" >&2
    return 1
  fi
}

run_success "dry-run all phases" \
  env SETUP_DRY_RUN=1 SETUP_PHASES=all "$SETUP_SCRIPT"

run_success "dry-run deploy phase only" \
  env SETUP_DRY_RUN=1 SETUP_PHASES=deploy "$SETUP_SCRIPT"

run_success "dry-run postdeploy phase only" \
  env SETUP_DRY_RUN=1 SETUP_PHASES=postdeploy "$SETUP_SCRIPT"

run_failure "reject unsupported phase token" \
  env SETUP_DRY_RUN=1 SETUP_PHASES=prereqs,invalid "$SETUP_SCRIPT"

run_failure "reject mutable latest image references by default" \
  env SETUP_DRY_RUN=1 BACKEND_IMAGE="ghcr.io/csufpsudocromis/bretter-backend:latest" "$SETUP_SCRIPT"

run_success "allow mutable image references only with explicit override" \
  env SETUP_DRY_RUN=1 ALLOW_MUTABLE_IMAGE_TAGS=1 BACKEND_IMAGE="ghcr.io/csufpsudocromis/bretter-backend:latest" "$SETUP_SCRIPT"

run_failure "reject invalid admission policy toggle" \
  env SETUP_DRY_RUN=1 ENABLE_ADMISSION_POLICIES=2 "$SETUP_SCRIPT"

run_failure "reject invalid post-deploy API health check toggle" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_API_HEALTH_CHECK=2 "$SETUP_SCRIPT"

run_failure "reject invalid bootstrap env prune toggle" \
  env SETUP_DRY_RUN=1 PRUNE_BOOTSTRAP_ADMIN_ENV=2 "$SETUP_SCRIPT"

echo "[smoke] setup.sh smoke checks passed"
