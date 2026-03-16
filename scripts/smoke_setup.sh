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

run_success "validate production profile command (repo defaults)" \
  python3 "$ROOT_DIR/scripts/validate_production_profile.py"

run_success "strict production profile validation passes concrete production defaults" \
  python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict

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

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  CONTROL_NODE: ""
EOF
run_failure "strict production profile validation rejects placeholder overrides" \
  python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  SECRETS_ENCRYPTION_KEY: "dont-commit-secrets-in-values"
EOF
run_failure "strict production profile validation rejects committed plaintext secrets key" \
  python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  RUNTIME_SECRETS_ENCRYPTION_KEY_KEY: ""
EOF
run_failure "strict production profile validation requires runtime secret injection keys" \
  python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

echo "[smoke] setup.sh smoke checks passed"
