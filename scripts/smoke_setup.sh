#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="$ROOT_DIR/scripts/setup.sh"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi
PROD_BACKEND_IMAGE="ghcr.io/csufpsudocromis/bretter-backend-runtime:v0.3.1@sha256:9431c8a0774ae07529d74c5b57b35a0cf93f66642955d67da884c3953d1ab2fe"
PROD_BACKEND_ADMIN_IMAGE="ghcr.io/csufpsudocromis/bretter-backend:v0.3.1@sha256:9431c8a0774ae07529d74c5b57b35a0cf93f66642955d67da884c3953d1ab2fe"
PROD_FRONTEND_IMAGE="ghcr.io/csufpsudocromis/bretter-frontend:v0.3.1@sha256:ab276331c5c9f9125b3ed4b67fbfd057358f3d2132de713d20c4cc4db49a947a"
PROD_RUNNER_IMAGE="ghcr.io/csufpsudocromis/win-vm-runner:v0.3.1@sha256:5a96b3743e1dabd2ae82f481edadc1cdbbd869a15b91891828a7e41305a40e76"

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

run_failure "reject backend hpa max lower than min" \
  env SETUP_DRY_RUN=1 BACKEND_HPA_MIN_REPLICAS=2 BACKEND_HPA_MAX_REPLICAS=1 "$SETUP_SCRIPT"

run_failure "reject backend replicas outside hpa bounds" \
  env SETUP_DRY_RUN=1 BACKEND_REPLICAS=3 BACKEND_HPA_MIN_REPLICAS=1 BACKEND_HPA_MAX_REPLICAS=2 "$SETUP_SCRIPT"

run_failure "reject invalid uvicorn workers value" \
  env SETUP_DRY_RUN=1 UVICORN_WORKERS=0 "$SETUP_SCRIPT"

run_success "allow autoscaling bounds with valid worker tuning" \
  env SETUP_DRY_RUN=1 BACKEND_REPLICAS=1 BACKEND_HPA_MIN_REPLICAS=1 BACKEND_HPA_MAX_REPLICAS=3 \
  FRONTEND_REPLICAS=2 FRONTEND_HPA_MIN_REPLICAS=2 FRONTEND_HPA_MAX_REPLICAS=4 \
  UVICORN_WORKERS=2 "$SETUP_SCRIPT"

run_failure "reject invalid image import backend mode" \
  env SETUP_DRY_RUN=1 IMAGE_IMPORT_BACKEND=invalid "$SETUP_SCRIPT"

run_failure "reject crd image-import backend without labimageimport controller" \
  env SETUP_DRY_RUN=1 IMAGE_IMPORT_BACKEND=crd LABIMAGEIMPORT_CONTROLLER_ENABLED=0 "$SETUP_SCRIPT"

run_failure "reject invalid team namespace mode" \
  env SETUP_DRY_RUN=1 TEAM_NAMESPACE_MODE=invalid "$SETUP_SCRIPT"

run_failure "reject invalid team namespace prefix in per_team mode" \
  env SETUP_DRY_RUN=1 TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_PREFIX=invalid-prefix "$SETUP_SCRIPT"

run_failure "reject invalid admission policy toggle" \
  env SETUP_DRY_RUN=1 ENABLE_ADMISSION_POLICIES=2 "$SETUP_SCRIPT"

run_failure "reject invalid post-deploy API health check toggle" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_API_HEALTH_CHECK=2 "$SETUP_SCRIPT"

run_failure "reject invalid admin API smoke toggle" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_ADMIN_API_SMOKE_CHECK=2 "$SETUP_SCRIPT"

run_failure "reject invalid admin API smoke timeout" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_ADMIN_API_SMOKE_CHECK=1 ADMIN_API_SMOKE_PASSWORD=bootstrap-secret POST_DEPLOY_ADMIN_API_SMOKE_TIMEOUT_SECONDS=20 "$SETUP_SCRIPT"

run_failure "reject invalid bootstrap env prune toggle" \
  env SETUP_DRY_RUN=1 PRUNE_BOOTSTRAP_ADMIN_ENV=2 "$SETUP_SCRIPT"

run_failure "reject invalid production go-live proof toggle" \
  env SETUP_DRY_RUN=1 RUN_PRODUCTION_GO_LIVE_PROOF=2 "$SETUP_SCRIPT"

run_failure "reject invalid production go-live proof health timeout" \
  env SETUP_DRY_RUN=1 RUN_PRODUCTION_GO_LIVE_PROOF=1 PRODUCTION_GO_LIVE_HEALTH_TIMEOUT_SECONDS=abc "$SETUP_SCRIPT"

