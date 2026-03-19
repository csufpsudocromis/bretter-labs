#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="$ROOT_DIR/scripts/setup.sh"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

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
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py"

run_success "strict production profile validation passes concrete production defaults" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict

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

run_failure "reject invalid production go-live proof toggle" \
  env SETUP_DRY_RUN=1 RUN_PRODUCTION_GO_LIVE_PROOF=2 "$SETUP_SCRIPT"

run_failure "reject invalid production go-live proof health timeout" \
  env SETUP_DRY_RUN=1 RUN_PRODUCTION_GO_LIVE_PROOF=1 PRODUCTION_GO_LIVE_HEALTH_TIMEOUT_SECONDS=abc "$SETUP_SCRIPT"

run_failure "reject invalid runner smoke toggle" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK=2 "$SETUP_SCRIPT"

run_failure "reject production profile with missing explicit control-node override" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE=ghcr.io/csufpsudocromis/bretter-backend@sha256:3680afce30f651faf7758eb56ea8a15a84a5101e1448098d50b858eb95e0a906 \
  FRONTEND_IMAGE=ghcr.io/csufpsudocromis/bretter-frontend@sha256:ab276331c5c9f9125b3ed4b67fbfd057358f3d2132de713d20c4cc4db49a947a \
  RUNNER_IMAGE=ghcr.io/csufpsudocromis/win-vm-runner@sha256:5a96b3743e1dabd2ae82f481edadc1cdbbd869a15b91891828a7e41305a40e76 \
  RUNNER_NODE_SELECTOR_VALUE=runner-pool NODE_EXTERNAL_HOST=prod-labs.internal VM_STORAGE_CLASS=prod-vm-storage \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_success "allow production dry-run with explicit hardened overrides" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE=ghcr.io/csufpsudocromis/bretter-backend@sha256:3680afce30f651faf7758eb56ea8a15a84a5101e1448098d50b858eb95e0a906 \
  FRONTEND_IMAGE=ghcr.io/csufpsudocromis/bretter-frontend@sha256:ab276331c5c9f9125b3ed4b67fbfd057358f3d2132de713d20c4cc4db49a947a \
  RUNNER_IMAGE=ghcr.io/csufpsudocromis/win-vm-runner@sha256:5a96b3743e1dabd2ae82f481edadc1cdbbd869a15b91891828a7e41305a40e76 \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  CONTROL_NODE: ""
EOF
run_failure "strict production profile validation rejects placeholder overrides" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  SECRETS_ENCRYPTION_KEY: "dont-commit-secrets-in-values"
EOF
run_failure "strict production profile validation rejects committed plaintext secrets key" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

tmp_values="$(mktemp)"
cat >"$tmp_values" <<'EOF'
appTemplateValues:
  RUNTIME_SECRETS_ENCRYPTION_KEY_KEY: ""
EOF
run_failure "strict production profile validation requires runtime secret injection keys" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$ROOT_DIR/deploy/helm/values-production.yaml" -f "$tmp_values"
rm -f "$tmp_values"

echo "[smoke] setup.sh smoke checks passed"