run_failure "reject invalid runner smoke toggle" \
  env SETUP_DRY_RUN=1 RUN_POST_DEPLOY_RUNNER_SMOKE_CHECK=2 "$SETUP_SCRIPT"

run_failure "reject invalid postgres backup toggle" \
  env SETUP_DRY_RUN=1 ENABLE_POSTGRES_BACKUP_AUTOMATION=2 "$SETUP_SCRIPT"

run_failure "reject invalid postgres backup PVC size" \
  env SETUP_DRY_RUN=1 POSTGRES_BACKUP_PVC_SIZE=invalid "$SETUP_SCRIPT"

run_failure "reject invalid image-import queue age threshold" \
  env SETUP_DRY_RUN=1 ENABLE_USERFLOW_SLO_PROBES=1 USERFLOW_SLO_IMAGE_IMPORT_QUEUE_MAX_AGE_MINUTES=0 "$SETUP_SCRIPT"

run_failure "reject rdp connect latency probe without secret wiring" \
  env SETUP_DRY_RUN=1 ENABLE_USERFLOW_SLO_PROBES=1 ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE=1 USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0 USERFLOW_SLO_API_PASSWORD=smoke-rdp-probe-secret "$SETUP_SCRIPT"

run_success "allow rdp connect latency probe with credentials" \
  env SETUP_DRY_RUN=1 ENABLE_USERFLOW_SLO_PROBES=1 ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE=1 USERFLOW_SLO_API_USERNAME=admin USERFLOW_SLO_API_PASSWORD=smoke-rdp-probe-secret "$SETUP_SCRIPT"

run_failure "reject production profile with missing explicit control-node override" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  RUNNER_NODE_SELECTOR_VALUE=runner-pool NODE_EXTERNAL_HOST=prod-labs.internal VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_success "allow production profile when backup replication is disabled" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=0 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_success "allow production dry-run with explicit hardened overrides" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_failure "reject production profile with local/dev image references" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE=localhost/bretter-backend:v0.3.1@sha256:9431c8a0774ae07529d74c5b57b35a0cf93f66642955d67da884c3953d1ab2fe \
  BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_failure "reject production profile when schema gate is disabled" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 REQUIRE_SCHEMA_READY=0 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  "$SETUP_SCRIPT"

run_failure "reject production rdp probe with setup-managed auth bootstrap" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  ENABLE_USERFLOW_SLO_PROBES=1 ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE=1 \
  USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=1 USERFLOW_SLO_API_PASSWORD=smoke-secret \
  "$SETUP_SCRIPT"

run_success "allow production rdp probe with pre-provisioned secret auth" \
  env SETUP_DRY_RUN=1 PRODUCTION_PROFILE=1 CORS_ENTERPRISE_PROFILE=1 CORS_ALLOWED_ORIGINS=https://prod-labs.internal:30073 \
  BACKEND_IMAGE="$PROD_BACKEND_IMAGE" BACKEND_ADMIN_IMAGE="$PROD_BACKEND_ADMIN_IMAGE" \
  FRONTEND_IMAGE="$PROD_FRONTEND_IMAGE" RUNNER_IMAGE="$PROD_RUNNER_IMAGE" \
  TEAM_NAMESPACE_MODE=per_team TEAM_NAMESPACE_BOOTSTRAP_ENABLED=1 \
  RUN_POST_DEPLOY_SYNTHETIC_CHECK=1 SYNTHETIC_CHECK_REQUIRE_TEMPLATES=1 \
  POST_DEPLOY_AUTH_SECRET_NAME=bretter-postdeploy-auth ORCHESTRATION_BACKEND=dual \
  CONTROL_NODE=control-plane-1 NODE_EXTERNAL_HOST=prod-labs.internal RUNNER_NODE_SELECTOR_VALUE=runner-pool VM_STORAGE_CLASS=prod-vm-storage \
  ENABLE_POSTGRES_BACKUP_REPLICATION=1 POSTGRES_BACKUP_REPLICATION_BUCKET=prod-backups \
  POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE=GOVERNANCE POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS=30 \
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1 CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub \
  ENABLE_USERFLOW_SLO_PROBES=1 ENABLE_USERFLOW_SLO_RDP_CONNECT_LATENCY_PROBE=1 \
  USERFLOW_SLO_API_AUTH_MANAGED_BY_SETUP=0 USERFLOW_SLO_API_AUTH_SECRET_NAME=rdp-slo-auth \
  USERFLOW_SLO_API_AUTH_USERNAME_KEY=username USERFLOW_SLO_API_AUTH_PASSWORD_KEY=password \
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
