#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION" 2>/dev/null || true)"
if [[ "$APP_VERSION" =~ ^[0-9]+(\.[0-9]+){2}$ ]]; then
  DEFAULT_IMAGE_TAG="v${APP_VERSION}"
else
  DEFAULT_IMAGE_TAG=""
fi

NAMESPACE="${NAMESPACE:-labs}"
if [ -n "$DEFAULT_IMAGE_TAG" ]; then
  DEFAULT_BACKEND_IMAGE="ghcr.io/csufpsudocromis/bretter-backend:${DEFAULT_IMAGE_TAG}"
  DEFAULT_FRONTEND_IMAGE="ghcr.io/csufpsudocromis/bretter-frontend:${DEFAULT_IMAGE_TAG}"
  DEFAULT_RUNNER_IMAGE="ghcr.io/csufpsudocromis/win-vm-runner:${DEFAULT_IMAGE_TAG}"
else
  DEFAULT_BACKEND_IMAGE="ghcr.io/csufpsudocromis/bretter-backend"
  DEFAULT_FRONTEND_IMAGE="ghcr.io/csufpsudocromis/bretter-frontend"
  DEFAULT_RUNNER_IMAGE="ghcr.io/csufpsudocromis/win-vm-runner"
fi
BACKEND_IMAGE="${BACKEND_IMAGE:-$DEFAULT_BACKEND_IMAGE}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-$DEFAULT_FRONTEND_IMAGE}"
RUNNER_IMAGE="${RUNNER_IMAGE:-$DEFAULT_RUNNER_IMAGE}"
ALLOW_MUTABLE_IMAGE_TAGS="${ALLOW_MUTABLE_IMAGE_TAGS:-0}"
SETUP_PHASES="${SETUP_PHASES:-prereqs,deploy,postdeploy}"
SETUP_DRY_RUN="${SETUP_DRY_RUN:-0}"
KUBECONFIG_PATH="${KUBECONFIG:-}"
APPLY_GOLDEN_PVC="${APPLY_GOLDEN_PVC:-0}"
APPLY_GOLDEN_HOSTPATH="${APPLY_GOLDEN_HOSTPATH:-1}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"
LOAD_LOCAL_IMAGES="${LOAD_LOCAL_IMAGES:-1}"
PRELOAD_RUNNER_ON_ALL_NODES="${PRELOAD_RUNNER_ON_ALL_NODES:-1}"
CREATE_PULL_SECRET="${CREATE_PULL_SECRET:-0}"
CONTROL_NODE="${CONTROL_NODE:-}"
NODE_EXTERNAL_HOST="${NODE_EXTERNAL_HOST:-}"
RUNNER_NODE_SELECTOR_VALUE="${RUNNER_NODE_SELECTOR_VALUE:-}"
VM_STORAGE_CLASS="${VM_STORAGE_CLASS:-}"
LONGHORN_TUNE="${LONGHORN_TUNE:-1}"
LONGHORN_VM_STORAGE_CLASS="${LONGHORN_VM_STORAGE_CLASS:-longhorn-r1}"
LONGHORN_VM_REPLICA_COUNT="${LONGHORN_VM_REPLICA_COUNT:-1}"
LONGHORN_DEFAULT_REPLICA_COUNT="${LONGHORN_DEFAULT_REPLICA_COUNT:-2}"
LONGHORN_RESERVED_PERCENT="${LONGHORN_RESERVED_PERCENT:-10}"
LONGHORN_MIN_AVAILABLE_PERCENT="${LONGHORN_MIN_AVAILABLE_PERCENT:-5}"
LONGHORN_OVERPROVISION_PERCENT="${LONGHORN_OVERPROVISION_PERCENT:-200}"
LONGHORN_DEFAULT_DATA_PATH="${LONGHORN_DEFAULT_DATA_PATH:-}"
ENABLE_AUTOCLEANUP="${ENABLE_AUTOCLEANUP:-1}"
AUTOCLEANUP_SCHEDULE="${AUTOCLEANUP_SCHEDULE:-*/15 * * * *}"
AUTOCLEANUP_HELPER_MAX_AGE_MINUTES="${AUTOCLEANUP_HELPER_MAX_AGE_MINUTES:-30}"
AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES="${AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES:-60}"
AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES="${AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES:-180}"
AUTOCLEANUP_RESTART_ALERT_COUNT="${AUTOCLEANUP_RESTART_ALERT_COUNT:-3}"
AUTOCLEANUP_NODEFS_WARN_PCT="${AUTOCLEANUP_NODEFS_WARN_PCT:-70}"
AUTOCLEANUP_NODEFS_CRITICAL_PCT="${AUTOCLEANUP_NODEFS_CRITICAL_PCT:-85}"
AUTOCLEANUP_NODEFS_EMERGENCY_PCT="${AUTOCLEANUP_NODEFS_EMERGENCY_PCT:-95}"
AUTOCLEANUP_PVC_WARN_PCT="${AUTOCLEANUP_PVC_WARN_PCT:-70}"
AUTOCLEANUP_PVC_CRITICAL_PCT="${AUTOCLEANUP_PVC_CRITICAL_PCT:-85}"
AUTOCLEANUP_PVC_EMERGENCY_PCT="${AUTOCLEANUP_PVC_EMERGENCY_PCT:-95}"
SETUP_MIN_FREE_GIB="${SETUP_MIN_FREE_GIB:-25}"
SETUP_WARN_FREE_GIB="${SETUP_WARN_FREE_GIB:-40}"
PUBLIC_SCHEME="${PUBLIC_SCHEME:-https}"
TLS_ENABLED="${TLS_ENABLED:-1}"
TLS_SECRET_NAME="${TLS_SECRET_NAME:-bretter-tls}"
TLS_CERT_FILE="${TLS_CERT_FILE:-}"
TLS_KEY_FILE="${TLS_KEY_FILE:-}"
WINDOWS_MACHINE_TYPE="${WINDOWS_MACHINE_TYPE:-q35}"
WINDOWS_EFI_ENABLED="${WINDOWS_EFI_ENABLED:-true}"
WINDOWS_CPU_MODEL="${WINDOWS_CPU_MODEL:-host}"
LINUX_MACHINE_TYPE="${LINUX_MACHINE_TYPE:-pc}"
LINUX_EFI_ENABLED="${LINUX_EFI_ENABLED:-false}"
LINUX_CPU_MODEL="${LINUX_CPU_MODEL:-host}"
VM_NET_BACKEND="${VM_NET_BACKEND:-user}"
VM_RUNNER_PRIVILEGED="${VM_RUNNER_PRIVILEGED:-0}"
VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY="${VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY:-Local}"
VM_CONSOLE_SOURCE_CIDRS="${VM_CONSOLE_SOURCE_CIDRS:-}"
VM_CONSOLE_TICKET_LENGTH="${VM_CONSOLE_TICKET_LENGTH:-24}"
BACKEND_NODEPORT_ENABLED="${BACKEND_NODEPORT_ENABLED:-0}"
BACKEND_NODEPORT="${BACKEND_NODEPORT:-30080}"
PRODUCTION_PROFILE="${PRODUCTION_PROFILE:-0}"
CORS_ENTERPRISE_PROFILE="${CORS_ENTERPRISE_PROFILE:-0}"
CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-}"
CORS_ALLOWED_ORIGIN_REGEX="${CORS_ALLOWED_ORIGIN_REGEX:-}"
CORS_ALLOWED_METHODS="${CORS_ALLOWED_METHODS:-GET,POST,PUT,PATCH,DELETE,OPTIONS}"
CORS_ALLOWED_HEADERS="${CORS_ALLOWED_HEADERS:-Accept,Content-Type,Authorization}"
AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS="${AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS:-300}"
AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS="${AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS:-5}"
AUTH_LOGIN_LOCKOUT_SECONDS="${AUTH_LOGIN_LOCKOUT_SECONDS:-300}"
PRUNE_BOOTSTRAP_ADMIN_ENV="${PRUNE_BOOTSTRAP_ADMIN_ENV:-1}"
VM_CONNECT_INSECURE_TLS="${VM_CONNECT_INSECURE_TLS:-0}"
CONTAINER_CONNECT_INSECURE_TLS="${CONTAINER_CONNECT_INSECURE_TLS:-0}"
SECRETS_ENCRYPTION_KEY="${SECRETS_ENCRYPTION_KEY:-}"
CONTAINER_INGRESS_ENABLED="${CONTAINER_INGRESS_ENABLED:-0}"
CONTAINER_INGRESS_CLASS="${CONTAINER_INGRESS_CLASS:-}"
CONTAINER_INGRESS_BASE_DOMAIN="${CONTAINER_INGRESS_BASE_DOMAIN:-}"
CONTAINER_INGRESS_ANNOTATIONS_JSON="${CONTAINER_INGRESS_ANNOTATIONS_JSON:-{}}"
CONTAINER_IMAGE_PREPULL_ENABLED="${CONTAINER_IMAGE_PREPULL_ENABLED:-1}"
CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS="${CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS:-45}"
CONTAINER_ALLOWED_REGISTRIES="${CONTAINER_ALLOWED_REGISTRIES:-docker.io,ghcr.io,quay.io,mcr.microsoft.com,gcr.io,registry.k8s.io,lscr.io}"
CONTAINER_SIGNATURE_VERIFICATION_ENABLED="${CONTAINER_SIGNATURE_VERIFICATION_ENABLED:-0}"
CONTAINER_SIGNATURE_KEY_REF="${CONTAINER_SIGNATURE_KEY_REF:-}"
CONTAINER_SCAN_ENABLED="${CONTAINER_SCAN_ENABLED:-1}"
CONTAINER_SCAN_INTERVAL_MINUTES="${CONTAINER_SCAN_INTERVAL_MINUTES:-360}"
CONTAINER_SCAN_SEVERITY="${CONTAINER_SCAN_SEVERITY:-HIGH,CRITICAL}"
CONTAINER_START_QUEUE_ENABLED="${CONTAINER_START_QUEUE_ENABLED:-1}"
CONTAINER_START_QUEUE_BASE_DELAY_SECONDS="${CONTAINER_START_QUEUE_BASE_DELAY_SECONDS:-20}"
CONTAINER_START_QUEUE_MAX_DELAY_SECONDS="${CONTAINER_START_QUEUE_MAX_DELAY_SECONDS:-300}"
BACKEND_DATA_HOSTPATH="${BACKEND_DATA_HOSTPATH:-/var/lib/bretter-labs/backend-data}"
GOLDEN_IMAGES_HOSTPATH="${GOLDEN_IMAGES_HOSTPATH:-/var/lib/bretter-labs/golden-images}"
POSTGRES_DATA_HOSTPATH="${POSTGRES_DATA_HOSTPATH:-/var/lib/bretter-labs/postgres-data}"
POSTGRES_USER="${POSTGRES_USER:-bretter}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-bretterpass}"
POSTGRES_DB="${POSTGRES_DB:-bretterlabs}"
USE_EXTERNAL_SECRETS="${USE_EXTERNAL_SECRETS:-0}"
INSTALL_EXTERNAL_SECRETS_OPERATOR="${INSTALL_EXTERNAL_SECRETS_OPERATOR:-1}"
EXTERNAL_SECRETS_NAMESPACE="${EXTERNAL_SECRETS_NAMESPACE:-external-secrets}"
EXTERNAL_SECRETS_RELEASE_NAME="${EXTERNAL_SECRETS_RELEASE_NAME:-external-secrets}"
EXTERNAL_SECRETS_CHART_VERSION="${EXTERNAL_SECRETS_CHART_VERSION:-v2.1.0}"
EXTERNAL_SECRETS_STORE_NAME="${EXTERNAL_SECRETS_STORE_NAME:-corp-secrets}"
CREATE_VAULT_CLUSTER_SECRET_STORE="${CREATE_VAULT_CLUSTER_SECRET_STORE:-0}"
VAULT_ADDR="${VAULT_ADDR:-}"
VAULT_K8S_AUTH_PATH="${VAULT_K8S_AUTH_PATH:-kubernetes}"
VAULT_K8S_ROLE="${VAULT_K8S_ROLE:-external-secrets}"
VAULT_KV_MOUNT="${VAULT_KV_MOUNT:-secret}"
VAULT_KV_VERSION="${VAULT_KV_VERSION:-v2}"
EXTERNAL_SECRETS_CONTROLLER_SERVICEACCOUNT_NAME="${EXTERNAL_SECRETS_CONTROLLER_SERVICEACCOUNT_NAME:-$EXTERNAL_SECRETS_RELEASE_NAME}"
EXTERNAL_POSTGRES_REMOTE_KEY="${EXTERNAL_POSTGRES_REMOTE_KEY:-bretter-labs/postgres}"
EXTERNAL_POSTGRES_USER_PROPERTY="${EXTERNAL_POSTGRES_USER_PROPERTY:-POSTGRES_USER}"
EXTERNAL_POSTGRES_PASSWORD_PROPERTY="${EXTERNAL_POSTGRES_PASSWORD_PROPERTY:-POSTGRES_PASSWORD}"
EXTERNAL_POSTGRES_DB_PROPERTY="${EXTERNAL_POSTGRES_DB_PROPERTY:-POSTGRES_DB}"
EXTERNAL_PULL_SECRET_ENABLED="${EXTERNAL_PULL_SECRET_ENABLED:-0}"
EXTERNAL_PULL_SECRET_REMOTE_KEY="${EXTERNAL_PULL_SECRET_REMOTE_KEY:-bretter-labs/registry}"
EXTERNAL_PULL_SECRET_PROPERTY="${EXTERNAL_PULL_SECRET_PROPERTY:-.dockerconfigjson}"
EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS="${EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS:-180}"
CDI_NAMESPACE="${CDI_NAMESPACE:-cdi}"
INSTALL_CDI="${INSTALL_CDI:-1}"
CDI_VERSION="${CDI_VERSION:-v1.61.0}"
CDI_UPLOAD_NODEPORT="${CDI_UPLOAD_NODEPORT:-30443}"
CDI_UPLOAD_PROXY_URL="${CDI_UPLOAD_PROXY_URL:-}"
CPU_MANAGER_STATIC="${CPU_MANAGER_STATIC:-0}"
ENABLE_MONITORING="${ENABLE_MONITORING:-1}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
MONITORING_RELEASE_NAME="${MONITORING_RELEASE_NAME:-kube-prometheus-stack}"
MONITORING_CHART_VERSION="${MONITORING_CHART_VERSION:-v82.10.4}"
MONITORING_RESTART_ALERT_COUNT="${MONITORING_RESTART_ALERT_COUNT:-3}"
MONITORING_DV_STALE_MINUTES="${MONITORING_DV_STALE_MINUTES:-60}"
MONITORING_WARM_POOL_MIN_READY="${MONITORING_WARM_POOL_MIN_READY:-1}"
ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL="${ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL:-1}"
KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE="${KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE:-*/5 * * * *}"
HELM_VERSION="${HELM_VERSION:-v3.15.4}"
HELM_RELEASE_NAME="${HELM_RELEASE_NAME:-bretter-labs}"
HELM_CHART_DIR="${HELM_CHART_DIR:-$ROOT_DIR/deploy/helm}"
ENABLE_METRICS_SERVER="${ENABLE_METRICS_SERVER:-1}"
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.8.1}"
METRICS_SERVER_MANIFEST_URL="${METRICS_SERVER_MANIFEST_URL:-https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml}"
METRICS_SERVER_INSECURE_TLS="${METRICS_SERVER_INSECURE_TLS:-0}"
ENABLE_ADMISSION_POLICIES="${ENABLE_ADMISSION_POLICIES:-1}"
INSTALL_KYVERNO="${INSTALL_KYVERNO:-1}"
KYVERNO_NAMESPACE="${KYVERNO_NAMESPACE:-kyverno}"
KYVERNO_RELEASE_NAME="${KYVERNO_RELEASE_NAME:-kyverno}"
KYVERNO_CHART_VERSION="${KYVERNO_CHART_VERSION:-v3.7.1}"
ADMISSION_POLICY_TEMPLATE="${ADMISSION_POLICY_TEMPLATE:-$ROOT_DIR/deploy/policies/kyverno/clusterpolicies.yaml.tpl}"
RUN_POST_DEPLOY_API_HEALTH_CHECK="${RUN_POST_DEPLOY_API_HEALTH_CHECK:-1}"
POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS="${POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS:-120}"
RUN_POST_DEPLOY_SYNTHETIC_CHECK="${RUN_POST_DEPLOY_SYNTHETIC_CHECK:-1}"
SYNTHETIC_CHECK_USERNAME="${SYNTHETIC_CHECK_USERNAME:-admin}"
SYNTHETIC_CHECK_PASSWORD="${SYNTHETIC_CHECK_PASSWORD:-}"
SYNTHETIC_CHECK_TIMEOUT_SECONDS="${SYNTHETIC_CHECK_TIMEOUT_SECONDS:-420}"
SYNTHETIC_CHECK_REQUIRE_TEMPLATES="${SYNTHETIC_CHECK_REQUIRE_TEMPLATES:-0}"
ADMIN_BOOTSTRAP_PASSWORD="${ADMIN_BOOTSTRAP_PASSWORD:-}"

RENDERED_GOLDEN_HOSTPATH_MANIFEST=""
RENDERED_GOLDEN_PVC_MANIFEST=""
RENDERED_HELM_VALUES=""
ADMIN_BOOTSTRAP_PASSWORD_GENERATED=0
SYNTHETIC_CHECK_PASSWORD_AUTOSET=0
SYNTHETIC_CHECK_AUTO_DISABLED=0
ADMIN_BOOTSTRAP_SECRET_FILE=""

log() {
  echo "==> $*"
}

warn() {
  echo "WARNING: $*" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

generate_random_bootstrap_secret() {
  local generated
  generated="$(head -c 24 /dev/urandom | base64 | tr -d '\n=' | tr '/+' '_-')"
  printf '%s' "$generated"
}

persist_generated_bootstrap_secret() {
  local secret_file_dir secret_file_path
  secret_file_dir="${HOME}/.config/bretter-labs"
  mkdir -p "$secret_file_dir"
  chmod 700 "$secret_file_dir" >/dev/null 2>&1 || true
  secret_file_path="${secret_file_dir}/bootstrap-admin-$(date -u +%Y%m%dT%H%M%SZ).txt"
  umask 077
  cat >"$secret_file_path" <<EOF
username=admin
password=${ADMIN_BOOTSTRAP_PASSWORD}
generated_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  chmod 600 "$secret_file_path" >/dev/null 2>&1 || true
  ADMIN_BOOTSTRAP_SECRET_FILE="$secret_file_path"
}

configure_admin_bootstrap_credentials() {
  if [ -z "$ADMIN_BOOTSTRAP_PASSWORD" ]; then
    ADMIN_BOOTSTRAP_PASSWORD="$(generate_random_bootstrap_secret)"
    ADMIN_BOOTSTRAP_PASSWORD_GENERATED=1
    persist_generated_bootstrap_secret
  fi
  if [ "$RUN_POST_DEPLOY_SYNTHETIC_CHECK" -eq 1 ] && [ -z "$SYNTHETIC_CHECK_PASSWORD" ]; then
    if [ "$ADMIN_BOOTSTRAP_PASSWORD_GENERATED" -eq 1 ]; then
      RUN_POST_DEPLOY_SYNTHETIC_CHECK=0
      SYNTHETIC_CHECK_AUTO_DISABLED=1
    else
      SYNTHETIC_CHECK_PASSWORD="$ADMIN_BOOTSTRAP_PASSWORD"
      SYNTHETIC_CHECK_PASSWORD_AUTOSET=1
    fi
  fi
}

validate_public_scheme() {
  case "$PUBLIC_SCHEME" in
    https|http) ;;
    *) fail "PUBLIC_SCHEME must be either https or http." ;;
  esac
}

validate_tls_config() {
  if [ -z "$TLS_SECRET_NAME" ]; then
    fail "TLS_SECRET_NAME cannot be empty."
  fi
  if [ "$TLS_ENABLED" -ne 1 ] && [ "$PUBLIC_SCHEME" = "https" ]; then
    log "WARNING: PUBLIC_SCHEME=https with TLS_ENABLED=0. Ensure secret $TLS_SECRET_NAME already exists."
  fi
}

validate_preload_config() {
  case "$PRELOAD_RUNNER_ON_ALL_NODES" in
    0|1) ;;
    *) fail "PRELOAD_RUNNER_ON_ALL_NODES must be either 0 or 1." ;;
  esac
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_semver_tag() {
  [[ "$1" =~ ^v?[0-9]+(\.[0-9]+){2}$ ]]
}

phase_enabled() {
  local phase="$1"
  case ",${SETUP_PHASES}," in
    *",${phase},"*) return 0 ;;
    *) return 1 ;;
  esac
}

is_mutable_image_reference() {
  local ref="$1"
  local without_digest last_component tag
  if [[ "$ref" == *@sha256:* ]]; then
    return 1
  fi
  without_digest="${ref%%@*}"
  last_component="${without_digest##*/}"
  if [[ "$last_component" != *:* ]]; then
    return 0
  fi
  tag="${last_component##*:}"
  if [ -z "$tag" ]; then
    return 0
  fi
  if [ "${tag,,}" = "latest" ]; then
    return 0
  fi
  return 1
}

validate_longhorn_tuning_config() {
  case "$LONGHORN_TUNE" in
    0|1) ;;
    *) fail "LONGHORN_TUNE must be either 0 or 1." ;;
  esac

  if ! is_uint "$LONGHORN_VM_REPLICA_COUNT" || [ "$LONGHORN_VM_REPLICA_COUNT" -lt 1 ]; then
    fail "LONGHORN_VM_REPLICA_COUNT must be an integer >= 1."
  fi
  if ! is_uint "$LONGHORN_DEFAULT_REPLICA_COUNT" || [ "$LONGHORN_DEFAULT_REPLICA_COUNT" -lt 1 ]; then
    fail "LONGHORN_DEFAULT_REPLICA_COUNT must be an integer >= 1."
  fi
  if ! is_uint "$LONGHORN_RESERVED_PERCENT" || [ "$LONGHORN_RESERVED_PERCENT" -gt 100 ]; then
    fail "LONGHORN_RESERVED_PERCENT must be an integer between 0 and 100."
  fi
  if ! is_uint "$LONGHORN_MIN_AVAILABLE_PERCENT" || [ "$LONGHORN_MIN_AVAILABLE_PERCENT" -gt 100 ]; then
    fail "LONGHORN_MIN_AVAILABLE_PERCENT must be an integer between 0 and 100."
  fi
  if ! is_uint "$LONGHORN_OVERPROVISION_PERCENT" || [ "$LONGHORN_OVERPROVISION_PERCENT" -lt 1 ]; then
    fail "LONGHORN_OVERPROVISION_PERCENT must be an integer >= 1."
  fi
}

validate_autocleanup_config() {
  case "$ENABLE_AUTOCLEANUP" in
    0|1) ;;
    *) fail "ENABLE_AUTOCLEANUP must be either 0 or 1." ;;
  esac

  if [ "$ENABLE_AUTOCLEANUP" -eq 0 ]; then
    return
  fi
  if [ -z "$AUTOCLEANUP_SCHEDULE" ]; then
    fail "AUTOCLEANUP_SCHEDULE cannot be empty when ENABLE_AUTOCLEANUP=1."
  fi
  if ! is_uint "$AUTOCLEANUP_HELPER_MAX_AGE_MINUTES" || [ "$AUTOCLEANUP_HELPER_MAX_AGE_MINUTES" -lt 1 ]; then
    fail "AUTOCLEANUP_HELPER_MAX_AGE_MINUTES must be an integer >= 1."
  fi
  if ! is_uint "$AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES" || [ "$AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES" -lt 1 ]; then
    fail "AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES must be an integer >= 1."
  fi
  if ! is_uint "$AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES" || [ "$AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES" -lt 1 ]; then
    fail "AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES must be an integer >= 1."
  fi
  if ! is_uint "$AUTOCLEANUP_RESTART_ALERT_COUNT" || [ "$AUTOCLEANUP_RESTART_ALERT_COUNT" -lt 1 ]; then
    fail "AUTOCLEANUP_RESTART_ALERT_COUNT must be an integer >= 1."
  fi
  if ! is_uint "$AUTOCLEANUP_NODEFS_WARN_PCT" || [ "$AUTOCLEANUP_NODEFS_WARN_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_NODEFS_WARN_PCT must be an integer between 0 and 100."
  fi
  if ! is_uint "$AUTOCLEANUP_NODEFS_CRITICAL_PCT" || [ "$AUTOCLEANUP_NODEFS_CRITICAL_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_NODEFS_CRITICAL_PCT must be an integer between 0 and 100."
  fi
  if ! is_uint "$AUTOCLEANUP_NODEFS_EMERGENCY_PCT" || [ "$AUTOCLEANUP_NODEFS_EMERGENCY_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_NODEFS_EMERGENCY_PCT must be an integer between 0 and 100."
  fi
  if ! is_uint "$AUTOCLEANUP_PVC_WARN_PCT" || [ "$AUTOCLEANUP_PVC_WARN_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_PVC_WARN_PCT must be an integer between 0 and 100."
  fi
  if ! is_uint "$AUTOCLEANUP_PVC_CRITICAL_PCT" || [ "$AUTOCLEANUP_PVC_CRITICAL_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_PVC_CRITICAL_PCT must be an integer between 0 and 100."
  fi
  if ! is_uint "$AUTOCLEANUP_PVC_EMERGENCY_PCT" || [ "$AUTOCLEANUP_PVC_EMERGENCY_PCT" -gt 100 ]; then
    fail "AUTOCLEANUP_PVC_EMERGENCY_PCT must be an integer between 0 and 100."
  fi
  if [ "$AUTOCLEANUP_NODEFS_WARN_PCT" -gt "$AUTOCLEANUP_NODEFS_CRITICAL_PCT" ] || \
     [ "$AUTOCLEANUP_NODEFS_CRITICAL_PCT" -gt "$AUTOCLEANUP_NODEFS_EMERGENCY_PCT" ]; then
    fail "Nodefs alert thresholds must be non-decreasing (warn <= critical <= emergency)."
  fi
  if [ "$AUTOCLEANUP_PVC_WARN_PCT" -gt "$AUTOCLEANUP_PVC_CRITICAL_PCT" ] || \
     [ "$AUTOCLEANUP_PVC_CRITICAL_PCT" -gt "$AUTOCLEANUP_PVC_EMERGENCY_PCT" ]; then
    fail "PVC alert thresholds must be non-decreasing (warn <= critical <= emergency)."
  fi
}

validate_kubelet_serving_csr_autoapproval_config() {
  case "$ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL" in
    0|1) ;;
    *) fail "ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL must be either 0 or 1." ;;
  esac
  if [ "$ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL" -eq 0 ]; then
    return
  fi
  if [ -z "$KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE" ]; then
    fail "KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE cannot be empty when ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL=1."
  fi
}

validate_setup_phase_config() {
  case "$SETUP_DRY_RUN" in
    0|1) ;;
    *) fail "SETUP_DRY_RUN must be either 0 or 1." ;;
  esac

  local raw phase deduped
  local -a phases
  raw="$(printf '%s' "$SETUP_PHASES" | tr -d '[:space:]')"
  [ -n "$raw" ] || fail "SETUP_PHASES cannot be empty."
  if [ "$raw" = "all" ]; then
    SETUP_PHASES="prereqs,deploy,postdeploy"
    return
  fi

  deduped=""
  IFS=',' read -r -a phases <<<"$raw"
  for phase in "${phases[@]}"; do
    case "$phase" in
      prereqs|deploy|postdeploy) ;;
      *) fail "Unsupported setup phase: ${phase}. Allowed values: prereqs,deploy,postdeploy (or all)." ;;
    esac
    case ",${deduped}," in
      *",${phase},"*) continue ;;
      *) deduped="${deduped}${deduped:+,}${phase}" ;;
    esac
  done
  [ -n "$deduped" ] || fail "SETUP_PHASES resolved to an empty phase set."
  SETUP_PHASES="$deduped"
}

validate_image_reference_policy() {
  case "$ALLOW_MUTABLE_IMAGE_TAGS" in
    0|1) ;;
    *) fail "ALLOW_MUTABLE_IMAGE_TAGS must be either 0 or 1." ;;
  esac

  if [ "$ALLOW_MUTABLE_IMAGE_TAGS" -eq 1 ]; then
    warn "Mutable image references are allowed (ALLOW_MUTABLE_IMAGE_TAGS=1). This is not recommended for production."
    return
  fi

  local invalid_refs=()
  local image_var image_ref
  for image_var in BACKEND_IMAGE FRONTEND_IMAGE RUNNER_IMAGE; do
    image_ref="${!image_var}"
    if is_mutable_image_reference "$image_ref"; then
      invalid_refs+=("${image_var}=${image_ref}")
    fi
  done
  if [ "${#invalid_refs[@]}" -gt 0 ]; then
    fail "Mutable image references are not allowed: ${invalid_refs[*]}. Use immutable tags or digests, or set ALLOW_MUTABLE_IMAGE_TAGS=1 for dev-only workflows."
  fi
}

validate_storage_guard_config() {
  if ! is_uint "$SETUP_MIN_FREE_GIB" || [ "$SETUP_MIN_FREE_GIB" -lt 1 ]; then
    fail "SETUP_MIN_FREE_GIB must be an integer >= 1."
  fi
  if ! is_uint "$SETUP_WARN_FREE_GIB" || [ "$SETUP_WARN_FREE_GIB" -lt 1 ]; then
    fail "SETUP_WARN_FREE_GIB must be an integer >= 1."
  fi
  if [ "$SETUP_WARN_FREE_GIB" -lt "$SETUP_MIN_FREE_GIB" ]; then
    fail "SETUP_WARN_FREE_GIB must be >= SETUP_MIN_FREE_GIB."
  fi
}

validate_vm_network_config() {
  case "$VM_NET_BACKEND" in
    tap-nat|user) ;;
    *) fail "VM_NET_BACKEND must be either tap-nat or user." ;;
  esac
  case "$VM_RUNNER_PRIVILEGED" in
    0|1) ;;
    *) fail "VM_RUNNER_PRIVILEGED must be either 0 or 1." ;;
  esac
  case "${VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY}" in
    Local|Cluster|local|cluster) ;;
    *) fail "VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY must be Local or Cluster." ;;
  esac
  if ! is_uint "$VM_CONSOLE_TICKET_LENGTH" || [ "$VM_CONSOLE_TICKET_LENGTH" -lt 12 ] || [ "$VM_CONSOLE_TICKET_LENGTH" -gt 64 ]; then
    fail "VM_CONSOLE_TICKET_LENGTH must be an integer between 12 and 64."
  fi
  case "$BACKEND_NODEPORT_ENABLED" in
    0|1) ;;
    *) fail "BACKEND_NODEPORT_ENABLED must be either 0 or 1." ;;
  esac
  if [ "$BACKEND_NODEPORT_ENABLED" -eq 1 ]; then
    if ! is_uint "$BACKEND_NODEPORT" || [ "$BACKEND_NODEPORT" -lt 30000 ] || [ "$BACKEND_NODEPORT" -gt 32767 ]; then
      fail "BACKEND_NODEPORT must be a valid NodePort in 30000-32767 when BACKEND_NODEPORT_ENABLED=1."
    fi
  fi
  case "$VM_CONNECT_INSECURE_TLS" in
    0|1) ;;
    *) fail "VM_CONNECT_INSECURE_TLS must be either 0 or 1." ;;
  esac
}

validate_container_runtime_config() {
  case "$CONTAINER_INGRESS_ENABLED" in
    0|1) ;;
    *) fail "CONTAINER_INGRESS_ENABLED must be either 0 or 1." ;;
  esac
  case "$CONTAINER_IMAGE_PREPULL_ENABLED" in
    0|1) ;;
    *) fail "CONTAINER_IMAGE_PREPULL_ENABLED must be either 0 or 1." ;;
  esac
  if [ "$CONTAINER_INGRESS_ENABLED" -eq 1 ] && [ -z "$CONTAINER_INGRESS_BASE_DOMAIN" ]; then
    fail "CONTAINER_INGRESS_BASE_DOMAIN is required when CONTAINER_INGRESS_ENABLED=1."
  fi
  if ! is_uint "$CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS" || [ "$CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS" -lt 10 ]; then
    fail "CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS must be an integer >= 10."
  fi
  case "$CONTAINER_SIGNATURE_VERIFICATION_ENABLED" in
    0|1) ;;
    *) fail "CONTAINER_SIGNATURE_VERIFICATION_ENABLED must be either 0 or 1." ;;
  esac
  case "$CONTAINER_SCAN_ENABLED" in
    0|1) ;;
    *) fail "CONTAINER_SCAN_ENABLED must be either 0 or 1." ;;
  esac
  case "$CONTAINER_START_QUEUE_ENABLED" in
    0|1) ;;
    *) fail "CONTAINER_START_QUEUE_ENABLED must be either 0 or 1." ;;
  esac
  if [ -z "$CONTAINER_ALLOWED_REGISTRIES" ]; then
    fail "CONTAINER_ALLOWED_REGISTRIES cannot be empty."
  fi
  if ! is_uint "$CONTAINER_SCAN_INTERVAL_MINUTES" || [ "$CONTAINER_SCAN_INTERVAL_MINUTES" -lt 15 ]; then
    fail "CONTAINER_SCAN_INTERVAL_MINUTES must be an integer >= 15."
  fi
  if [ -z "$CONTAINER_SCAN_SEVERITY" ]; then
    fail "CONTAINER_SCAN_SEVERITY cannot be empty."
  fi
  if ! is_uint "$CONTAINER_START_QUEUE_BASE_DELAY_SECONDS" || [ "$CONTAINER_START_QUEUE_BASE_DELAY_SECONDS" -lt 5 ]; then
    fail "CONTAINER_START_QUEUE_BASE_DELAY_SECONDS must be an integer >= 5."
  fi
  if ! is_uint "$CONTAINER_START_QUEUE_MAX_DELAY_SECONDS" || [ "$CONTAINER_START_QUEUE_MAX_DELAY_SECONDS" -lt "$CONTAINER_START_QUEUE_BASE_DELAY_SECONDS" ]; then
    fail "CONTAINER_START_QUEUE_MAX_DELAY_SECONDS must be >= CONTAINER_START_QUEUE_BASE_DELAY_SECONDS."
  fi
  case "$CONTAINER_CONNECT_INSECURE_TLS" in
    0|1) ;;
    *) fail "CONTAINER_CONNECT_INSECURE_TLS must be either 0 or 1." ;;
  esac
}

validate_auth_and_cors_config() {
  local cors_origins_lower secrets_key_lower
  case "$PRODUCTION_PROFILE" in
    0|1) ;;
    *) fail "PRODUCTION_PROFILE must be either 0 or 1." ;;
  esac
  case "$CORS_ENTERPRISE_PROFILE" in
    0|1) ;;
    *) fail "CORS_ENTERPRISE_PROFILE must be either 0 or 1." ;;
  esac
  if [ "$CORS_ENTERPRISE_PROFILE" -eq 1 ] && [ -z "$CORS_ALLOWED_ORIGINS" ]; then
    fail "CORS_ALLOWED_ORIGINS must be set when CORS_ENTERPRISE_PROFILE=1."
  fi
  if ! is_uint "$AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS" || [ "$AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS" -lt 10 ]; then
    fail "AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS must be an integer >= 10."
  fi
  if ! is_uint "$AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS" || [ "$AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS" -lt 1 ]; then
    fail "AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS must be an integer >= 1."
  fi
  if ! is_uint "$AUTH_LOGIN_LOCKOUT_SECONDS" || [ "$AUTH_LOGIN_LOCKOUT_SECONDS" -lt 10 ]; then
    fail "AUTH_LOGIN_LOCKOUT_SECONDS must be an integer >= 10."
  fi
  case "$PRUNE_BOOTSTRAP_ADMIN_ENV" in
    0|1) ;;
    *) fail "PRUNE_BOOTSTRAP_ADMIN_ENV must be either 0 or 1." ;;
  esac
  if [ "$PRODUCTION_PROFILE" -eq 1 ]; then
    if [ "$PUBLIC_SCHEME" != "https" ]; then
      fail "PUBLIC_SCHEME must be https when PRODUCTION_PROFILE=1."
    fi
    if [ "$CORS_ENTERPRISE_PROFILE" -ne 1 ]; then
      fail "CORS_ENTERPRISE_PROFILE must be 1 when PRODUCTION_PROFILE=1."
    fi
    if [[ "$CORS_ALLOWED_METHODS" == *"*"* ]]; then
      fail "CORS_ALLOWED_METHODS cannot include wildcard '*' when PRODUCTION_PROFILE=1."
    fi
    if [[ "$CORS_ALLOWED_HEADERS" == *"*"* ]]; then
      fail "CORS_ALLOWED_HEADERS cannot include wildcard '*' when PRODUCTION_PROFILE=1."
    fi
    cors_origins_lower="${CORS_ALLOWED_ORIGINS,,}"
    if [[ "$cors_origins_lower" == *"localhost"* || "$cors_origins_lower" == *"127.0.0.1"* ]]; then
      fail "CORS_ALLOWED_ORIGINS cannot include localhost/127.0.0.1 when PRODUCTION_PROFILE=1."
    fi
    if [ "$PRUNE_BOOTSTRAP_ADMIN_ENV" -ne 1 ]; then
      fail "PRUNE_BOOTSTRAP_ADMIN_ENV must be 1 when PRODUCTION_PROFILE=1."
    fi
    if [ "$VM_CONNECT_INSECURE_TLS" -ne 0 ] || [ "$CONTAINER_CONNECT_INSECURE_TLS" -ne 0 ]; then
      fail "VM/CONTAINER_CONNECT_INSECURE_TLS must be 0 when PRODUCTION_PROFILE=1."
    fi
    if [ -z "$RUNNER_NODE_SELECTOR_VALUE" ]; then
      fail "RUNNER_NODE_SELECTOR_VALUE must be set when PRODUCTION_PROFILE=1."
    fi
    if [ -z "$SECRETS_ENCRYPTION_KEY" ]; then
      fail "SECRETS_ENCRYPTION_KEY must be set when PRODUCTION_PROFILE=1."
    fi
    if [ "${#SECRETS_ENCRYPTION_KEY}" -lt 24 ]; then
      fail "SECRETS_ENCRYPTION_KEY must be at least 24 characters when PRODUCTION_PROFILE=1."
    fi
    secrets_key_lower="${SECRETS_ENCRYPTION_KEY,,}"
    case "$secrets_key_lower" in
      admin|password|changeme|admin123|secret|default)
        fail "SECRETS_ENCRYPTION_KEY uses a weak value; set a strong key for production."
        ;;
    esac
  fi
}

validate_postgres_config() {
  case "$USE_EXTERNAL_SECRETS" in
    0|1) ;;
    *) fail "USE_EXTERNAL_SECRETS must be either 0 or 1." ;;
  esac
  [ -n "$POSTGRES_USER" ] || fail "POSTGRES_USER cannot be empty."
  [ -n "$POSTGRES_PASSWORD" ] || fail "POSTGRES_PASSWORD cannot be empty."
  [ -n "$POSTGRES_DB" ] || fail "POSTGRES_DB cannot be empty."
}

validate_external_secrets_config() {
  case "$INSTALL_EXTERNAL_SECRETS_OPERATOR" in
    0|1) ;;
    *) fail "INSTALL_EXTERNAL_SECRETS_OPERATOR must be either 0 or 1." ;;
  esac
  case "$CREATE_VAULT_CLUSTER_SECRET_STORE" in
    0|1) ;;
    *) fail "CREATE_VAULT_CLUSTER_SECRET_STORE must be either 0 or 1." ;;
  esac
  case "$EXTERNAL_PULL_SECRET_ENABLED" in
    0|1) ;;
    *) fail "EXTERNAL_PULL_SECRET_ENABLED must be either 0 or 1." ;;
  esac
  if ! is_uint "$EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS" || [ "$EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS" -lt 30 ]; then
    fail "EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS must be an integer >= 30."
  fi
  if [ "$INSTALL_EXTERNAL_SECRETS_OPERATOR" -eq 1 ] && ! is_semver_tag "$EXTERNAL_SECRETS_CHART_VERSION"; then
    fail "EXTERNAL_SECRETS_CHART_VERSION must look like X.Y.Z."
  fi
  if [ "$USE_EXTERNAL_SECRETS" -ne 1 ]; then
    return
  fi
  [ -n "$EXTERNAL_SECRETS_STORE_NAME" ] || fail "EXTERNAL_SECRETS_STORE_NAME cannot be empty when USE_EXTERNAL_SECRETS=1."
  [ -n "$EXTERNAL_POSTGRES_REMOTE_KEY" ] || fail "EXTERNAL_POSTGRES_REMOTE_KEY cannot be empty when USE_EXTERNAL_SECRETS=1."
  [ -n "$EXTERNAL_POSTGRES_USER_PROPERTY" ] || fail "EXTERNAL_POSTGRES_USER_PROPERTY cannot be empty when USE_EXTERNAL_SECRETS=1."
  [ -n "$EXTERNAL_POSTGRES_PASSWORD_PROPERTY" ] || fail "EXTERNAL_POSTGRES_PASSWORD_PROPERTY cannot be empty when USE_EXTERNAL_SECRETS=1."
  [ -n "$EXTERNAL_POSTGRES_DB_PROPERTY" ] || fail "EXTERNAL_POSTGRES_DB_PROPERTY cannot be empty when USE_EXTERNAL_SECRETS=1."
  if [ "$EXTERNAL_PULL_SECRET_ENABLED" -eq 1 ] && [ -z "$EXTERNAL_PULL_SECRET_REMOTE_KEY" ]; then
    fail "EXTERNAL_PULL_SECRET_REMOTE_KEY cannot be empty when EXTERNAL_PULL_SECRET_ENABLED=1."
  fi
  if [ "$CREATE_VAULT_CLUSTER_SECRET_STORE" -eq 1 ]; then
    [ -n "$VAULT_ADDR" ] || fail "VAULT_ADDR cannot be empty when CREATE_VAULT_CLUSTER_SECRET_STORE=1."
    [ -n "$VAULT_K8S_AUTH_PATH" ] || fail "VAULT_K8S_AUTH_PATH cannot be empty when CREATE_VAULT_CLUSTER_SECRET_STORE=1."
    [ -n "$VAULT_K8S_ROLE" ] || fail "VAULT_K8S_ROLE cannot be empty when CREATE_VAULT_CLUSTER_SECRET_STORE=1."
    [ -n "$VAULT_KV_MOUNT" ] || fail "VAULT_KV_MOUNT cannot be empty when CREATE_VAULT_CLUSTER_SECRET_STORE=1."
    case "$VAULT_KV_VERSION" in
      v1|v2) ;;
      *) fail "VAULT_KV_VERSION must be v1 or v2." ;;
    esac
  fi
}

validate_cdi_upload_config() {
  case "$INSTALL_CDI" in
    0|1) ;;
    *) fail "INSTALL_CDI must be either 0 or 1." ;;
  esac
  [ -n "$CDI_VERSION" ] || fail "CDI_VERSION cannot be empty."
  if ! is_uint "$CDI_UPLOAD_NODEPORT" || [ "$CDI_UPLOAD_NODEPORT" -lt 30000 ] || [ "$CDI_UPLOAD_NODEPORT" -gt 32767 ]; then
    fail "CDI_UPLOAD_NODEPORT must be a valid NodePort in 30000-32767."
  fi
}

validate_cpu_manager_config() {
  case "$CPU_MANAGER_STATIC" in
    0|1) ;;
    *) fail "CPU_MANAGER_STATIC must be either 0 or 1." ;;
  esac
}

validate_monitoring_config() {
  case "$ENABLE_MONITORING" in
    0|1) ;;
    *) fail "ENABLE_MONITORING must be either 0 or 1." ;;
  esac
  if [ "$ENABLE_MONITORING" -eq 0 ]; then
    return
  fi

  [ -n "$MONITORING_NAMESPACE" ] || fail "MONITORING_NAMESPACE cannot be empty when ENABLE_MONITORING=1."
  [ -n "$MONITORING_RELEASE_NAME" ] || fail "MONITORING_RELEASE_NAME cannot be empty when ENABLE_MONITORING=1."
  if ! is_semver_tag "$MONITORING_CHART_VERSION"; then
    fail "MONITORING_CHART_VERSION must look like X.Y.Z."
  fi
  if ! is_uint "$MONITORING_RESTART_ALERT_COUNT" || [ "$MONITORING_RESTART_ALERT_COUNT" -lt 1 ]; then
    fail "MONITORING_RESTART_ALERT_COUNT must be an integer >= 1."
  fi
  if ! is_uint "$MONITORING_DV_STALE_MINUTES" || [ "$MONITORING_DV_STALE_MINUTES" -lt 1 ]; then
    fail "MONITORING_DV_STALE_MINUTES must be an integer >= 1."
  fi
  if ! is_uint "$MONITORING_WARM_POOL_MIN_READY"; then
    fail "MONITORING_WARM_POOL_MIN_READY must be an integer >= 0."
  fi
  if [ "$MONITORING_WARM_POOL_MIN_READY" -lt 0 ]; then
    fail "MONITORING_WARM_POOL_MIN_READY must be an integer >= 0."
  fi
  if [ -z "$HELM_VERSION" ]; then
    fail "HELM_VERSION cannot be empty when ENABLE_MONITORING=1."
  fi
}

validate_metrics_server_config() {
  case "$ENABLE_METRICS_SERVER" in
    0|1) ;;
    *) fail "ENABLE_METRICS_SERVER must be either 0 or 1." ;;
  esac
  case "$METRICS_SERVER_INSECURE_TLS" in
    0|1) ;;
    *) fail "METRICS_SERVER_INSECURE_TLS must be either 0 or 1." ;;
  esac
  if ! is_semver_tag "$METRICS_SERVER_VERSION"; then
    fail "METRICS_SERVER_VERSION must look like X.Y.Z."
  fi
  if [ "$ENABLE_METRICS_SERVER" -eq 1 ] && [ -z "$METRICS_SERVER_MANIFEST_URL" ]; then
    fail "METRICS_SERVER_MANIFEST_URL cannot be empty when ENABLE_METRICS_SERVER=1."
  fi
}

validate_admission_policy_config() {
  case "$ENABLE_ADMISSION_POLICIES" in
    0|1) ;;
    *) fail "ENABLE_ADMISSION_POLICIES must be either 0 or 1." ;;
  esac
  case "$INSTALL_KYVERNO" in
    0|1) ;;
    *) fail "INSTALL_KYVERNO must be either 0 or 1." ;;
  esac
  if [ "$ENABLE_ADMISSION_POLICIES" -eq 0 ]; then
    return
  fi
  [ -n "$KYVERNO_NAMESPACE" ] || fail "KYVERNO_NAMESPACE cannot be empty when ENABLE_ADMISSION_POLICIES=1."
  [ -n "$KYVERNO_RELEASE_NAME" ] || fail "KYVERNO_RELEASE_NAME cannot be empty when ENABLE_ADMISSION_POLICIES=1."
  if ! is_semver_tag "$KYVERNO_CHART_VERSION"; then
    fail "KYVERNO_CHART_VERSION must look like X.Y.Z."
  fi
  [ -n "$ADMISSION_POLICY_TEMPLATE" ] || fail "ADMISSION_POLICY_TEMPLATE cannot be empty when ENABLE_ADMISSION_POLICIES=1."
  [ -f "$ADMISSION_POLICY_TEMPLATE" ] || fail "ADMISSION_POLICY_TEMPLATE does not exist: $ADMISSION_POLICY_TEMPLATE"
}

validate_post_deploy_api_health_config() {
  case "$RUN_POST_DEPLOY_API_HEALTH_CHECK" in
    0|1) ;;
    *) fail "RUN_POST_DEPLOY_API_HEALTH_CHECK must be either 0 or 1." ;;
  esac
  if [ "$RUN_POST_DEPLOY_API_HEALTH_CHECK" -eq 0 ]; then
    return
  fi
  if ! is_uint "$POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS" || [ "$POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS" -lt 10 ]; then
    fail "POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS must be an integer >= 10."
  fi
}

validate_synthetic_check_config() {
  case "$RUN_POST_DEPLOY_SYNTHETIC_CHECK" in
    0|1) ;;
    *) fail "RUN_POST_DEPLOY_SYNTHETIC_CHECK must be either 0 or 1." ;;
  esac
  case "$SYNTHETIC_CHECK_REQUIRE_TEMPLATES" in
    0|1) ;;
    *) fail "SYNTHETIC_CHECK_REQUIRE_TEMPLATES must be either 0 or 1." ;;
  esac
  if [ "$RUN_POST_DEPLOY_SYNTHETIC_CHECK" -eq 0 ]; then
    return
  fi
  [ -n "$SYNTHETIC_CHECK_USERNAME" ] || fail "SYNTHETIC_CHECK_USERNAME cannot be empty when synthetic checks are enabled."
  [ -n "$SYNTHETIC_CHECK_PASSWORD" ] || fail "SYNTHETIC_CHECK_PASSWORD cannot be empty when synthetic checks are enabled."
  if ! is_uint "$SYNTHETIC_CHECK_TIMEOUT_SECONDS" || [ "$SYNTHETIC_CHECK_TIMEOUT_SECONDS" -lt 60 ]; then
    fail "SYNTHETIC_CHECK_TIMEOUT_SECONDS must be an integer >= 60."
  fi
}

validate_helm_deploy_config() {
  [ -n "$HELM_RELEASE_NAME" ] || fail "HELM_RELEASE_NAME cannot be empty."
  [ -n "$HELM_CHART_DIR" ] || fail "HELM_CHART_DIR cannot be empty."
  [ -d "$HELM_CHART_DIR" ] || fail "HELM_CHART_DIR does not exist: $HELM_CHART_DIR"
  [ -f "$HELM_CHART_DIR/Chart.yaml" ] || fail "Helm chart is missing Chart.yaml under $HELM_CHART_DIR"
  [ -n "$HELM_VERSION" ] || fail "HELM_VERSION cannot be empty."
}

sudo_cmd() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

require_apt() {
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get is required (this script supports Debian/Ubuntu)."
  fi
}

cleanup() {
  rm -f \
    "${RENDERED_GOLDEN_HOSTPATH_MANIFEST:-}" \
    "${RENDERED_GOLDEN_PVC_MANIFEST:-}" \
    "${RENDERED_HELM_VALUES:-}"
}

trap cleanup EXIT

install_base_packages() {
  log "Installing base packages..."
  sudo_cmd apt-get update -y
  sudo_cmd apt-get install -y ca-certificates curl gnupg lsb-release git python3 python3-venv python3-pip openssl
}

install_node() {
  local need_node=1
  if command -v node >/dev/null 2>&1; then
    local major
    major="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
    if [ "${major:-0}" -ge 18 ]; then
      need_node=0
    fi
  fi
  if [ "$need_node" -eq 1 ]; then
    log "Installing Node.js 20..."
    if [ "$(id -u)" -eq 0 ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    else
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    fi
    sudo_cmd apt-get install -y nodejs
  fi
}

install_kubectl() {
  if command -v kubectl >/dev/null 2>&1; then
    return
  fi
  log "Installing kubectl..."
  sudo_cmd apt-get update -y
  sudo_cmd apt-get install -y apt-transport-https
  sudo_cmd mkdir -p /etc/apt/keyrings
  curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.31/deb/Release.key \
    | sudo_cmd gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
  echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.31/deb/ /" \
    | sudo_cmd tee /etc/apt/sources.list.d/kubernetes.list >/dev/null
  sudo_cmd apt-get update -y
  sudo_cmd apt-get install -y kubectl
}

install_helm() {
  if command -v helm >/dev/null 2>&1; then
    return
  fi

  local arch helm_arch tmp_dir
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64)
      helm_arch="amd64"
      ;;
    aarch64|arm64)
      helm_arch="arm64"
      ;;
    *)
      fail "Unsupported CPU architecture for helm install: $arch"
      ;;
  esac

  tmp_dir="$(mktemp -d /tmp/helm-install.XXXXXX)"
  log "Installing helm ${HELM_VERSION}..."
  curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-${helm_arch}.tar.gz" -o "$tmp_dir/helm.tgz"
  tar -xzf "$tmp_dir/helm.tgz" -C "$tmp_dir"
  sudo_cmd install -m 0755 "$tmp_dir/linux-${helm_arch}/helm" /usr/local/bin/helm
  rm -rf "$tmp_dir"
}

install_podman() {
  if command -v podman >/dev/null 2>&1; then
    return
  fi
  log "Installing podman..."
  sudo_cmd apt-get update -y
  sudo_cmd apt-get install -y podman
}

ensure_kubeconfig() {
  if [ -n "$KUBECONFIG_PATH" ]; then
    export KUBECONFIG="$KUBECONFIG_PATH"
  elif [ -z "${KUBECONFIG:-}" ]; then
    if [ -f "$HOME/.kube/config" ]; then
      export KUBECONFIG="$HOME/.kube/config"
    elif [ -r /etc/kubernetes/admin.conf ]; then
      export KUBECONFIG=/etc/kubernetes/admin.conf
    fi
  fi
  if ! kubectl version --client >/dev/null 2>&1; then
    fail "kubectl is not working. Check your PATH or installation."
  fi
  if ! kubectl get ns >/dev/null 2>&1; then
    fail "kubectl cannot reach a cluster. Ensure KUBECONFIG is set correctly."
  fi
}

longhorn_available() {
  kubectl -n longhorn-system get settings.longhorn.io default-replica-count >/dev/null 2>&1
}

patch_longhorn_setting() {
  local name="$1"
  local value="$2"

  if ! kubectl -n longhorn-system get settings.longhorn.io "$name" >/dev/null 2>&1; then
    log "Longhorn setting $name not found; skipping."
    return
  fi
  kubectl -n longhorn-system patch settings.longhorn.io "$name" --type=merge -p "{\"value\":\"$value\"}" >/dev/null
}

sync_longhorn_reserved_capacity() {
  local node disk reserved payload
  while IFS=$'\t' read -r node disk reserved; do
    [ -n "$node" ] || continue
    [ -n "$disk" ] || continue
    [ -n "$reserved" ] || continue
    payload="$(printf '{"spec":{"disks":{"%s":{"storageReserved":%s}}}}' "$disk" "$reserved")"
    if ! kubectl -n longhorn-system patch nodes.longhorn.io "$node" --type=merge -p "$payload" >/dev/null 2>&1; then
      log "WARNING: failed to update Longhorn reserved bytes on node=$node disk=$disk"
    fi
  done < <(
    python3 - "$LONGHORN_RESERVED_PERCENT" <<'PY'
import json
import subprocess
import sys

reserve_percent = int(sys.argv[1])
raw = subprocess.check_output(
    ["kubectl", "-n", "longhorn-system", "get", "nodes.longhorn.io", "-o", "json"],
    text=True,
)
data = json.loads(raw)
for item in data.get("items", []):
    node_name = item.get("metadata", {}).get("name")
    disk_status = item.get("status", {}).get("diskStatus", {})
    for disk_name, disk in disk_status.items():
        max_bytes = int(disk.get("storageMaximum") or 0)
        if max_bytes <= 0:
            continue
        reserved = max_bytes * reserve_percent // 100
        print(f"{node_name}\t{disk_name}\t{reserved}")
PY
  )
}

ensure_longhorn_vm_storage_class() {
  if kubectl get storageclass "$LONGHORN_VM_STORAGE_CLASS" >/dev/null 2>&1; then
    local provisioner existing_replicas
    provisioner="$(kubectl get storageclass "$LONGHORN_VM_STORAGE_CLASS" -o jsonpath='{.provisioner}' 2>/dev/null || true)"
    if [ "$provisioner" != "driver.longhorn.io" ]; then
      fail "StorageClass $LONGHORN_VM_STORAGE_CLASS exists but is not managed by Longhorn."
    fi
    existing_replicas="$(kubectl get storageclass "$LONGHORN_VM_STORAGE_CLASS" -o jsonpath='{.parameters.numberOfReplicas}' 2>/dev/null || true)"
    if [ -n "$existing_replicas" ] && [ "$existing_replicas" != "$LONGHORN_VM_REPLICA_COUNT" ]; then
      log "Longhorn VM StorageClass $LONGHORN_VM_STORAGE_CLASS already exists with numberOfReplicas=$existing_replicas; keeping existing definition."
    fi
    return
  fi

  kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ${LONGHORN_VM_STORAGE_CLASS}
provisioner: driver.longhorn.io
allowVolumeExpansion: true
reclaimPolicy: Delete
volumeBindingMode: Immediate
parameters:
  numberOfReplicas: "${LONGHORN_VM_REPLICA_COUNT}"
  staleReplicaTimeout: "30"
  fromBackup: ""
  fsType: ext4
  dataLocality: disabled
  unmapMarkSnapChainRemoved: ignored
EOF
}

tune_longhorn_for_phase2() {
  if [ "$LONGHORN_TUNE" -ne 1 ]; then
    return
  fi
  if ! longhorn_available; then
    log "Longhorn not detected; skipping Longhorn phase-2 tuning."
    return
  fi

  log "Applying Longhorn phase-2 defaults..."
  patch_longhorn_setting "default-replica-count" "$LONGHORN_DEFAULT_REPLICA_COUNT"
  patch_longhorn_setting "storage-reserved-percentage-for-default-disk" "$LONGHORN_RESERVED_PERCENT"
  patch_longhorn_setting "storage-minimal-available-percentage" "$LONGHORN_MIN_AVAILABLE_PERCENT"
  patch_longhorn_setting "storage-over-provisioning-percentage" "$LONGHORN_OVERPROVISION_PERCENT"
  if [ -n "$LONGHORN_DEFAULT_DATA_PATH" ]; then
    patch_longhorn_setting "default-data-path" "$LONGHORN_DEFAULT_DATA_PATH"
  fi
  sync_longhorn_reserved_capacity
  ensure_longhorn_vm_storage_class

  if [ -z "$VM_STORAGE_CLASS" ]; then
    VM_STORAGE_CLASS="$LONGHORN_VM_STORAGE_CLASS"
    log "VM_STORAGE_CLASS not set; defaulting to $VM_STORAGE_CLASS for clone-based VM disks."
  fi
}

ensure_cdi_installed() {
  if kubectl get crd datavolumes.cdi.kubevirt.io >/dev/null 2>&1 && \
     kubectl api-resources --api-group=upload.cdi.kubevirt.io 2>/dev/null | awk '{print $1}' | grep -qx "uploadtokenrequests"; then
    return
  fi
  if [ "$INSTALL_CDI" -ne 1 ]; then
    log "CDI CRDs not found and INSTALL_CDI=0; direct CDI uploads will remain disabled."
    return
  fi

  log "Installing CDI ${CDI_VERSION}..."
  kubectl apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/${CDI_VERSION}/cdi-operator.yaml"
  kubectl apply -f "https://github.com/kubevirt/containerized-data-importer/releases/download/${CDI_VERSION}/cdi-cr.yaml"

  kubectl wait --for=condition=Established crd/datavolumes.cdi.kubevirt.io --timeout=300s
  kubectl -n "$CDI_NAMESPACE" rollout status deployment/cdi-operator --timeout=300s >/dev/null 2>&1 || true
  kubectl -n "$CDI_NAMESPACE" wait --for=condition=Available deployment --all --timeout=600s >/dev/null 2>&1 || true
  local attempts
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    if kubectl api-resources --api-group=upload.cdi.kubevirt.io 2>/dev/null | awk '{print $1}' | grep -qx "uploadtokenrequests"; then
      return
    fi
    sleep 2
    attempts=$((attempts + 1))
  done
  fail "CDI upload token API did not become available."
}

ensure_cdi_uploadproxy_nodeport() {
  if ! kubectl -n "$CDI_NAMESPACE" get svc cdi-uploadproxy >/dev/null 2>&1; then
    return
  fi
  python3 - "$CDI_NAMESPACE" "$CDI_UPLOAD_NODEPORT" <<'PY' | kubectl apply -f -
import json
import subprocess
import sys

namespace = sys.argv[1]
node_port = int(sys.argv[2])
svc = json.loads(
    subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "svc", "cdi-uploadproxy", "-o", "json"],
        text=True,
    )
)
selector = svc.get("spec", {}).get("selector") or {}
ports = svc.get("spec", {}).get("ports") or []
target_port = 443
if ports:
    target_port = ports[0].get("targetPort") or ports[0].get("port") or 443
print("apiVersion: v1")
print("kind: Service")
print("metadata:")
print("  name: bretter-cdi-uploadproxy")
print(f"  namespace: {namespace}")
print("spec:")
print("  type: NodePort")
if selector:
    print("  selector:")
    for k, v in selector.items():
        print(f"    {k}: {v}")
else:
    print("  selector: {}")
print("  ports:")
print("    - name: https")
print("      protocol: TCP")
print("      port: 443")
print(f"      targetPort: {target_port}")
print(f"      nodePort: {node_port}")
PY
}

configure_cdi_upload_proxy_url() {
  if kubectl -n "$CDI_NAMESPACE" get svc cdi-uploadproxy >/dev/null 2>&1; then
    ensure_cdi_uploadproxy_nodeport
  fi
  if [ -n "$CDI_UPLOAD_PROXY_URL" ]; then
    return
  fi
  if ! kubectl -n "$CDI_NAMESPACE" get svc cdi-uploadproxy >/dev/null 2>&1; then
    log "CDI uploadproxy service not detected; direct CDI upload URL will be disabled."
    return
  fi
  log "Ensuring CDI uploadproxy NodePort service in namespace $CDI_NAMESPACE..."
  ensure_cdi_uploadproxy_nodeport
  CDI_UPLOAD_PROXY_URL="https://${NODE_EXTERNAL_HOST}:${CDI_UPLOAD_NODEPORT}"
}

install_monitoring_stack() {
  if [ "$ENABLE_MONITORING" -ne 1 ]; then
    log "Skipping monitoring stack install (ENABLE_MONITORING=0)."
    return
  fi

  install_helm
  log "Installing kube-prometheus-stack in namespace ${MONITORING_NAMESPACE}..."
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update >/dev/null

  local values_file
  values_file="$(mktemp /tmp/bretter-monitoring-values.XXXXXX.yaml)"
  cat >"$values_file" <<EOF
grafana:
  enabled: true
  defaultDashboardsEnabled: true
alertmanager:
  enabled: true
kubeEtcd:
  enabled: false
kubeControllerManager:
  enabled: false
kubeScheduler:
  enabled: false
kubeProxy:
  enabled: false
defaultRules:
  rules:
    etcd: false
kube-state-metrics:
  metricLabelsAllowlist:
    - persistentvolumeclaims=[blabs-pool,pool-state,template-id]
prometheus:
  prometheusSpec:
    retention: 10d
EOF

  local helm_cmd=(upgrade --install "$MONITORING_RELEASE_NAME" prometheus-community/kube-prometheus-stack --namespace "$MONITORING_NAMESPACE" --create-namespace -f "$values_file")
  if [ -n "$MONITORING_CHART_VERSION" ]; then
    helm_cmd+=(--version "${MONITORING_CHART_VERSION#v}")
  fi
  helm "${helm_cmd[@]}"
  rm -f "$values_file"

  local deploy
  while IFS= read -r deploy; do
    [ -n "$deploy" ] || continue
    kubectl -n "$MONITORING_NAMESPACE" rollout status "$deploy" --timeout=600s
  done < <(kubectl -n "$MONITORING_NAMESPACE" get deployment -l app.kubernetes.io/instance="$MONITORING_RELEASE_NAME" -o name)

  local sts
  while IFS= read -r sts; do
    [ -n "$sts" ] || continue
    kubectl -n "$MONITORING_NAMESPACE" rollout status "$sts" --timeout=600s
  done < <(kubectl -n "$MONITORING_NAMESPACE" get statefulset -l app.kubernetes.io/instance="$MONITORING_RELEASE_NAME" -o name)
}

patch_default_pvc_alert_exclusions() {
  if [ "$ENABLE_MONITORING" -ne 1 ]; then
    return
  fi

  local rule_name
  rule_name="${MONITORING_RELEASE_NAME}-kubernetes-storage"
  if ! kubectl -n "$MONITORING_NAMESPACE" get prometheusrule "$rule_name" >/dev/null 2>&1; then
    log "PrometheusRule ${rule_name} not found; skipping default PVC exclusion patch."
    return
  fi

  log "Patching ${rule_name} PVC filling alerts to ignore pool-* PVCs..."
  if ! python3 - "$MONITORING_NAMESPACE" "$rule_name" <<'PY' \
    | kubectl -n "$MONITORING_NAMESPACE" apply -f - >/dev/null; then
import json
import re
import subprocess
import sys

namespace = sys.argv[1]
rule_name = sys.argv[2]
obj = json.loads(
    subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "prometheusrule", rule_name, "-o", "json"],
        text=True,
    )
)
changed = 0

for group in obj.get("spec", {}).get("groups", []):
    for rule in group.get("rules", []):
        alert_name = str(rule.get("alert") or "")
        if alert_name not in {"KubePersistentVolumeFillingUp", "KubePersistentVolumeInodesFillingUp"}:
            continue
        expr = str(rule.get("expr") or "")
        updated = expr
        metric_list = []
        if alert_name == "KubePersistentVolumeFillingUp":
            metric_list = [
                "kubelet_volume_stats_available_bytes",
                "kubelet_volume_stats_capacity_bytes",
                "kubelet_volume_stats_used_bytes",
            ]
        else:
            metric_list = [
                "kubelet_volume_stats_inodes_free",
                "kubelet_volume_stats_inodes",
                "kubelet_volume_stats_inodes_used",
            ]
        for metric in metric_list:
            pattern = re.compile(rf"({metric})\\{{([^}}]*)\\}}")

            def _inject_filter(match: re.Match[str]) -> str:
                metric_name = match.group(1)
                labels = match.group(2).strip()
                if 'persistentvolumeclaim!~"pool-.*"' in labels:
                    return f"{metric_name}{{{labels}}}"
                if "persistentvolumeclaim=" in labels or "persistentvolumeclaim=~" in labels:
                    return f"{metric_name}{{{labels}}}"
                if labels:
                    return f'{metric_name}{{{labels},persistentvolumeclaim!~"pool-.*"}}'
                return f'{metric_name}{{persistentvolumeclaim!~"pool-.*"}}'

            updated = pattern.sub(_inject_filter, updated)
        if updated != expr:
            rule["expr"] = updated
            changed += 1

obj.pop("status", None)
print(json.dumps(obj))
print(f"patched_rules={changed}", file=sys.stderr)
PY
    warn "Failed to patch default KubePersistentVolumeFillingUp rules; continuing."
    return
  fi
}

install_metrics_server() {
  if [ "$ENABLE_METRICS_SERVER" -ne 1 ]; then
    log "Skipping metrics-server install (ENABLE_METRICS_SERVER=0)."
    return
  fi

  log "Installing metrics-server from ${METRICS_SERVER_MANIFEST_URL}..."
  kubectl apply -f "$METRICS_SERVER_MANIFEST_URL"

  local waited_seconds
  waited_seconds=0
  until kubectl -n kube-system get deployment metrics-server >/dev/null 2>&1; do
    if [ "$waited_seconds" -ge 120 ]; then
      fail "metrics-server deployment was not created in kube-system after apply."
    fi
    sleep 2
    waited_seconds=$((waited_seconds + 2))
  done

  if [ "$METRICS_SERVER_INSECURE_TLS" -eq 1 ]; then
    log "Configuring metrics-server with --kubelet-insecure-tls (dev-only mode)."
  else
    log "Ensuring metrics-server verifies kubelet TLS certificates (production default)."
  fi

  local patch_payload
  patch_payload="$(
    python3 - "$METRICS_SERVER_INSECURE_TLS" <<'PY'
import json
import subprocess
import sys

flag = "--kubelet-insecure-tls"
desired_insecure = str(sys.argv[1]) == "1"
obj = json.loads(
    subprocess.check_output(
        ["kubectl", "-n", "kube-system", "get", "deployment", "metrics-server", "-o", "json"],
        text=True,
    )
)
containers = (obj.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [])
if not containers:
    print("[]")
    raise SystemExit(0)

container_index = 0
for idx, container in enumerate(containers):
    if str(container.get("name") or "") == "metrics-server":
        container_index = idx
        break

container = containers[container_index]
args_present = "args" in container
args = list(container.get("args") or [])
path_base = f"/spec/template/spec/containers/{container_index}/args"
ops = []

if desired_insecure:
    if flag not in args:
        if args_present:
            ops.append({"op": "add", "path": f"{path_base}/-", "value": flag})
        else:
            ops.append({"op": "add", "path": path_base, "value": [flag]})
else:
    if args_present:
        remove_indices = [idx for idx, value in enumerate(args) if value == flag]
        for idx in sorted(remove_indices, reverse=True):
            ops.append({"op": "remove", "path": f"{path_base}/{idx}"})

print(json.dumps(ops, separators=(",", ":")))
PY
  )"
  if [ "$patch_payload" != "[]" ]; then
    kubectl -n kube-system patch deployment metrics-server --type='json' -p="$patch_payload" >/dev/null
  fi

  kubectl -n kube-system rollout status deployment/metrics-server --timeout=600s
}

install_kyverno() {
  if [ "$ENABLE_ADMISSION_POLICIES" -ne 1 ]; then
    log "Skipping Kyverno install (ENABLE_ADMISSION_POLICIES=0)."
    return
  fi
  if [ "$INSTALL_KYVERNO" -ne 1 ]; then
    log "Skipping Kyverno install (INSTALL_KYVERNO=0)."
    return
  fi

  install_helm
  log "Installing Kyverno in namespace ${KYVERNO_NAMESPACE}..."
  helm repo add kyverno https://kyverno.github.io/kyverno >/dev/null 2>&1 || true
  helm repo update >/dev/null

  local helm_cmd=(upgrade --install "$KYVERNO_RELEASE_NAME" kyverno/kyverno --namespace "$KYVERNO_NAMESPACE" --create-namespace)
  if [ -n "$KYVERNO_CHART_VERSION" ]; then
    helm_cmd+=(--version "${KYVERNO_CHART_VERSION#v}")
  fi
  helm "${helm_cmd[@]}"

  kubectl wait --for=condition=Established crd/clusterpolicies.kyverno.io --timeout=180s >/dev/null

  local deploy
  while IFS= read -r deploy; do
    [ -n "$deploy" ] || continue
    kubectl -n "$KYVERNO_NAMESPACE" rollout status "$deploy" --timeout=600s
  done < <(kubectl -n "$KYVERNO_NAMESPACE" get deployment -l app.kubernetes.io/part-of=kyverno -o name)
}

apply_admission_policies() {
  if [ "$ENABLE_ADMISSION_POLICIES" -ne 1 ]; then
    log "Skipping admission policy apply (ENABLE_ADMISSION_POLICIES=0)."
    return
  fi
  if ! kubectl get crd clusterpolicies.kyverno.io >/dev/null 2>&1; then
    fail "Kyverno CRDs are not present. Set INSTALL_KYVERNO=1 or install Kyverno before applying admission policies."
  fi

  local namespace_escaped
  namespace_escaped="$(escape_sed_replacement "$NAMESPACE")"
  log "Applying Kyverno admission policies from ${ADMISSION_POLICY_TEMPLATE}..."
  sed -e "s/__NAMESPACE__/${namespace_escaped}/g" "$ADMISSION_POLICY_TEMPLATE" | kubectl apply -f -
}

apply_monitoring_alert_rules() {
  if [ "$ENABLE_MONITORING" -ne 1 ]; then
    return
  fi

  log "Applying monitoring alert rules..."
  # Replace in place for PrometheusRule to avoid API update failures that require resourceVersion on this CRD.
  kubectl -n "$MONITORING_NAMESPACE" delete prometheusrule bretter-labs-alerts --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$MONITORING_NAMESPACE" apply -f - <<EOF
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: bretter-labs-alerts
  namespace: ${MONITORING_NAMESPACE}
  labels:
    release: ${MONITORING_RELEASE_NAME}
    app.kubernetes.io/part-of: bretter-labs
spec:
  groups:
    - name: bretter-labs-capacity
      rules:
        - alert: BretterNodeFsUsageWarning
          expr: |
            max by (instance) (
              100 * (
                1 - (
                  node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                  /
                  node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                )
              )
            ) >= ${AUTOCLEANUP_NODEFS_WARN_PCT}
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: Node filesystem usage is above ${AUTOCLEANUP_NODEFS_WARN_PCT}%.
        - alert: BretterNodeFsUsageCritical
          expr: |
            max by (instance) (
              100 * (
                1 - (
                  node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                  /
                  node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                )
              )
            ) >= ${AUTOCLEANUP_NODEFS_CRITICAL_PCT}
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: Node filesystem usage is above ${AUTOCLEANUP_NODEFS_CRITICAL_PCT}%.
        - alert: BretterNodeFsUsageEmergency
          expr: |
            max by (instance) (
              100 * (
                1 - (
                  node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                  /
                  node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs|overlay|squashfs",device!~"rootfs"}
                )
              )
            ) >= ${AUTOCLEANUP_NODEFS_EMERGENCY_PCT}
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: Node filesystem usage is above ${AUTOCLEANUP_NODEFS_EMERGENCY_PCT}%.
        - alert: BretterPvcUsageWarning
          expr: |
            max by (namespace, persistentvolumeclaim) (
              100 * (
                1 - (
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                )
              )
            ) >= ${AUTOCLEANUP_PVC_WARN_PCT}
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: A PVC in namespace ${NAMESPACE} is above ${AUTOCLEANUP_PVC_WARN_PCT}% usage.
        - alert: BretterPvcUsageCritical
          expr: |
            max by (namespace, persistentvolumeclaim) (
              100 * (
                1 - (
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                )
              )
            ) >= ${AUTOCLEANUP_PVC_CRITICAL_PCT}
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: A PVC in namespace ${NAMESPACE} is above ${AUTOCLEANUP_PVC_CRITICAL_PCT}% usage.
        - alert: BretterPvcUsageEmergency
          expr: |
            max by (namespace, persistentvolumeclaim) (
              100 * (
                1 - (
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}",persistentvolumeclaim!~"pool-.*"}
                )
              )
            ) >= ${AUTOCLEANUP_PVC_EMERGENCY_PCT}
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: A PVC in namespace ${NAMESPACE} is above ${AUTOCLEANUP_PVC_EMERGENCY_PCT}% usage.
        - alert: BretterNodeDiskPressure
          expr: max by (node) (kube_node_status_condition{condition="DiskPressure",status="true"} == 1) > 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: One or more nodes report DiskPressure.
    - name: bretter-labs-runtime
      rules:
        - alert: BretterPodRestartBurst
          expr: |
            sum by (namespace, pod) (
              increase(kube_pod_container_status_restarts_total{namespace="${NAMESPACE}"}[15m])
            ) >= ${MONITORING_RESTART_ALERT_COUNT}
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: Pod restart burst detected in namespace ${NAMESPACE}.
        - alert: BretterContainerStartupSlow
          expr: |
            (
              (time() - kube_pod_created{namespace="${NAMESPACE}",pod=~"ct-.*"}) / 60
            ) > 5
            and on(namespace, pod)
            kube_pod_status_phase{namespace="${NAMESPACE}",phase=~"Pending|Running"} == 1
            and on(namespace, pod)
            kube_pod_status_ready{namespace="${NAMESPACE}",condition="true"} == 0
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: Container pod startup is taking longer than expected.
        - alert: BretterContainerCrashLoop
          expr: |
            max by (namespace, pod, container) (
              kube_pod_container_status_waiting_reason{
                namespace="${NAMESPACE}",
                pod=~"ct-.*",
                reason="CrashLoopBackOff"
              } == 1
            ) > 0
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: Container pod is in CrashLoopBackOff.
        - alert: BretterContainerImagePullBackOff
          expr: |
            max by (namespace, pod, container) (
              kube_pod_container_status_waiting_reason{
                namespace="${NAMESPACE}",
                pod=~"ct-.*",
                reason=~"ImagePullBackOff|ErrImagePull"
              } == 1
            ) > 0
          for: 3m
          labels:
            severity: critical
          annotations:
            summary: Container image pull is failing.
        - alert: BretterContainerOOMKilled
          expr: |
            max by (namespace, pod, container) (
              kube_pod_container_status_last_terminated_reason{
                namespace="${NAMESPACE}",
                pod=~"ct-.*",
                reason="OOMKilled"
              } == 1
            ) > 0
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: Container was terminated due to OOMKilled.
        - alert: BretterWarmPoolDepleted
          expr: |
            (
              sum(kube_persistentvolumeclaim_labels{namespace="${NAMESPACE}",label_blabs_pool="true",label_pool_state="ready"})
              < ${MONITORING_WARM_POOL_MIN_READY}
            )
            and
            (
              sum(kube_persistentvolumeclaim_labels{namespace="${NAMESPACE}",label_blabs_pool="true"}) > 0
            )
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: Warm pool ready PVC count fell below ${MONITORING_WARM_POOL_MIN_READY}.
        - alert: BretterDataVolumeUploadStale
          expr: |
            (
              (time() - kube_persistentvolumeclaim_created{namespace="${NAMESPACE}",persistentvolumeclaim=~"img-upload-.*"}) / 60
            ) > ${MONITORING_DV_STALE_MINUTES}
            and on(namespace, persistentvolumeclaim)
            kube_persistentvolumeclaim_status_phase{namespace="${NAMESPACE}",phase=~"Pending|Bound"} == 1
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: A direct-upload DataVolume PVC has been active for over ${MONITORING_DV_STALE_MINUTES} minutes.
EOF
}

enable_cpu_manager_static_all_nodes() {
  if [ "$CPU_MANAGER_STATIC" -ne 1 ]; then
    return
  fi
  if ! kubectl debug -h >/dev/null 2>&1; then
    fail "kubectl debug is required to enable CPU manager static on all nodes."
  fi

  local nodes=()
  local node
  local debug_output
  mapfile -t nodes < <(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  if [ "${#nodes[@]}" -eq 0 ]; then
    fail "No nodes found while enabling CPU manager static."
  fi

  for node in "${nodes[@]}"; do
    log "Ensuring kubelet cpuManagerPolicy=static on node ${node}..."
    cleanup_node_debugger_pods "$node"
    if ! debug_output="$(
      kubectl debug "node/${node}" --quiet --image=ubuntu:24.04 --profile=sysadmin -- \
        chroot /host bash -lc '
set -euo pipefail
if [ ! -f /var/lib/kubelet/config.yaml ]; then
  echo "kubelet config not found, skipping"
  exit 0
fi
python3 - <<'"'"'PY'"'"'
from pathlib import Path
cfg = Path("/var/lib/kubelet/config.yaml")
lines = cfg.read_text().splitlines()
out = []
found_policy = False
found_reconcile = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("cpuManagerPolicy:"):
        out.append("cpuManagerPolicy: static")
        found_policy = True
        continue
    if stripped.startswith("cpuManagerReconcilePeriod:"):
        found_reconcile = True
    out.append(line)
if not found_policy:
    out.append("cpuManagerPolicy: static")
if not found_reconcile:
    out.append("cpuManagerReconcilePeriod: 5s")
cfg.write_text("\n".join(out) + "\n")
PY
if command -v systemctl >/dev/null 2>&1; then
  systemctl restart kubelet || true
fi
if pgrep -x kubelet >/dev/null 2>&1; then
  pkill -HUP kubelet || true
fi
'
    )"; then
      cleanup_node_debugger_pods "$node"
      if [ -n "$debug_output" ]; then
        echo "$debug_output" >&2
      fi
      fail "Failed to enable cpuManagerPolicy=static on node $node."
    fi
    cleanup_node_debugger_pods "$node"
  done

  kubectl wait --for=condition=Ready nodes --all --timeout=300s >/dev/null 2>&1 || \
    log "WARNING: kubelet restart finished but not all nodes reported Ready within timeout."
  log "CPU manager static enabled on all nodes."
}

detect_control_node() {
  if [ -n "$CONTROL_NODE" ]; then
    return
  fi

  CONTROL_NODE="$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -z "$CONTROL_NODE" ]; then
    CONTROL_NODE="$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  fi
  if [ -z "$CONTROL_NODE" ]; then
    fail "Could not determine a control node. Set CONTROL_NODE explicitly."
  fi
}

detect_node_external_host() {
  if [ -n "$NODE_EXTERNAL_HOST" ]; then
    return
  fi

  NODE_EXTERNAL_HOST="$(kubectl get node "$CONTROL_NODE" -o jsonpath='{.status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null || true)"
  if [ -z "$NODE_EXTERNAL_HOST" ]; then
    NODE_EXTERNAL_HOST="$(kubectl get node "$CONTROL_NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
  fi
  if [ -z "$NODE_EXTERNAL_HOST" ]; then
    fail "Could not determine NODE_EXTERNAL_HOST from node $CONTROL_NODE."
  fi
}

check_free_space_guard() {
  local path="$1"
  local label="$2"
  local avail_kib avail_gib

  if [ ! -e "$path" ]; then
    return
  fi

  avail_kib="$(df -Pk "$path" | awk 'NR==2 {print $4}')"
  avail_gib=$((avail_kib / 1024 / 1024))

  if [ "$avail_gib" -lt "$SETUP_MIN_FREE_GIB" ]; then
    fail "${label} is low on free space (${avail_gib}Gi available, minimum ${SETUP_MIN_FREE_GIB}Gi)."
  fi
  if [ "$avail_gib" -lt "$SETUP_WARN_FREE_GIB" ]; then
    log "WARNING: ${label} free space is low (${avail_gib}Gi available)."
  fi
}

warn_if_diskpressure_nodes() {
  local pressured
  pressured="$(
    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.conditions[?(@.type=="DiskPressure")].status}{"\n"}{end}' \
      | awk '$2=="True"{print $1}' \
      | xargs || true
  )"
  if [ -n "$pressured" ]; then
    log "WARNING: nodes currently reporting DiskPressure: $pressured"
  fi
}

run_storage_preflight_checks() {
  log "Running storage preflight checks..."
  check_free_space_guard "/" "root filesystem (/)"
  check_free_space_guard "/var/lib" "/var/lib"
  if [ -d "$GOLDEN_IMAGES_HOSTPATH" ]; then
    check_free_space_guard "$GOLDEN_IMAGES_HOSTPATH" "golden image storage path"
  else
    check_free_space_guard "$(dirname "$GOLDEN_IMAGES_HOSTPATH")" "golden image storage parent path"
  fi
  if [ -d "$POSTGRES_DATA_HOSTPATH" ]; then
    check_free_space_guard "$POSTGRES_DATA_HOSTPATH" "postgres data path"
  else
    check_free_space_guard "$(dirname "$POSTGRES_DATA_HOSTPATH")" "postgres data parent path"
  fi
  if [ -d "$BACKEND_DATA_HOSTPATH" ]; then
    check_free_space_guard "$BACKEND_DATA_HOSTPATH" "backend data path"
  else
    check_free_space_guard "$(dirname "$BACKEND_DATA_HOSTPATH")" "backend data parent path"
  fi
  warn_if_diskpressure_nodes
}

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

yaml_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

render_manifest_template() {
  local input="$1"
  local output="$2"

  local ns control_node node_external_host backend_image frontend_image runner_image public_scheme tls_secret_name
  local runner_node_selector_value
  local vm_storage_class backend_data_hostpath golden_images_hostpath postgres_data_hostpath cdi_upload_proxy_url
  local windows_machine_type windows_efi_enabled windows_cpu_model linux_machine_type linux_efi_enabled linux_cpu_model vm_net_backend vm_runner_privileged
  local vm_console_external_traffic_policy vm_console_source_cidrs vm_console_ticket_length
  local container_ingress_enabled container_ingress_class container_ingress_base_domain container_ingress_annotations_json
  local container_image_prepull_enabled container_image_prepull_timeout_seconds
  local container_allowed_registries container_signature_verification_enabled container_signature_key_ref
  local container_scan_enabled container_scan_interval_minutes container_scan_severity
  local container_start_queue_enabled container_start_queue_base_delay_seconds container_start_queue_max_delay_seconds
  ns="$(escape_sed_replacement "$NAMESPACE")"
  control_node="$(escape_sed_replacement "$CONTROL_NODE")"
  node_external_host="$(escape_sed_replacement "$NODE_EXTERNAL_HOST")"
  runner_node_selector_value="$(escape_sed_replacement "$RUNNER_NODE_SELECTOR_VALUE")"
  vm_storage_class="$(escape_sed_replacement "$VM_STORAGE_CLASS")"
  backend_image="$(escape_sed_replacement "$BACKEND_IMAGE")"
  frontend_image="$(escape_sed_replacement "$FRONTEND_IMAGE")"
  runner_image="$(escape_sed_replacement "$RUNNER_IMAGE")"
  public_scheme="$(escape_sed_replacement "$PUBLIC_SCHEME")"
  tls_secret_name="$(escape_sed_replacement "$TLS_SECRET_NAME")"
  windows_machine_type="$(escape_sed_replacement "$WINDOWS_MACHINE_TYPE")"
  windows_efi_enabled="$(escape_sed_replacement "$WINDOWS_EFI_ENABLED")"
  windows_cpu_model="$(escape_sed_replacement "$WINDOWS_CPU_MODEL")"
  linux_machine_type="$(escape_sed_replacement "$LINUX_MACHINE_TYPE")"
  linux_efi_enabled="$(escape_sed_replacement "$LINUX_EFI_ENABLED")"
  linux_cpu_model="$(escape_sed_replacement "$LINUX_CPU_MODEL")"
  vm_net_backend="$(escape_sed_replacement "$VM_NET_BACKEND")"
  vm_runner_privileged="$(escape_sed_replacement "$VM_RUNNER_PRIVILEGED")"
  vm_console_external_traffic_policy="$(escape_sed_replacement "$VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY")"
  vm_console_source_cidrs="$(escape_sed_replacement "$VM_CONSOLE_SOURCE_CIDRS")"
  vm_console_ticket_length="$(escape_sed_replacement "$VM_CONSOLE_TICKET_LENGTH")"
  container_ingress_enabled="$(escape_sed_replacement "$CONTAINER_INGRESS_ENABLED")"
  container_ingress_class="$(escape_sed_replacement "$CONTAINER_INGRESS_CLASS")"
  container_ingress_base_domain="$(escape_sed_replacement "$CONTAINER_INGRESS_BASE_DOMAIN")"
  container_ingress_annotations_json="$(escape_sed_replacement "$CONTAINER_INGRESS_ANNOTATIONS_JSON")"
  container_image_prepull_enabled="$(escape_sed_replacement "$CONTAINER_IMAGE_PREPULL_ENABLED")"
  container_image_prepull_timeout_seconds="$(escape_sed_replacement "$CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS")"
  container_allowed_registries="$(escape_sed_replacement "$CONTAINER_ALLOWED_REGISTRIES")"
  container_signature_verification_enabled="$(escape_sed_replacement "$CONTAINER_SIGNATURE_VERIFICATION_ENABLED")"
  container_signature_key_ref="$(escape_sed_replacement "$CONTAINER_SIGNATURE_KEY_REF")"
  container_scan_enabled="$(escape_sed_replacement "$CONTAINER_SCAN_ENABLED")"
  container_scan_interval_minutes="$(escape_sed_replacement "$CONTAINER_SCAN_INTERVAL_MINUTES")"
  container_scan_severity="$(escape_sed_replacement "$CONTAINER_SCAN_SEVERITY")"
  container_start_queue_enabled="$(escape_sed_replacement "$CONTAINER_START_QUEUE_ENABLED")"
  container_start_queue_base_delay_seconds="$(escape_sed_replacement "$CONTAINER_START_QUEUE_BASE_DELAY_SECONDS")"
  container_start_queue_max_delay_seconds="$(escape_sed_replacement "$CONTAINER_START_QUEUE_MAX_DELAY_SECONDS")"
  backend_data_hostpath="$(escape_sed_replacement "$BACKEND_DATA_HOSTPATH")"
  golden_images_hostpath="$(escape_sed_replacement "$GOLDEN_IMAGES_HOSTPATH")"
  postgres_data_hostpath="$(escape_sed_replacement "$POSTGRES_DATA_HOSTPATH")"
  cdi_upload_proxy_url="$(escape_sed_replacement "$CDI_UPLOAD_PROXY_URL")"

  sed \
    -e "s/__NAMESPACE__/${ns}/g" \
    -e "s/__CONTROL_NODE__/${control_node}/g" \
    -e "s/__NODE_EXTERNAL_HOST__/${node_external_host}/g" \
    -e "s/__RUNNER_NODE_SELECTOR_VALUE__/${runner_node_selector_value}/g" \
    -e "s/__VM_STORAGE_CLASS__/${vm_storage_class}/g" \
    -e "s/__BACKEND_IMAGE__/${backend_image}/g" \
    -e "s/__FRONTEND_IMAGE__/${frontend_image}/g" \
    -e "s/__RUNNER_IMAGE__/${runner_image}/g" \
    -e "s/__PUBLIC_SCHEME__/${public_scheme}/g" \
    -e "s/__TLS_SECRET_NAME__/${tls_secret_name}/g" \
    -e "s/__WINDOWS_MACHINE_TYPE__/${windows_machine_type}/g" \
    -e "s/__WINDOWS_EFI_ENABLED__/${windows_efi_enabled}/g" \
    -e "s/__WINDOWS_CPU_MODEL__/${windows_cpu_model}/g" \
    -e "s/__LINUX_MACHINE_TYPE__/${linux_machine_type}/g" \
    -e "s/__LINUX_EFI_ENABLED__/${linux_efi_enabled}/g" \
    -e "s/__LINUX_CPU_MODEL__/${linux_cpu_model}/g" \
    -e "s/__VM_NET_BACKEND__/${vm_net_backend}/g" \
    -e "s/__VM_RUNNER_PRIVILEGED__/${vm_runner_privileged}/g" \
    -e "s/__VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY__/${vm_console_external_traffic_policy}/g" \
    -e "s#__VM_CONSOLE_SOURCE_CIDRS__#${vm_console_source_cidrs}#g" \
    -e "s/__VM_CONSOLE_TICKET_LENGTH__/${vm_console_ticket_length}/g" \
    -e "s/__CONTAINER_INGRESS_ENABLED__/${container_ingress_enabled}/g" \
    -e "s/__CONTAINER_INGRESS_CLASS__/${container_ingress_class}/g" \
    -e "s/__CONTAINER_INGRESS_BASE_DOMAIN__/${container_ingress_base_domain}/g" \
    -e "s#__CONTAINER_INGRESS_ANNOTATIONS_JSON__#${container_ingress_annotations_json}#g" \
    -e "s/__CONTAINER_IMAGE_PREPULL_ENABLED__/${container_image_prepull_enabled}/g" \
    -e "s/__CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS__/${container_image_prepull_timeout_seconds}/g" \
    -e "s/__CONTAINER_ALLOWED_REGISTRIES__/${container_allowed_registries}/g" \
    -e "s/__CONTAINER_SIGNATURE_VERIFICATION_ENABLED__/${container_signature_verification_enabled}/g" \
    -e "s#__CONTAINER_SIGNATURE_KEY_REF__#${container_signature_key_ref}#g" \
    -e "s/__CONTAINER_SCAN_ENABLED__/${container_scan_enabled}/g" \
    -e "s/__CONTAINER_SCAN_INTERVAL_MINUTES__/${container_scan_interval_minutes}/g" \
    -e "s/__CONTAINER_SCAN_SEVERITY__/${container_scan_severity}/g" \
    -e "s/__CONTAINER_START_QUEUE_ENABLED__/${container_start_queue_enabled}/g" \
    -e "s/__CONTAINER_START_QUEUE_BASE_DELAY_SECONDS__/${container_start_queue_base_delay_seconds}/g" \
    -e "s/__CONTAINER_START_QUEUE_MAX_DELAY_SECONDS__/${container_start_queue_max_delay_seconds}/g" \
    -e "s#__BACKEND_DATA_HOSTPATH__#${backend_data_hostpath}#g" \
    -e "s#__GOLDEN_IMAGES_HOSTPATH__#${golden_images_hostpath}#g" \
    -e "s#__POSTGRES_DATA_HOSTPATH__#${postgres_data_hostpath}#g" \
    -e "s#__CDI_UPLOAD_PROXY_URL__#${cdi_upload_proxy_url}#g" \
    "$input" >"$output"
}

prepare_rendered_manifests() {
  RENDERED_GOLDEN_HOSTPATH_MANIFEST="$(mktemp /tmp/bretter-golden-hostpath.XXXXXX.yaml)"
  RENDERED_GOLDEN_PVC_MANIFEST="$(mktemp /tmp/bretter-golden-pvc.XXXXXX.yaml)"

  render_manifest_template "$ROOT_DIR/deploy/golden-hostpath.yaml" "$RENDERED_GOLDEN_HOSTPATH_MANIFEST"
  render_manifest_template "$ROOT_DIR/deploy/golden-pvc.yaml" "$RENDERED_GOLDEN_PVC_MANIFEST"
}

render_helm_values_override() {
  local output_file="$1"
  local control_node node_external_host runner_node_selector_value vm_storage_class
  local backend_image frontend_image runner_image public_scheme tls_secret_name
  local windows_machine_type windows_efi_enabled windows_cpu_model linux_machine_type linux_efi_enabled linux_cpu_model
  local vm_net_backend vm_runner_privileged vm_console_external_traffic_policy vm_console_source_cidrs vm_console_ticket_length
  local backend_service_type backend_service_nodeport_line
  local container_ingress_enabled container_ingress_class container_ingress_base_domain container_ingress_annotations_json
  local container_image_prepull_enabled container_image_prepull_timeout_seconds
  local container_allowed_registries container_signature_verification_enabled container_signature_key_ref
  local container_scan_enabled container_scan_interval_minutes container_scan_severity
  local container_start_queue_enabled container_start_queue_base_delay_seconds container_start_queue_max_delay_seconds
  local production_profile
  local cors_enterprise_profile cors_allowed_origins cors_allowed_origin_regex cors_allowed_methods cors_allowed_headers
  local auth_login_rate_limit_window_seconds auth_login_rate_limit_max_attempts auth_login_lockout_seconds
  local vm_connect_insecure_tls container_connect_insecure_tls secrets_encryption_key
  local backend_data_hostpath golden_images_hostpath postgres_data_hostpath cdi_upload_proxy_url
  local admin_bootstrap_password

  control_node="$(yaml_escape "$CONTROL_NODE")"
  node_external_host="$(yaml_escape "$NODE_EXTERNAL_HOST")"
  runner_node_selector_value="$(yaml_escape "$RUNNER_NODE_SELECTOR_VALUE")"
  vm_storage_class="$(yaml_escape "$VM_STORAGE_CLASS")"
  backend_image="$(yaml_escape "$BACKEND_IMAGE")"
  frontend_image="$(yaml_escape "$FRONTEND_IMAGE")"
  runner_image="$(yaml_escape "$RUNNER_IMAGE")"
  public_scheme="$(yaml_escape "$PUBLIC_SCHEME")"
  tls_secret_name="$(yaml_escape "$TLS_SECRET_NAME")"
  windows_machine_type="$(yaml_escape "$WINDOWS_MACHINE_TYPE")"
  windows_efi_enabled="$(yaml_escape "$WINDOWS_EFI_ENABLED")"
  windows_cpu_model="$(yaml_escape "$WINDOWS_CPU_MODEL")"
  linux_machine_type="$(yaml_escape "$LINUX_MACHINE_TYPE")"
  linux_efi_enabled="$(yaml_escape "$LINUX_EFI_ENABLED")"
  linux_cpu_model="$(yaml_escape "$LINUX_CPU_MODEL")"
  vm_net_backend="$(yaml_escape "$VM_NET_BACKEND")"
  vm_runner_privileged="$(yaml_escape "$VM_RUNNER_PRIVILEGED")"
  vm_console_external_traffic_policy="$(yaml_escape "$VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY")"
  vm_console_source_cidrs="$(yaml_escape "$VM_CONSOLE_SOURCE_CIDRS")"
  vm_console_ticket_length="$(yaml_escape "$VM_CONSOLE_TICKET_LENGTH")"
  if [ "$BACKEND_NODEPORT_ENABLED" -eq 1 ]; then
    backend_service_type="NodePort"
    backend_service_nodeport_line="      nodePort: ${BACKEND_NODEPORT}"
  else
    backend_service_type="ClusterIP"
    backend_service_nodeport_line=""
  fi
  backend_service_type="$(yaml_escape "$backend_service_type")"
  backend_service_nodeport_line="$(yaml_escape "$backend_service_nodeport_line")"
  container_ingress_enabled="$(yaml_escape "$CONTAINER_INGRESS_ENABLED")"
  container_ingress_class="$(yaml_escape "$CONTAINER_INGRESS_CLASS")"
  container_ingress_base_domain="$(yaml_escape "$CONTAINER_INGRESS_BASE_DOMAIN")"
  container_ingress_annotations_json="$(yaml_escape "$CONTAINER_INGRESS_ANNOTATIONS_JSON")"
  container_image_prepull_enabled="$(yaml_escape "$CONTAINER_IMAGE_PREPULL_ENABLED")"
  container_image_prepull_timeout_seconds="$(yaml_escape "$CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS")"
  container_allowed_registries="$(yaml_escape "$CONTAINER_ALLOWED_REGISTRIES")"
  container_signature_verification_enabled="$(yaml_escape "$CONTAINER_SIGNATURE_VERIFICATION_ENABLED")"
  container_signature_key_ref="$(yaml_escape "$CONTAINER_SIGNATURE_KEY_REF")"
  container_scan_enabled="$(yaml_escape "$CONTAINER_SCAN_ENABLED")"
  container_scan_interval_minutes="$(yaml_escape "$CONTAINER_SCAN_INTERVAL_MINUTES")"
  container_scan_severity="$(yaml_escape "$CONTAINER_SCAN_SEVERITY")"
  container_start_queue_enabled="$(yaml_escape "$CONTAINER_START_QUEUE_ENABLED")"
  container_start_queue_base_delay_seconds="$(yaml_escape "$CONTAINER_START_QUEUE_BASE_DELAY_SECONDS")"
  container_start_queue_max_delay_seconds="$(yaml_escape "$CONTAINER_START_QUEUE_MAX_DELAY_SECONDS")"
  production_profile="$(yaml_escape "$PRODUCTION_PROFILE")"
  cors_enterprise_profile="$(yaml_escape "$CORS_ENTERPRISE_PROFILE")"
  cors_allowed_origins="$(yaml_escape "$CORS_ALLOWED_ORIGINS")"
  cors_allowed_origin_regex="$(yaml_escape "$CORS_ALLOWED_ORIGIN_REGEX")"
  cors_allowed_methods="$(yaml_escape "$CORS_ALLOWED_METHODS")"
  cors_allowed_headers="$(yaml_escape "$CORS_ALLOWED_HEADERS")"
  auth_login_rate_limit_window_seconds="$(yaml_escape "$AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS")"
  auth_login_rate_limit_max_attempts="$(yaml_escape "$AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS")"
  auth_login_lockout_seconds="$(yaml_escape "$AUTH_LOGIN_LOCKOUT_SECONDS")"
  vm_connect_insecure_tls="$(yaml_escape "$VM_CONNECT_INSECURE_TLS")"
  container_connect_insecure_tls="$(yaml_escape "$CONTAINER_CONNECT_INSECURE_TLS")"
  secrets_encryption_key="$(yaml_escape "$SECRETS_ENCRYPTION_KEY")"
  backend_data_hostpath="$(yaml_escape "$BACKEND_DATA_HOSTPATH")"
  golden_images_hostpath="$(yaml_escape "$GOLDEN_IMAGES_HOSTPATH")"
  postgres_data_hostpath="$(yaml_escape "$POSTGRES_DATA_HOSTPATH")"
  cdi_upload_proxy_url="$(yaml_escape "$CDI_UPLOAD_PROXY_URL")"
  admin_bootstrap_password="$(yaml_escape "$ADMIN_BOOTSTRAP_PASSWORD")"

  cat >"$output_file" <<EOF
appTemplateValues:
  CONTROL_NODE: "${control_node}"
  NODE_EXTERNAL_HOST: "${node_external_host}"
  RUNNER_NODE_SELECTOR_VALUE: "${runner_node_selector_value}"
  VM_STORAGE_CLASS: "${vm_storage_class}"
  BACKEND_IMAGE: "${backend_image}"
  FRONTEND_IMAGE: "${frontend_image}"
  RUNNER_IMAGE: "${runner_image}"
  PUBLIC_SCHEME: "${public_scheme}"
  TLS_SECRET_NAME: "${tls_secret_name}"
  ADMIN_BOOTSTRAP_PASSWORD: "${admin_bootstrap_password}"
  WINDOWS_MACHINE_TYPE: "${windows_machine_type}"
  WINDOWS_EFI_ENABLED: "${windows_efi_enabled}"
  WINDOWS_CPU_MODEL: "${windows_cpu_model}"
  LINUX_MACHINE_TYPE: "${linux_machine_type}"
  LINUX_EFI_ENABLED: "${linux_efi_enabled}"
  LINUX_CPU_MODEL: "${linux_cpu_model}"
  VM_NET_BACKEND: "${vm_net_backend}"
  VM_RUNNER_PRIVILEGED: "${vm_runner_privileged}"
  VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY: "${vm_console_external_traffic_policy}"
  VM_CONSOLE_SOURCE_CIDRS: "${vm_console_source_cidrs}"
  VM_CONSOLE_TICKET_LENGTH: "${vm_console_ticket_length}"
  BACKEND_SERVICE_TYPE: "${backend_service_type}"
  BACKEND_SERVICE_NODEPORT_LINE: "${backend_service_nodeport_line}"
  CONTAINER_INGRESS_ENABLED: "${container_ingress_enabled}"
  CONTAINER_INGRESS_CLASS: "${container_ingress_class}"
  CONTAINER_INGRESS_BASE_DOMAIN: "${container_ingress_base_domain}"
  CONTAINER_INGRESS_ANNOTATIONS_JSON: "${container_ingress_annotations_json}"
  CONTAINER_IMAGE_PREPULL_ENABLED: "${container_image_prepull_enabled}"
  CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS: "${container_image_prepull_timeout_seconds}"
  CONTAINER_ALLOWED_REGISTRIES: "${container_allowed_registries}"
  CONTAINER_SIGNATURE_VERIFICATION_ENABLED: "${container_signature_verification_enabled}"
  CONTAINER_SIGNATURE_KEY_REF: "${container_signature_key_ref}"
  CONTAINER_SCAN_ENABLED: "${container_scan_enabled}"
  CONTAINER_SCAN_INTERVAL_MINUTES: "${container_scan_interval_minutes}"
  CONTAINER_SCAN_SEVERITY: "${container_scan_severity}"
  CONTAINER_START_QUEUE_ENABLED: "${container_start_queue_enabled}"
  CONTAINER_START_QUEUE_BASE_DELAY_SECONDS: "${container_start_queue_base_delay_seconds}"
  CONTAINER_START_QUEUE_MAX_DELAY_SECONDS: "${container_start_queue_max_delay_seconds}"
  PRODUCTION_PROFILE: "${production_profile}"
  CORS_ENTERPRISE_PROFILE: "${cors_enterprise_profile}"
  CORS_ALLOWED_ORIGINS: "${cors_allowed_origins}"
  CORS_ALLOWED_ORIGIN_REGEX: "${cors_allowed_origin_regex}"
  CORS_ALLOWED_METHODS: "${cors_allowed_methods}"
  CORS_ALLOWED_HEADERS: "${cors_allowed_headers}"
  AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS: "${auth_login_rate_limit_window_seconds}"
  AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS: "${auth_login_rate_limit_max_attempts}"
  AUTH_LOGIN_LOCKOUT_SECONDS: "${auth_login_lockout_seconds}"
  VM_CONNECT_INSECURE_TLS: "${vm_connect_insecure_tls}"
  CONTAINER_CONNECT_INSECURE_TLS: "${container_connect_insecure_tls}"
  SECRETS_ENCRYPTION_KEY: "${secrets_encryption_key}"
  BACKEND_DATA_HOSTPATH: "${backend_data_hostpath}"
  GOLDEN_IMAGES_HOSTPATH: "${golden_images_hostpath}"
  POSTGRES_DATA_HOSTPATH: "${postgres_data_hostpath}"
  CDI_UPLOAD_PROXY_URL: "${cdi_upload_proxy_url}"
EOF
}

apply_base_release_with_helm() {
  adopt_resource_for_helm() {
    local namespace="$1"
    local kind="$2"
    local name="$3"
    local ref="${kind}/${name}"
    local scope_args=()
    if [ -n "$namespace" ]; then
      scope_args=(-n "$namespace")
    fi
    if ! kubectl "${scope_args[@]}" get "$kind" "$name" >/dev/null 2>&1; then
      return
    fi
    kubectl "${scope_args[@]}" annotate "$ref" --overwrite \
      "meta.helm.sh/release-name=$HELM_RELEASE_NAME" \
      "meta.helm.sh/release-namespace=$NAMESPACE" >/dev/null
    kubectl "${scope_args[@]}" label "$ref" --overwrite \
      "app.kubernetes.io/managed-by=Helm" >/dev/null
  }

  adopt_existing_resources_for_helm() {
    # One-time migration helper: adopt existing kubectl-managed resources before Helm takes over.
    adopt_resource_for_helm "$NAMESPACE" serviceaccount bretter-backend
    adopt_resource_for_helm "$NAMESPACE" limitrange bretter-default-container-limits
    adopt_resource_for_helm "$NAMESPACE" resourcequota bretter-runtime-quota
    adopt_resource_for_helm "$NAMESPACE" role bretter-backend
    adopt_resource_for_helm "$NAMESPACE" rolebinding bretter-backend
    adopt_resource_for_helm "$NAMESPACE" persistentvolumeclaim backend-postgres-data
    adopt_resource_for_helm "$NAMESPACE" deployment bretter-postgres
    adopt_resource_for_helm "$NAMESPACE" service bretter-postgres
    adopt_resource_for_helm "$NAMESPACE" persistentvolumeclaim backend-data
    adopt_resource_for_helm "$NAMESPACE" deployment bretter-backend
    adopt_resource_for_helm "$NAMESPACE" service bretter-backend
    adopt_resource_for_helm "$NAMESPACE" deployment bretter-frontend
    adopt_resource_for_helm "$NAMESPACE" service bretter-frontend
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-default-deny-ingress
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-backend-allow-ingress
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-frontend-allow-ingress
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-backend-restrict-egress
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-frontend-restrict-egress
    adopt_resource_for_helm "$NAMESPACE" networkpolicy bretter-postgres-allow-backend
    adopt_resource_for_helm "$NAMESPACE" poddisruptionbudget bretter-backend
    adopt_resource_for_helm "$NAMESPACE" poddisruptionbudget bretter-frontend
    adopt_resource_for_helm "" clusterrole bretter-backend
    adopt_resource_for_helm "" clusterrolebinding bretter-backend
    adopt_resource_for_helm "" persistentvolume backend-postgres-pv
    adopt_resource_for_helm "" persistentvolume backend-data-pv
  }

  install_helm
  RENDERED_HELM_VALUES="$(mktemp /tmp/bretter-helm-values.XXXXXX.yaml)"
  render_helm_values_override "$RENDERED_HELM_VALUES"
  adopt_existing_resources_for_helm

  log "Applying base release via Helm (release: $HELM_RELEASE_NAME chart: $HELM_CHART_DIR)"
  helm upgrade --install "$HELM_RELEASE_NAME" "$HELM_CHART_DIR" \
    --namespace "$NAMESPACE" \
    --create-namespace \
    -f "$HELM_CHART_DIR/values.yaml" \
    -f "$RENDERED_HELM_VALUES"
}

ensure_ghcr_login() {
  local ghcr_user="${GHCR_USERNAME:-}"
  local ghcr_token="${GHCR_TOKEN:-}"

  if [ -z "$ghcr_user" ]; then
    read -r -p "GHCR username: " ghcr_user
  fi
  if [ -z "$ghcr_token" ]; then
    read -r -s -p "GHCR token (write:packages): " ghcr_token
    echo
  fi
  if [ -z "$ghcr_user" ] || [ -z "$ghcr_token" ]; then
    fail "GHCR credentials are required for image push or pull-secret creation."
  fi

  echo "$ghcr_token" | podman login ghcr.io --username "$ghcr_user" --password-stdin
}

build_images() {
  local vite_api_base="${VITE_API_BASE:-/api}"

  log "Building backend image: $BACKEND_IMAGE"
  podman build -t "$BACKEND_IMAGE" -f "$ROOT_DIR/backend/Dockerfile" "$ROOT_DIR"

  log "Building frontend image: $FRONTEND_IMAGE"
  podman build --build-arg "VITE_API_BASE=${vite_api_base}" -t "$FRONTEND_IMAGE" -f "$ROOT_DIR/frontend-vite/Dockerfile" "$ROOT_DIR"

  log "Building runner image: $RUNNER_IMAGE"
  podman build -t "$RUNNER_IMAGE" -f "$ROOT_DIR/runner/Dockerfile" "$ROOT_DIR/runner"
}

push_images() {
  log "Pushing backend image..."
  podman push "$BACKEND_IMAGE"

  log "Pushing frontend image..."
  podman push "$FRONTEND_IMAGE"

  log "Pushing runner image..."
  podman push "$RUNNER_IMAGE"
}

load_images_into_containerd() {
  if ! command -v ctr >/dev/null 2>&1; then
    fail "ctr is required to load local images into containerd."
  fi

  local backend_tar frontend_tar runner_tar
  backend_tar="$(mktemp /tmp/bretter-backend-image.XXXXXX.tar)"
  frontend_tar="$(mktemp /tmp/bretter-frontend-image.XXXXXX.tar)"
  runner_tar="$(mktemp /tmp/bretter-runner-image.XXXXXX.tar)"

  log "Saving local backend image tar..."
  podman save -o "$backend_tar" "$BACKEND_IMAGE"
  log "Saving local frontend image tar..."
  podman save -o "$frontend_tar" "$FRONTEND_IMAGE"
  log "Saving local runner image tar..."
  podman save -o "$runner_tar" "$RUNNER_IMAGE"

  log "Importing backend image into containerd..."
  sudo_cmd ctr -n k8s.io images import "$backend_tar"
  log "Importing frontend image into containerd..."
  sudo_cmd ctr -n k8s.io images import "$frontend_tar"
  log "Importing runner image into containerd..."
  sudo_cmd ctr -n k8s.io images import "$runner_tar"

  preload_runner_image_on_worker_nodes "$runner_tar"

  rm -f "$backend_tar" "$frontend_tar" "$runner_tar"
}

cleanup_node_debugger_pods() {
  local node="$1"
  local pod_names

  pod_names="$(kubectl -n default get pods --no-headers -o custom-columns=':metadata.name' 2>/dev/null \
    | grep "^node-debugger-${node}-" || true)"
  if [ -z "$pod_names" ]; then
    return
  fi

  # Delete any prior node-debugger pods to avoid clutter or stale failures.
  # shellcheck disable=SC2086
  kubectl -n default delete pod $pod_names --ignore-not-found=true >/dev/null 2>&1 || true
}

preload_runner_image_on_worker_nodes() {
  local runner_tar="$1"
  local nodes=()
  local node
  local preload_output

  if [ "$PRELOAD_RUNNER_ON_ALL_NODES" -ne 1 ]; then
    log "Skipping cross-node runner image preload (PRELOAD_RUNNER_ON_ALL_NODES=0)."
    return
  fi

  mapfile -t nodes < <(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  if [ "${#nodes[@]}" -le 1 ]; then
    return
  fi

  if ! kubectl debug -h >/dev/null 2>&1; then
    fail "kubectl debug is required to preload runner images across nodes."
  fi

  for node in "${nodes[@]}"; do
    if [ "$node" = "$CONTROL_NODE" ]; then
      continue
    fi

    log "Preloading runner image into containerd on node $node..."
    cleanup_node_debugger_pods "$node"
    # Stage the tar on the host and import from file for reliable cross-node loads.
    if ! preload_output="$(
      # shellcheck disable=SC2016
      kubectl debug "node/${node}" --quiet --image=busybox:1.36 -- \
        sh -c 'set -eu; tmp=/host/tmp/bretter-runner-image.tar; cat >"$tmp"; chroot /host ctr -n k8s.io images import /tmp/bretter-runner-image.tar; rm -f "$tmp"' \
        < "$runner_tar" 2>&1
    )"; then
      cleanup_node_debugger_pods "$node"
      if [ -n "$preload_output" ]; then
        echo "$preload_output" >&2
      fi
      fail "Failed to preload runner image on node $node."
    fi
    cleanup_node_debugger_pods "$node"
  done
}

ensure_golden_images_claim() {
  if kubectl -n "$NAMESPACE" get pvc golden-images >/dev/null 2>&1; then
    log "golden-images PVC already exists; skipping storage manifest apply."
    return
  fi

  if [ "$APPLY_GOLDEN_HOSTPATH" -eq 1 ]; then
    log "Applying golden-images hostPath PV/PVC for node $CONTROL_NODE"
    kubectl apply -f "$RENDERED_GOLDEN_HOSTPATH_MANIFEST"
    return
  fi

  if [ "$APPLY_GOLDEN_PVC" -eq 1 ]; then
    log "Applying golden-images PVC (ensure storageClassName is set correctly)"
    kubectl apply -f "$RENDERED_GOLDEN_PVC_MANIFEST"
    return
  fi

  fail "golden-images PVC is missing. Set APPLY_GOLDEN_HOSTPATH=1 or APPLY_GOLDEN_PVC=1, or create the claim manually."
}

ensure_tls_secret() {
  if [ "$TLS_ENABLED" -ne 1 ]; then
    return
  fi

  if kubectl -n "$NAMESPACE" get secret "$TLS_SECRET_NAME" >/dev/null 2>&1; then
    log "Using existing TLS secret $TLS_SECRET_NAME"
    return
  fi

  if [ -n "$TLS_CERT_FILE" ] && [ -n "$TLS_KEY_FILE" ]; then
    log "Creating TLS secret $TLS_SECRET_NAME from provided files"
    kubectl -n "$NAMESPACE" create secret tls "$TLS_SECRET_NAME" \
      --cert="$TLS_CERT_FILE" \
      --key="$TLS_KEY_FILE" \
      --dry-run=client -o yaml | kubectl apply -f -
    return
  fi

  log "Generating self-signed TLS cert for $NODE_EXTERNAL_HOST"
  local cert_tmp key_tmp sans
  cert_tmp="$(mktemp /tmp/bretter-tls-cert.XXXXXX.crt)"
  key_tmp="$(mktemp /tmp/bretter-tls-key.XXXXXX.key)"
  if [[ "$NODE_EXTERNAL_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    sans="IP:${NODE_EXTERNAL_HOST},IP:127.0.0.1,DNS:localhost"
  else
    sans="DNS:${NODE_EXTERNAL_HOST},DNS:localhost,IP:127.0.0.1"
  fi

  openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$key_tmp" \
    -out "$cert_tmp" \
    -days 825 \
    -subj "/CN=${NODE_EXTERNAL_HOST}" \
    -addext "subjectAltName=${sans}" >/dev/null 2>&1

  kubectl -n "$NAMESPACE" create secret tls "$TLS_SECRET_NAME" \
    --cert="$cert_tmp" \
    --key="$key_tmp" \
    --dry-run=client -o yaml | kubectl apply -f -

  rm -f "$cert_tmp" "$key_tmp"
}

reconcile_backend_data_pv() {
  if ! kubectl get pv backend-data-pv >/dev/null 2>&1; then
    return
  fi

  local current_hostnames current_hostpath recreate_reason
  current_hostnames="$(kubectl get pv backend-data-pv -o jsonpath='{range .spec.nodeAffinity.required.nodeSelectorTerms[*].matchExpressions[*]}{.key}={.values[*]}{"\n"}{end}' \
    | awk -F= '$1=="kubernetes.io/hostname"{print $2}')"
  current_hostpath="$(kubectl get pv backend-data-pv -o jsonpath='{.spec.hostPath.path}' 2>/dev/null || true)"
  recreate_reason=""

  if [ -n "$current_hostnames" ] && [[ "$current_hostnames" != *"$CONTROL_NODE"* ]]; then
    recreate_reason="node affinity ($current_hostnames) does not match $CONTROL_NODE"
  fi
  if [ "$current_hostpath" != "$BACKEND_DATA_HOSTPATH" ]; then
    if [ -n "$recreate_reason" ]; then
      recreate_reason="$recreate_reason; "
    fi
    recreate_reason="${recreate_reason}hostPath ($current_hostpath) does not match $BACKEND_DATA_HOSTPATH"
  fi
  if [ -z "$recreate_reason" ]; then
    return
  fi

  log "backend-data-pv ${recreate_reason}; recreating PV/PVC."
  kubectl -n "$NAMESPACE" scale deployment bretter-backend --replicas=0 >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" wait --for=delete pod -l app=bretter-backend --timeout=180s >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" delete pvc backend-data --ignore-not-found=true >/dev/null 2>&1 || true
  kubectl delete pv backend-data-pv --ignore-not-found=true >/dev/null 2>&1 || true
}

reconcile_postgres_data_pv() {
  if ! kubectl get pv backend-postgres-pv >/dev/null 2>&1; then
    return
  fi

  local current_hostnames current_hostpath recreate_reason
  current_hostnames="$(kubectl get pv backend-postgres-pv -o jsonpath='{range .spec.nodeAffinity.required.nodeSelectorTerms[*].matchExpressions[*]}{.key}={.values[*]}{"\n"}{end}' \
    | awk -F= '$1=="kubernetes.io/hostname"{print $2}')"
  current_hostpath="$(kubectl get pv backend-postgres-pv -o jsonpath='{.spec.hostPath.path}' 2>/dev/null || true)"
  recreate_reason=""

  if [ -n "$current_hostnames" ] && [[ "$current_hostnames" != *"$CONTROL_NODE"* ]]; then
    recreate_reason="node affinity ($current_hostnames) does not match $CONTROL_NODE"
  fi
  if [ "$current_hostpath" != "$POSTGRES_DATA_HOSTPATH" ]; then
    if [ -n "$recreate_reason" ]; then
      recreate_reason="$recreate_reason; "
    fi
    recreate_reason="${recreate_reason}hostPath ($current_hostpath) does not match $POSTGRES_DATA_HOSTPATH"
  fi
  if [ -z "$recreate_reason" ]; then
    return
  fi

  log "backend-postgres-pv ${recreate_reason}; recreating PV/PVC."
  kubectl -n "$NAMESPACE" scale deployment bretter-backend --replicas=0 >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" scale deployment bretter-postgres --replicas=0 >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" wait --for=delete pod -l app=bretter-postgres --timeout=180s >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" delete pvc backend-postgres-data --ignore-not-found=true >/dev/null 2>&1 || true
  kubectl delete pv backend-postgres-pv --ignore-not-found=true >/dev/null 2>&1 || true
}

postgres_database_url() {
  python3 - <<PY
import urllib.parse
user = urllib.parse.quote("${POSTGRES_USER}", safe="")
password = urllib.parse.quote("${POSTGRES_PASSWORD}", safe="")
db = urllib.parse.quote("${POSTGRES_DB}", safe="")
print(f"postgresql://{user}:{password}@bretter-postgres.${NAMESPACE}.svc.cluster.local:5432/{db}")
PY
}

ensure_postgres_secret() {
  if [ "$USE_EXTERNAL_SECRETS" -eq 1 ]; then
    log "Skipping local postgres secret creation (USE_EXTERNAL_SECRETS=1)."
    return
  fi
  log "Applying postgres app secret bretter-postgres"
  local database_url
  database_url="$(postgres_database_url)"
  kubectl -n "$NAMESPACE" create secret generic bretter-postgres \
    --from-literal=POSTGRES_USER="$POSTGRES_USER" \
    --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    --from-literal=POSTGRES_DB="$POSTGRES_DB" \
    --from-literal=BLABS_DATABASE_URL="$database_url" \
    --dry-run=client -o yaml | kubectl apply -f -
}

install_external_secrets_operator() {
  if [ "$USE_EXTERNAL_SECRETS" -ne 1 ]; then
    return
  fi
  if [ "$INSTALL_EXTERNAL_SECRETS_OPERATOR" -ne 1 ]; then
    log "Skipping External Secrets Operator install (INSTALL_EXTERNAL_SECRETS_OPERATOR=0)."
    return
  fi

  install_helm
  log "Installing External Secrets Operator in namespace ${EXTERNAL_SECRETS_NAMESPACE}..."
  helm repo add external-secrets https://charts.external-secrets.io >/dev/null 2>&1 || true
  helm repo update >/dev/null

  local helm_cmd=(upgrade --install "$EXTERNAL_SECRETS_RELEASE_NAME" external-secrets/external-secrets --namespace "$EXTERNAL_SECRETS_NAMESPACE" --create-namespace)
  if [ -n "$EXTERNAL_SECRETS_CHART_VERSION" ]; then
    helm_cmd+=(--version "${EXTERNAL_SECRETS_CHART_VERSION#v}")
  fi
  helm "${helm_cmd[@]}"

  local deploy
  while IFS= read -r deploy; do
    [ -n "$deploy" ] || continue
    kubectl -n "$EXTERNAL_SECRETS_NAMESPACE" rollout status "$deploy" --timeout=600s
  done < <(kubectl -n "$EXTERNAL_SECRETS_NAMESPACE" get deployment -l app.kubernetes.io/instance="$EXTERNAL_SECRETS_RELEASE_NAME" -o name)
}

apply_vault_cluster_secret_store() {
  if [ "$USE_EXTERNAL_SECRETS" -ne 1 ] || [ "$CREATE_VAULT_CLUSTER_SECRET_STORE" -ne 1 ]; then
    return
  fi
  log "Applying ClusterSecretStore ${EXTERNAL_SECRETS_STORE_NAME} (Vault)"
  kubectl apply -f - <<EOF
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: ${EXTERNAL_SECRETS_STORE_NAME}
spec:
  provider:
    vault:
      server: ${VAULT_ADDR}
      path: ${VAULT_KV_MOUNT}
      version: ${VAULT_KV_VERSION}
      auth:
        kubernetes:
          mountPath: ${VAULT_K8S_AUTH_PATH}
          role: ${VAULT_K8S_ROLE}
          serviceAccountRef:
            name: ${EXTERNAL_SECRETS_CONTROLLER_SERVICEACCOUNT_NAME}
            namespace: ${EXTERNAL_SECRETS_NAMESPACE}
EOF
}

wait_for_secret_ready() {
  local secret_name="$1"
  local timeout_seconds="$2"
  local waited=0
  while ! kubectl -n "$NAMESPACE" get secret "$secret_name" >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout_seconds" ]; then
      fail "Timed out waiting for secret ${secret_name} in namespace ${NAMESPACE}."
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

apply_external_secrets_bindings() {
  if [ "$USE_EXTERNAL_SECRETS" -ne 1 ]; then
    return
  fi
  log "Applying ExternalSecret for postgres credentials"
  kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: bretter-postgres
  namespace: ${NAMESPACE}
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: ${EXTERNAL_SECRETS_STORE_NAME}
    kind: ClusterSecretStore
  target:
    name: bretter-postgres
    creationPolicy: Owner
    template:
      engineVersion: v2
      type: Opaque
      data:
        BLABS_DATABASE_URL: postgresql://{{ .POSTGRES_USER }}:{{ .POSTGRES_PASSWORD }}@bretter-postgres.${NAMESPACE}.svc.cluster.local:5432/{{ .POSTGRES_DB }}
  data:
    - secretKey: POSTGRES_USER
      remoteRef:
        key: ${EXTERNAL_POSTGRES_REMOTE_KEY}
        property: ${EXTERNAL_POSTGRES_USER_PROPERTY}
    - secretKey: POSTGRES_PASSWORD
      remoteRef:
        key: ${EXTERNAL_POSTGRES_REMOTE_KEY}
        property: ${EXTERNAL_POSTGRES_PASSWORD_PROPERTY}
    - secretKey: POSTGRES_DB
      remoteRef:
        key: ${EXTERNAL_POSTGRES_REMOTE_KEY}
        property: ${EXTERNAL_POSTGRES_DB_PROPERTY}
EOF

  if [ "$EXTERNAL_PULL_SECRET_ENABLED" -eq 1 ]; then
    log "Applying ExternalSecret for image pull secret ghcr-creds"
    if [ -n "$EXTERNAL_PULL_SECRET_PROPERTY" ]; then
      kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ghcr-creds
  namespace: ${NAMESPACE}
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: ${EXTERNAL_SECRETS_STORE_NAME}
    kind: ClusterSecretStore
  target:
    name: ghcr-creds
    creationPolicy: Owner
    template:
      type: kubernetes.io/dockerconfigjson
  data:
    - secretKey: .dockerconfigjson
      remoteRef:
        key: ${EXTERNAL_PULL_SECRET_REMOTE_KEY}
        property: ${EXTERNAL_PULL_SECRET_PROPERTY}
EOF
    else
      kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ghcr-creds
  namespace: ${NAMESPACE}
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: ${EXTERNAL_SECRETS_STORE_NAME}
    kind: ClusterSecretStore
  target:
    name: ghcr-creds
    creationPolicy: Owner
    template:
      type: kubernetes.io/dockerconfigjson
  data:
    - secretKey: .dockerconfigjson
      remoteRef:
        key: ${EXTERNAL_PULL_SECRET_REMOTE_KEY}
EOF
    fi
  fi

  log "Waiting for ExternalSecret-backed secrets to sync..."
  wait_for_secret_ready bretter-postgres "$EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS"
  if [ "$EXTERNAL_PULL_SECRET_ENABLED" -eq 1 ]; then
    wait_for_secret_ready ghcr-creds "$EXTERNAL_SECRETS_WAIT_TIMEOUT_SECONDS"
  fi
}

ensure_pull_secret() {
  if [ "$USE_EXTERNAL_SECRETS" -eq 1 ] && [ "$EXTERNAL_PULL_SECRET_ENABLED" -eq 1 ]; then
    log "Skipping local pull secret creation (managed by ExternalSecret)."
    return
  fi
  if [ "$CREATE_PULL_SECRET" -eq 1 ]; then
    log "Updating ghcr-creds secret"
    if [ -n "${GHCR_USERNAME:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
      kubectl -n "$NAMESPACE" create secret docker-registry ghcr-creds \
        --docker-server=ghcr.io \
        --docker-username="$GHCR_USERNAME" \
        --docker-password="$GHCR_TOKEN" \
        --dry-run=client -o yaml | kubectl apply -f -
    else
      log "Using existing podman auth for ghcr-creds"
      local auth_path
      auth_path="$(podman info --format '{{.Host.AuthFile}}')"
      kubectl -n "$NAMESPACE" create secret generic ghcr-creds \
        --from-file=.dockerconfigjson="$auth_path" \
        --type=kubernetes.io/dockerconfigjson \
        --dry-run=client -o yaml | kubectl apply -f -
    fi
    return
  fi

  if kubectl -n "$NAMESPACE" get secret ghcr-creds >/dev/null 2>&1; then
    log "Using existing ghcr-creds secret"
    return
  fi

  log "Creating placeholder ghcr-creds secret (set CREATE_PULL_SECRET=1 for private registries)"
  kubectl -n "$NAMESPACE" create secret generic ghcr-creds \
    --from-literal=.dockerconfigjson='{"auths":{}}' \
    --type=kubernetes.io/dockerconfigjson \
    --dry-run=client -o yaml | kubectl apply -f -
}

apply_cleanup_automation() {
  if [ "$ENABLE_AUTOCLEANUP" -ne 1 ]; then
    log "Skipping cleanup automation (ENABLE_AUTOCLEANUP=0)."
    return
  fi

  log "Applying cleanup automation CronJob (schedule: $AUTOCLEANUP_SCHEDULE)"
  kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: bretter-maintenance
  namespace: ${NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: bretter-maintenance
  namespace: ${NAMESPACE}
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "persistentvolumeclaims"]
    verbs: ["get", "list", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "delete"]
  - apiGroups: ["cdi.kubevirt.io"]
    resources: ["datavolumes"]
    verbs: ["get", "list", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: bretter-maintenance
  namespace: ${NAMESPACE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: bretter-maintenance
subjects:
  - kind: ServiceAccount
    name: bretter-maintenance
    namespace: ${NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: bretter-maintenance-node-read
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: bretter-maintenance-node-read
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: bretter-maintenance-node-read
subjects:
  - kind: ServiceAccount
    name: bretter-maintenance
    namespace: ${NAMESPACE}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: bretter-cleanup
  namespace: ${NAMESPACE}
spec:
  schedule: "${AUTOCLEANUP_SCHEDULE}"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          serviceAccountName: bretter-maintenance
          nodeSelector:
            kubernetes.io/hostname: ${CONTROL_NODE}
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
              effect: NoSchedule
          imagePullSecrets:
            - name: ghcr-creds
          containers:
            - name: cleanup
              image: ${BACKEND_IMAGE}
              imagePullPolicy: IfNotPresent
              env:
                - name: BACKEND_DATA_HOSTPATH
                  value: "${BACKEND_DATA_HOSTPATH}"
                - name: POSTGRES_DATA_HOSTPATH
                  value: "${POSTGRES_DATA_HOSTPATH}"
                - name: GOLDEN_IMAGES_HOSTPATH
                  value: "${GOLDEN_IMAGES_HOSTPATH}"
              command:
                - /bin/bash
                - -lc
                - |
                  set -euo pipefail
                  NS="${NAMESPACE}"
                  HELPER_MAX_MINUTES=${AUTOCLEANUP_HELPER_MAX_AGE_MINUTES}
                  FINISHED_MAX_MINUTES=${AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES}
                  STALE_UPLOAD_MAX_MINUTES=${AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES}
                  RESTART_ALERT_COUNT=${AUTOCLEANUP_RESTART_ALERT_COUNT}
                  NODEFS_WARN_PCT=${AUTOCLEANUP_NODEFS_WARN_PCT}
                  NODEFS_CRITICAL_PCT=${AUTOCLEANUP_NODEFS_CRITICAL_PCT}
                  NODEFS_EMERGENCY_PCT=${AUTOCLEANUP_NODEFS_EMERGENCY_PCT}
                  PVC_WARN_PCT=${AUTOCLEANUP_PVC_WARN_PCT}
                  PVC_CRITICAL_PCT=${AUTOCLEANUP_PVC_CRITICAL_PCT}
                  PVC_EMERGENCY_PCT=${AUTOCLEANUP_PVC_EMERGENCY_PCT}
                  now_epoch="$(date +%s)"
                  pressure_mode=0

                  pct_used() {
                    local path="\$1"
                    df -Pk "\$path" 2>/dev/null | awk 'NR==2 {gsub(/%/, "", \$5); print \$5}'
                  }

                  check_path_alerts() {
                    local kind="\$1"
                    local label="\$2"
                    local path="\$3"
                    local warn="\$4"
                    local critical="\$5"
                    local emergency="\$6"
                    local target=""
                    local used_pct=""
                    if [ -d "/host-root\${path}" ]; then
                      target="/host-root\${path}"
                    elif [ -d "\$path" ]; then
                      target="\$path"
                    else
                      return 0
                    fi
                    used_pct="\$(pct_used "\$target" || true)"
                    if ! [[ "\$used_pct" =~ ^[0-9]+$ ]]; then
                      return 0
                    fi
                    if [ "\$used_pct" -ge "\$warn" ]; then
                      echo "ALERT[\$kind] \${label} usage is \${used_pct}% (warn=\${warn} critical=\${critical} emergency=\${emergency})"
                    fi
                    if [ "\$used_pct" -ge "\$critical" ] && [ "\$pressure_mode" -lt 1 ]; then
                      pressure_mode=1
                    fi
                    if [ "\$used_pct" -ge "\$emergency" ] && [ "\$pressure_mode" -lt 2 ]; then
                      pressure_mode=2
                    fi
                  }

                  check_path_alerts "nodefs" "control-node rootfs" "/" "\$NODEFS_WARN_PCT" "\$NODEFS_CRITICAL_PCT" "\$NODEFS_EMERGENCY_PCT"
                  check_path_alerts "nodefs" "control-node /var/lib" "/var/lib" "\$NODEFS_WARN_PCT" "\$NODEFS_CRITICAL_PCT" "\$NODEFS_EMERGENCY_PCT"
                  check_path_alerts "pvc" "backend data path" "\$BACKEND_DATA_HOSTPATH" "\$PVC_WARN_PCT" "\$PVC_CRITICAL_PCT" "\$PVC_EMERGENCY_PCT"
                  check_path_alerts "pvc" "postgres data path" "\$POSTGRES_DATA_HOSTPATH" "\$PVC_WARN_PCT" "\$PVC_CRITICAL_PCT" "\$PVC_EMERGENCY_PCT"
                  check_path_alerts "pvc" "golden image path" "\$GOLDEN_IMAGES_HOSTPATH" "\$PVC_WARN_PCT" "\$PVC_CRITICAL_PCT" "\$PVC_EMERGENCY_PCT"

                  if [ "\$pressure_mode" -eq 1 ]; then
                    FINISHED_MAX_MINUTES=\$(( FINISHED_MAX_MINUTES < 15 ? FINISHED_MAX_MINUTES : 15 ))
                    STALE_UPLOAD_MAX_MINUTES=\$(( STALE_UPLOAD_MAX_MINUTES < 60 ? STALE_UPLOAD_MAX_MINUTES : 60 ))
                    echo "ALERT[nodefs] critical storage pressure mode active; tightening cleanup thresholds."
                  elif [ "\$pressure_mode" -ge 2 ]; then
                    FINISHED_MAX_MINUTES=\$(( FINISHED_MAX_MINUTES < 5 ? FINISHED_MAX_MINUTES : 5 ))
                    STALE_UPLOAD_MAX_MINUTES=\$(( STALE_UPLOAD_MAX_MINUTES < 20 ? STALE_UPLOAD_MAX_MINUTES : 20 ))
                    echo "ALERT[nodefs] emergency storage pressure mode active; aggressive cleanup enabled."
                  fi

                  while IFS='|' read -r name phase created; do
                    [ -n "\$name" ] || continue
                    created_epoch="\$now_epoch"
                    if [ -n "\$created" ]; then
                      parsed="\$(date -d "\$created" +%s 2>/dev/null || true)"
                      if [ -n "\$parsed" ]; then
                        created_epoch="\$parsed"
                      fi
                    fi
                    age_min=\$(( (now_epoch - created_epoch) / 60 ))

                    if [[ "\$name" == image-sync-* ]]; then
                      if [[ "\$phase" == "Failed" || "\$phase" == "Succeeded" ]]; then
                        kubectl -n "\$NS" delete pod "\$name" --ignore-not-found=true >/dev/null || true
                        continue
                      fi
                      if [[ "\$phase" == "Running" && "\$age_min" -ge "\$HELPER_MAX_MINUTES" ]]; then
                        kubectl -n "\$NS" delete pod "\$name" --ignore-not-found=true >/dev/null || true
                        continue
                      fi
                    fi

                    if [[ "\$phase" == "Failed" || "\$phase" == "Succeeded" ]]; then
                      if [ "\$age_min" -ge "\$FINISHED_MAX_MINUTES" ]; then
                        kubectl -n "\$NS" delete pod "\$name" --ignore-not-found=true >/dev/null || true
                      fi
                    fi
                  done < <(
                    kubectl -n "\$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.phase}{"|"}{.metadata.creationTimestamp}{"\n"}{end}'
                  )

                  if kubectl -n "\$NS" get datavolumes.cdi.kubevirt.io >/dev/null 2>&1; then
                    while IFS='|' read -r dv_name dv_phase dv_created; do
                      [ -n "\$dv_name" ] || continue
                      dv_epoch="\$now_epoch"
                      if [ -n "\$dv_created" ]; then
                        parsed="\$(date -d "\$dv_created" +%s 2>/dev/null || true)"
                        if [ -n "\$parsed" ]; then
                          dv_epoch="\$parsed"
                        fi
                      fi
                      dv_age_min=\$(( (now_epoch - dv_epoch) / 60 ))
                      if [[ "\$dv_name" != img-upload-* ]]; then
                        continue
                      fi
                      if [[ "\$dv_phase" == "Succeeded" || "\$dv_phase" == "Failed" ]]; then
                        if [ "\$dv_age_min" -ge "\$STALE_UPLOAD_MAX_MINUTES" ]; then
                          kubectl -n "\$NS" delete datavolume "\$dv_name" --ignore-not-found=true >/dev/null || true
                          kubectl -n "\$NS" delete pvc "\$dv_name" --ignore-not-found=true >/dev/null || true
                        fi
                      fi
                    done < <(
                      kubectl -n "\$NS" get datavolumes.cdi.kubevirt.io -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.phase}{"|"}{.metadata.creationTimestamp}{"\n"}{end}' || true
                    )
                  fi

                  while IFS='|' read -r pod_name restart_counts; do
                    [ -n "\$pod_name" ] || continue
                    total_restarts=0
                    IFS=',' read -ra parts <<< "\$restart_counts"
                    for c in "\${parts[@]}"; do
                      [ -n "\$c" ] || continue
                      if [[ "\$c" =~ ^[0-9]+$ ]]; then
                        total_restarts=\$((total_restarts + c))
                      fi
                    done
                    if [ "\$total_restarts" -ge "\$RESTART_ALERT_COUNT" ]; then
                      echo "ALERT[restart] pod=\$pod_name restarts=\$total_restarts (threshold=\$RESTART_ALERT_COUNT)"
                    fi
                  done < <(
                    kubectl -n "\$NS" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{range .status.containerStatuses[*]}{.restartCount}{","}{end}{"\n"}{end}'
                  )

                  pressured_nodes="\$(
                    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.conditions[?(@.type=="DiskPressure")].status}{"\n"}{end}' \
                      | awk -F'|' '\$2=="True"{print \$1}' \
                      | xargs || true
                  )"
                  if [ -n "\$pressured_nodes" ]; then
                    echo "ALERT[nodefs] nodes reporting DiskPressure: \$pressured_nodes"
                  fi

                  for svc in \$(kubectl -n "\$NS" get svc -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^svc-' || true); do
                    pod="\$(kubectl -n "\$NS" get svc "\$svc" -o jsonpath='{.spec.selector.app}' 2>/dev/null || true)"
                    if [ -z "\$pod" ] || ! kubectl -n "\$NS" get pod "\$pod" >/dev/null 2>&1; then
                      kubectl -n "\$NS" delete svc "\$svc" --ignore-not-found=true >/dev/null || true
                    fi
                  done

                  for np in \$(kubectl -n "\$NS" get netpol -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^vm-' || true); do
                    app="\$(kubectl -n "\$NS" get netpol "\$np" -o jsonpath='{.spec.podSelector.matchLabels.app}' 2>/dev/null || true)"
                    if [ -z "\$app" ] || ! kubectl -n "\$NS" get pod "\$app" >/dev/null 2>&1; then
                      kubectl -n "\$NS" delete netpol "\$np" --ignore-not-found=true >/dev/null || true
                    fi
                  done
              volumeMounts:
                - name: host-root
                  mountPath: /host-root
                  readOnly: true
          volumes:
            - name: host-root
              hostPath:
                path: /
                type: Directory
EOF
}

apply_kubelet_serving_csr_autoapproval() {
  if [ "$ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL" -ne 1 ]; then
    log "Skipping kubelet-serving CSR auto-approval automation (ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL=0)."
    return
  fi

  log "Applying kubelet-serving CSR auto-approval CronJob (schedule: $KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE)"
  kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: bretter-kubelet-serving-csr-approver
  namespace: ${NAMESPACE}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: bretter-kubelet-serving-csr-approver
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["certificates.k8s.io"]
    resources: ["certificatesigningrequests"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["certificates.k8s.io"]
    resources: ["certificatesigningrequests/approval"]
    verbs: ["update", "patch"]
  - apiGroups: ["certificates.k8s.io"]
    resources: ["signers"]
    resourceNames: ["kubernetes.io/kubelet-serving"]
    verbs: ["approve"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: bretter-kubelet-serving-csr-approver
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: bretter-kubelet-serving-csr-approver
subjects:
  - kind: ServiceAccount
    name: bretter-kubelet-serving-csr-approver
    namespace: ${NAMESPACE}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: bretter-kubelet-serving-csr-approver
  namespace: ${NAMESPACE}
spec:
  schedule: "${KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE}"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 600
      template:
        spec:
          restartPolicy: OnFailure
          serviceAccountName: bretter-kubelet-serving-csr-approver
          nodeSelector:
            kubernetes.io/hostname: ${CONTROL_NODE}
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
              effect: NoSchedule
          imagePullSecrets:
            - name: ghcr-creds
          containers:
            - name: approver
              image: ${BACKEND_IMAGE}
              imagePullPolicy: IfNotPresent
              command:
                - /bin/bash
                - -lc
                - |
                  set -euo pipefail
                  python3 - <<'PY'
                  import base64
                  import json
                  import subprocess
                  import sys

                  from cryptography import x509
                  from cryptography.x509.oid import NameOID

                  SIGNER_NAME = "kubernetes.io/kubelet-serving"
                  approved_count = 0
                  skipped_count = 0


                  def run_json(cmd):
                    output = subprocess.check_output(cmd, text=True)
                    return json.loads(output)


                  def skip(message: str) -> None:
                    global skipped_count
                    skipped_count += 1
                    print(f"SKIP: {message}")


                  def allowed_names_for_node(node_name: str) -> tuple[set[str], set[str]]:
                    node = run_json(["kubectl", "get", "node", node_name, "-o", "json"])
                    allowed_dns = {node_name.lower()}
                    allowed_ips: set[str] = set()
                    for row in node.get("status", {}).get("addresses") or []:
                      addr_type = str(row.get("type") or "")
                      addr_value = str(row.get("address") or "").strip()
                      if not addr_value:
                        continue
                      if addr_type == "Hostname":
                        allowed_dns.add(addr_value.lower())
                      elif addr_type in {"InternalIP", "ExternalIP"}:
                        allowed_ips.add(addr_value)
                    return allowed_dns, allowed_ips


                  def approve_csr(csr_name: str) -> None:
                    global approved_count
                    subprocess.check_call(
                      ["kubectl", "certificate", "approve", csr_name],
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL,
                    )
                    approved_count += 1


                  try:
                    csr_list = run_json(["kubectl", "get", "csr", "-o", "json"]).get("items") or []
                  except Exception as exc:
                    print(f"ERROR: unable to fetch CSRs: {exc}", file=sys.stderr)
                    sys.exit(1)

                  for item in csr_list:
                    metadata = item.get("metadata") or {}
                    spec = item.get("spec") or {}
                    status = item.get("status") or {}
                    csr_name = str(metadata.get("name") or "")
                    signer_name = str(spec.get("signerName") or "")
                    if signer_name != SIGNER_NAME:
                      continue
                    if status.get("conditions"):
                      continue
                    username = str(spec.get("username") or "")
                    if not username.startswith("system:node:"):
                      skip(f"{csr_name}: unexpected requester {username!r}")
                      continue
                    node_name = username.split(":", 2)[-1]

                    try:
                      allowed_dns, allowed_ips = allowed_names_for_node(node_name)
                    except Exception as exc:
                      skip(f"{csr_name}: cannot fetch node {node_name!r}: {exc}")
                      continue

                    request_b64 = str(spec.get("request") or "")
                    if not request_b64:
                      skip(f"{csr_name}: empty request payload")
                      continue

                    try:
                      request_der = base64.b64decode(request_b64)
                      csr = x509.load_der_x509_csr(request_der)
                    except Exception as exc:
                      skip(f"{csr_name}: CSR parse failure: {exc}")
                      continue

                    cn_values = [attr.value for attr in csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
                    common_name = cn_values[0] if cn_values else ""
                    org_values = {attr.value for attr in csr.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)}
                    expected_cn = f"system:node:{node_name}"
                    if common_name != expected_cn or "system:nodes" not in org_values:
                      skip(
                        f"{csr_name}: subject mismatch cn={common_name!r} org={sorted(org_values)} expected_cn={expected_cn!r}"
                      )
                      continue

                    usages = {str(v).strip().lower() for v in (spec.get("usages") or [])}
                    if "server auth" not in usages:
                      skip(f"{csr_name}: missing 'server auth' usage ({sorted(usages)})")
                      continue

                    try:
                      san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
                    except x509.ExtensionNotFound:
                      skip(f"{csr_name}: missing SAN extension")
                      continue

                    dns_sans = {str(name).strip().lower() for name in san.get_values_for_type(x509.DNSName)}
                    ip_sans = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
                    uri_sans = list(san.get_values_for_type(x509.UniformResourceIdentifier))
                    email_sans = list(san.get_values_for_type(x509.RFC822Name))

                    if not dns_sans and not ip_sans:
                      skip(f"{csr_name}: SAN has no DNS/IP entries")
                      continue
                    if uri_sans or email_sans:
                      skip(f"{csr_name}: SAN contains unsupported URI/email entries")
                      continue
                    if any(name not in allowed_dns for name in dns_sans):
                      skip(
                        f"{csr_name}: DNS SAN not allowed dns={sorted(dns_sans)} allowed={sorted(allowed_dns)}"
                      )
                      continue
                    if any(ip not in allowed_ips for ip in ip_sans):
                      skip(
                        f"{csr_name}: IP SAN not allowed ip={sorted(ip_sans)} allowed={sorted(allowed_ips)}"
                      )
                      continue

                    try:
                      approve_csr(csr_name)
                      print(
                        f"APPROVED: {csr_name} node={node_name} dns={sorted(dns_sans)} ip={sorted(ip_sans)}"
                      )
                    except Exception as exc:
                      skip(f"{csr_name}: approval command failed: {exc}")

                  print(f"SUMMARY: approved={approved_count} skipped={skipped_count}")
                  PY
EOF
}

apply_manifests() {
  log "Ensuring namespace $NAMESPACE"
  kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || kubectl create ns "$NAMESPACE"

  ensure_tls_secret
  ensure_golden_images_claim
  reconcile_backend_data_pv
  reconcile_postgres_data_pv
  ensure_postgres_secret
  ensure_pull_secret
  apply_vault_cluster_secret_store
  apply_external_secrets_bindings
  apply_cleanup_automation
  apply_kubelet_serving_csr_autoapproval

  if [ -f "$ROOT_DIR/runner/spice-embed.html" ]; then
    log "Updating spice-embed ConfigMap"
    kubectl -n "$NAMESPACE" create configmap spice-embed \
      --from-file=spice-embed.html="$ROOT_DIR/runner/spice-embed.html" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi

  apply_base_release_with_helm

  log "Waiting for rollout"
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-postgres --timeout=300s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=300s

  prune_bootstrap_admin_env
}

prune_bootstrap_admin_env() {
  if [ "$PRUNE_BOOTSTRAP_ADMIN_ENV" -ne 1 ]; then
    log "Skipping bootstrap env pruning (PRUNE_BOOTSTRAP_ADMIN_ENV=0)."
    return
  fi

  local env_names patch_payload
  env_names="$(
    kubectl -n "$NAMESPACE" get deployment bretter-backend \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="backend")].env[*]}{.name}{"\n"}{end}' \
      2>/dev/null || true
  )"
  if ! printf '%s\n' "$env_names" | grep -qx 'BLABS_ADMIN_DEFAULT_PASSWORD'; then
    log "Bootstrap admin env already pruned from bretter-backend deployment."
    return
  fi

  patch_payload="$(cat <<'EOF'
spec:
  template:
    spec:
      containers:
      - name: backend
        env:
        - name: BLABS_ADMIN_DEFAULT_PASSWORD
          $patch: delete
EOF
)"

  log "Pruning bootstrap admin secret from bretter-backend deployment env..."
  kubectl -n "$NAMESPACE" patch deployment bretter-backend --type=strategic --patch "$patch_payload" >/dev/null
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s

  env_names="$(
    kubectl -n "$NAMESPACE" get deployment bretter-backend \
      -o jsonpath='{range .spec.template.spec.containers[?(@.name=="backend")].env[*]}{.name}{"\n"}{end}' \
      2>/dev/null || true
  )"
  if printf '%s\n' "$env_names" | grep -qx 'BLABS_ADMIN_DEFAULT_PASSWORD'; then
    fail "Failed to prune BLABS_ADMIN_DEFAULT_PASSWORD from bretter-backend deployment."
  fi
  log "Verified bootstrap admin secret env is pruned from bretter-backend deployment."
}

run_post_deploy_api_health_check() {
  if [ "$RUN_POST_DEPLOY_API_HEALTH_CHECK" -ne 1 ]; then
    log "Skipping post-deploy API health check (RUN_POST_DEPLOY_API_HEALTH_CHECK=0)."
    return
  fi
  if ! command -v curl >/dev/null 2>&1; then
    fail "curl is required for post-deploy API health checks."
  fi

  local url attempts i response
  local curl_tls_flags=()
  url="${PUBLIC_SCHEME}://${NODE_EXTERNAL_HOST}:30073/api/health"
  attempts=$(((POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS + 4) / 5))
  if [ "$attempts" -lt 1 ]; then
    attempts=1
  fi
  if [ "$PUBLIC_SCHEME" = "https" ]; then
    curl_tls_flags+=(--insecure)
  fi

  log "Running post-deploy API health check at ${url}..."
  for ((i = 1; i <= attempts; i++)); do
    response="$(curl -fsS --max-time 10 "${curl_tls_flags[@]}" "$url" 2>/dev/null || true)"
    if printf '%s' "$response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      log "Post-deploy API health check passed."
      return
    fi
    sleep 5
  done

  fail "Post-deploy API health check failed at ${url}."
}

run_post_deploy_synthetic_check() {
  if [ "$RUN_POST_DEPLOY_SYNTHETIC_CHECK" -ne 1 ]; then
    log "Skipping post-deploy synthetic validation (RUN_POST_DEPLOY_SYNTHETIC_CHECK=0)."
    return
  fi

  local username_b64 password_b64 synthetic_api_base synthetic_verify_tls
  username_b64="$(printf '%s' "$SYNTHETIC_CHECK_USERNAME" | base64 -w0)"
  password_b64="$(printf '%s' "$SYNTHETIC_CHECK_PASSWORD" | base64 -w0)"
  synthetic_api_base="http://bretter-backend.${NAMESPACE}.svc.cluster.local:8000"
  synthetic_verify_tls="1"
  if [ "$TLS_ENABLED" -eq 1 ]; then
    synthetic_api_base="https://bretter-backend.${NAMESPACE}.svc.cluster.local:8000"
    synthetic_verify_tls="0"
  fi

  log "Running post-deploy synthetic validation job..."
  kubectl -n "$NAMESPACE" delete job bretter-post-deploy-check --ignore-not-found=true >/dev/null 2>&1 || true
  kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: bretter-post-deploy-check
  namespace: ${NAMESPACE}
spec:
  ttlSecondsAfterFinished: 1800
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: ghcr-creds
      containers:
        - name: synthetic-check
          image: ${BACKEND_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: API_BASE
              value: "${synthetic_api_base}"
            - name: SYNTHETIC_VERIFY_TLS
              value: "${synthetic_verify_tls}"
            - name: SYNTHETIC_USERNAME_B64
              value: "${username_b64}"
            - name: SYNTHETIC_PASSWORD_B64
              value: "${password_b64}"
            - name: SYNTHETIC_TIMEOUT_SECONDS
              value: "${SYNTHETIC_CHECK_TIMEOUT_SECONDS}"
            - name: SYNTHETIC_REQUIRE_TEMPLATES
              value: "${SYNTHETIC_CHECK_REQUIRE_TEMPLATES}"
          command:
            - /bin/bash
            - -lc
            - |
              set -euo pipefail
              python3 - <<'PY'
              import base64
              import json
              import os
              import sys
              import time
              
              import requests
              
              API_BASE = str(os.environ.get("API_BASE") or "").rstrip("/")
              USERNAME = base64.b64decode(str(os.environ.get("SYNTHETIC_USERNAME_B64") or "")).decode("utf-8")
              PASSWORD = base64.b64decode(str(os.environ.get("SYNTHETIC_PASSWORD_B64") or "")).decode("utf-8")
              TIMEOUT_SECONDS = max(60, int(os.environ.get("SYNTHETIC_TIMEOUT_SECONDS") or "420"))
              REQUIRE_TEMPLATES = str(os.environ.get("SYNTHETIC_REQUIRE_TEMPLATES") or "0") == "1"
              VERIFY_TLS = str(os.environ.get("SYNTHETIC_VERIFY_TLS") or "1") == "1"
              DEADLINE = time.time() + TIMEOUT_SECONDS
              SINGLE_LAB_LIMIT_MESSAGE = "you already have a virtual lab running"
              
              if not API_BASE:
                  print("FAIL: API_BASE is required", file=sys.stderr)
                  sys.exit(1)
              
              session = requests.Session()
              session.headers.update({"Accept": "application/json"})
              
              
              def fail(message: str) -> None:
                  print(f"FAIL: {message}", file=sys.stderr)
                  sys.exit(1)
              
              
              def request_json(method: str, path: str, **kwargs):
                  url = path if path.startswith("http://") or path.startswith("https://") else f"{API_BASE}{path}"
                  response = session.request(method=method, url=url, timeout=20, verify=VERIFY_TLS, **kwargs)
                  return response
              
              
              def wait_for_instance(path: str, instance_id: str, allowed_stages: set[str]) -> dict:
                  while time.time() < DEADLINE:
                      response = request_json("GET", path)
                      if response.status_code != 200:
                          time.sleep(2)
                          continue
                      rows = response.json() or []
                      for row in rows:
                          if str(row.get("id")) != instance_id:
                              continue
                          stage = str(row.get("status_stage") or row.get("status") or "").lower()
                          if stage in allowed_stages:
                              return row
                      time.sleep(3)
                  fail(f"timeout while waiting for instance {instance_id} at {path}")
                  return {}
              
              
              def wait_until_vm_released(instance_id: str) -> None:
                  while time.time() < DEADLINE:
                      response = request_json("GET", "/user/pods")
                      if response.status_code != 200:
                          time.sleep(2)
                          continue
                      rows = response.json() or []
                      row = next((item for item in rows if str(item.get("id")) == instance_id), None)
                      if row is None:
                          return
                      status = str(row.get("status") or "").lower()
                      if status in {"stopped", "completed", "failed"}:
                          return
                      time.sleep(3)
                  fail("VM slot did not release in time after delete.")
              
              
              def start_container_with_retry(template_id: str) -> requests.Response:
                  while time.time() < DEADLINE:
                      response = request_json("POST", f"/user/container-templates/{template_id}/start")
                      if response.status_code == 201:
                          return response
                      detail = ""
                      try:
                          payload = response.json()
                          detail = str(payload.get("detail") or "")
                      except Exception:
                          detail = response.text
                      if response.status_code == 429 and SINGLE_LAB_LIMIT_MESSAGE in detail.lower():
                          time.sleep(3)
                          continue
                      fail(f"container start failed ({response.status_code}): {detail[:300]}")
                  fail("timeout waiting to acquire launch slot for container start")
                  return requests.Response()
              
              
              health = request_json("GET", "/health")
              if health.status_code != 200:
                  fail(f"health check failed ({health.status_code}): {health.text[:300]}")
              health_payload = health.json() if "application/json" in str(health.headers.get("content-type") or "") else {}
              if str(health_payload.get("status") or "").lower() != "ok":
                  fail(f"unexpected health payload: {json.dumps(health_payload)[:300]}")
              
              login = request_json("POST", "/auth/login", json={"username": USERNAME, "password": PASSWORD})
              if login.status_code != 200:
                  fail(f"login failed ({login.status_code}): {login.text[:300]}")
              
              vm_templates_resp = request_json("GET", "/user/templates")
              if vm_templates_resp.status_code != 200:
                  fail(f"failed to fetch VM templates ({vm_templates_resp.status_code})")
              vm_templates = vm_templates_resp.json() or []
              
              ct_templates_resp = request_json("GET", "/user/container-templates")
              if ct_templates_resp.status_code != 200:
                  fail(f"failed to fetch container templates ({ct_templates_resp.status_code})")
              container_templates = ct_templates_resp.json() or []
              
              if not vm_templates or not container_templates:
                  message = (
                      "synthetic check skipped: missing enabled VM or container templates "
                      f"(vm={len(vm_templates)} container={len(container_templates)})"
                  )
                  if REQUIRE_TEMPLATES:
                      fail(message)
                  print(f"SKIP: {message}")
                  request_json("POST", "/auth/logout")
                  sys.exit(0)
              
              vm_template_id = str(vm_templates[0]["id"])
              container_template_id = str(container_templates[0]["id"])
              
              vm_start = request_json("POST", f"/user/templates/{vm_template_id}/start")
              if vm_start.status_code != 201:
                  fail(f"VM start failed ({vm_start.status_code}): {vm_start.text[:300]}")
              vm_id = str((vm_start.json() or {}).get("id") or "")
              if not vm_id:
                  fail("VM start response did not include instance id")
              
              vm_row = wait_for_instance("/user/pods", vm_id, {"pending", "building", "starting", "running", "queued"})
              if not str(vm_row.get("console_url") or "").strip():
                  fail("VM instance did not publish console_url")
              
              vm_delete = request_json("DELETE", f"/user/pods/{vm_id}")
              if vm_delete.status_code not in {204, 404}:
                  fail(f"VM delete failed ({vm_delete.status_code}): {vm_delete.text[:300]}")
              wait_until_vm_released(vm_id)
              
              container_start = start_container_with_retry(container_template_id)
              container_id = str((container_start.json() or {}).get("id") or "")
              if not container_id:
                  fail("container start response did not include instance id")
              
              wait_for_instance("/user/containers", container_id, {"pending", "building", "starting", "running", "queued"})
              
              connect_token = request_json("POST", f"/user/containers/{container_id}/connect-token")
              if connect_token.status_code != 200:
                  fail(f"container connect-token failed ({connect_token.status_code}): {connect_token.text[:300]}")
              connect_url = str((connect_token.json() or {}).get("connect_url") or "").strip()
              if not connect_url:
                  fail("container connect-token response missing connect_url")
              
              bridge = request_json("GET", f"/user/containers/{container_id}/connect/__blabs_idle_bridge.js")
              if bridge.status_code != 200:
                  fail(f"container connect bridge fetch failed ({bridge.status_code}): {bridge.text[:300]}")
              if "Still using this lab?" not in bridge.text:
                  fail("container connect bridge did not include idle prompt content")
              
              container_delete = request_json("DELETE", f"/user/containers/{container_id}")
              if container_delete.status_code not in {204, 404}:
                  fail(f"container delete failed ({container_delete.status_code}): {container_delete.text[:300]}")
              
              request_json("POST", "/auth/logout")
              print("Synthetic validation passed: login -> VM launch -> container launch/connect/idle prompt bridge -> delete.")
              PY
EOF

  if ! kubectl -n "$NAMESPACE" wait --for=condition=complete job/bretter-post-deploy-check --timeout="${SYNTHETIC_CHECK_TIMEOUT_SECONDS}s"; then
    kubectl -n "$NAMESPACE" logs job/bretter-post-deploy-check --all-containers=true || true
    fail "Post-deploy synthetic validation job failed."
  fi
  kubectl -n "$NAMESPACE" logs job/bretter-post-deploy-check --all-containers=true || true
}

ensure_cluster_runtime_context() {
  if ! command -v kubectl >/dev/null 2>&1; then
    fail "kubectl is required for selected phases. Run SETUP_PHASES=prereqs first or install kubectl manually."
  fi
  ensure_kubeconfig
  detect_control_node
  detect_node_external_host
}

log_runtime_configuration() {
  log "Selected setup phases: $SETUP_PHASES (dry run: $SETUP_DRY_RUN)"
  log "Using control node: $CONTROL_NODE"
  log "Using node external host for API/UI: $NODE_EXTERNAL_HOST"
  if [ -n "$RUNNER_NODE_SELECTOR_VALUE" ]; then
    log "Pinning runner pods to node: $RUNNER_NODE_SELECTOR_VALUE"
  else
    log "Runner pods are not node-pinned (scheduler can choose any eligible node)."
  fi
  log "Using public scheme: $PUBLIC_SCHEME"
  log "Using TLS secret: $TLS_SECRET_NAME (enabled=$TLS_ENABLED)"
  if [ "$ADMIN_BOOTSTRAP_PASSWORD_GENERATED" -eq 1 ]; then
    log "Generated one-time bootstrap admin secret for username 'admin' (used only if no admin user exists)."
    if [ -n "$ADMIN_BOOTSTRAP_SECRET_FILE" ]; then
      log "Bootstrap admin secret saved to: $ADMIN_BOOTSTRAP_SECRET_FILE (permissions 600)."
    fi
  else
    log "Using provided ADMIN_BOOTSTRAP_PASSWORD for bootstrap admin (applies only when no admin user exists)."
  fi
  log "Using Helm release: $HELM_RELEASE_NAME (chart: $HELM_CHART_DIR)"
  log "Using backend data hostPath: $BACKEND_DATA_HOSTPATH"
  log "Using postgres data hostPath: $POSTGRES_DATA_HOSTPATH"
  log "Using golden images hostPath: $GOLDEN_IMAGES_HOSTPATH"
  log "External Secrets enabled: $USE_EXTERNAL_SECRETS (store: $EXTERNAL_SECRETS_STORE_NAME)"
  if [ "$USE_EXTERNAL_SECRETS" -eq 1 ]; then
    log "External Secrets operator install: $INSTALL_EXTERNAL_SECRETS_OPERATOR (namespace: $EXTERNAL_SECRETS_NAMESPACE release: $EXTERNAL_SECRETS_RELEASE_NAME)"
    log "Vault ClusterSecretStore auto-create: $CREATE_VAULT_CLUSTER_SECRET_STORE"
    log "External pull secret management: $EXTERNAL_PULL_SECRET_ENABLED"
  fi
  log "Using VM storage class: $VM_STORAGE_CLASS"
  log "Using VM network backend: $VM_NET_BACKEND"
  log "VM runner privileged override: $VM_RUNNER_PRIVILEGED"
  log "VM console external traffic policy: $VM_CONSOLE_EXTERNAL_TRAFFIC_POLICY"
  log "VM console source CIDRs: ${VM_CONSOLE_SOURCE_CIDRS:-unrestricted}"
  log "VM console ticket length: $VM_CONSOLE_TICKET_LENGTH"
  log "Backend NodePort exposure enabled: $BACKEND_NODEPORT_ENABLED (nodePort: $BACKEND_NODEPORT)"
  log "Container ingress enabled: $CONTAINER_INGRESS_ENABLED (base domain: ${CONTAINER_INGRESS_BASE_DOMAIN:-disabled}, class: ${CONTAINER_INGRESS_CLASS:-default})"
  log "Container image pre-pull enabled: $CONTAINER_IMAGE_PREPULL_ENABLED (timeout: ${CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS}s)"
  log "Container allowed registries: $CONTAINER_ALLOWED_REGISTRIES"
  log "Container signature verification enabled: $CONTAINER_SIGNATURE_VERIFICATION_ENABLED (key: ${CONTAINER_SIGNATURE_KEY_REF:-keyless})"
  log "Container scanning enabled: $CONTAINER_SCAN_ENABLED (interval: ${CONTAINER_SCAN_INTERVAL_MINUTES}m severity: ${CONTAINER_SCAN_SEVERITY})"
  log "Container start queue enabled: $CONTAINER_START_QUEUE_ENABLED (base/max backoff: ${CONTAINER_START_QUEUE_BASE_DELAY_SECONDS}s/${CONTAINER_START_QUEUE_MAX_DELAY_SECONDS}s)"
  log "Backend production profile: $PRODUCTION_PROFILE"
  log "CORS enterprise profile: $CORS_ENTERPRISE_PROFILE (origins: ${CORS_ALLOWED_ORIGINS:-default})"
  log "Auth login rate limit window/max/lockout: ${AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS}s/${AUTH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS}/${AUTH_LOGIN_LOCKOUT_SECONDS}s"
  log "Bootstrap admin env pruning: $PRUNE_BOOTSTRAP_ADMIN_ENV"
  log "Strict upstream TLS defaults: VM insecure=$VM_CONNECT_INSECURE_TLS container insecure=$CONTAINER_CONNECT_INSECURE_TLS"
  log "CPU manager static on all nodes: $CPU_MANAGER_STATIC"
  log "CDI install enabled: $INSTALL_CDI (version: $CDI_VERSION)"
  log "Using CDI upload proxy URL: ${CDI_UPLOAD_PROXY_URL:-disabled}"
  log "Monitoring stack enabled: $ENABLE_MONITORING (namespace: $MONITORING_NAMESPACE release: $MONITORING_RELEASE_NAME chart: ${MONITORING_CHART_VERSION})"
  log "Metrics-server enabled: $ENABLE_METRICS_SERVER (insecure kubelet TLS: $METRICS_SERVER_INSECURE_TLS)"
  log "Admission policies enabled: $ENABLE_ADMISSION_POLICIES (install Kyverno: $INSTALL_KYVERNO namespace: $KYVERNO_NAMESPACE release: $KYVERNO_RELEASE_NAME chart: ${KYVERNO_CHART_VERSION})"
  log "Kubelet-serving CSR auto-approval enabled: $ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL (schedule: $KUBELET_SERVING_CSR_AUTOAPPROVAL_SCHEDULE)"
  log "Mutable image tags allowed: $ALLOW_MUTABLE_IMAGE_TAGS"
  log "Post-deploy API health check enabled: $RUN_POST_DEPLOY_API_HEALTH_CHECK (timeout: ${POST_DEPLOY_API_HEALTH_TIMEOUT_SECONDS}s)"
  log "Post-deploy synthetic check enabled: $RUN_POST_DEPLOY_SYNTHETIC_CHECK (timeout: ${SYNTHETIC_CHECK_TIMEOUT_SECONDS}s)"
  if [ "$SYNTHETIC_CHECK_AUTO_DISABLED" -eq 1 ]; then
    log "Synthetic check auto-disabled: set SYNTHETIC_CHECK_PASSWORD to run authenticated synthetic validation on existing deployments."
  elif [ "$SYNTHETIC_CHECK_PASSWORD_AUTOSET" -eq 1 ]; then
    log "Synthetic check password was auto-set from the bootstrap admin secret."
  fi
  log "Longhorn tuning enabled: $LONGHORN_TUNE"
  if [ -n "$LONGHORN_DEFAULT_DATA_PATH" ]; then
    log "Longhorn default data path override: $LONGHORN_DEFAULT_DATA_PATH"
  fi
  log "Cleanup automation enabled: $ENABLE_AUTOCLEANUP (schedule: $AUTOCLEANUP_SCHEDULE)"
  log "Cleanup alert thresholds: nodefs ${AUTOCLEANUP_NODEFS_WARN_PCT}/${AUTOCLEANUP_NODEFS_CRITICAL_PCT}/${AUTOCLEANUP_NODEFS_EMERGENCY_PCT}% pvc ${AUTOCLEANUP_PVC_WARN_PCT}/${AUTOCLEANUP_PVC_CRITICAL_PCT}/${AUTOCLEANUP_PVC_EMERGENCY_PCT}%"
  log "Storage guard thresholds: warn<${SETUP_WARN_FREE_GIB}Gi, fail<${SETUP_MIN_FREE_GIB}Gi"
}

run_phase_prereqs() {
  if [ "$SETUP_DRY_RUN" -eq 1 ]; then
    log "DRY_RUN: would execute prereqs phase."
    return
  fi

  require_apt
  install_base_packages
  install_kubectl
  ensure_kubeconfig
  tune_longhorn_for_phase2
  detect_control_node
  detect_node_external_host
  enable_cpu_manager_static_all_nodes
  ensure_cdi_installed
  configure_cdi_upload_proxy_url
  install_external_secrets_operator
  prepare_rendered_manifests
}

run_phase_deploy() {
  if [ "$SETUP_DRY_RUN" -eq 1 ]; then
    log "DRY_RUN: would execute deploy phase."
    return
  fi

  if [ "$PUSH_IMAGES" -eq 1 ] || [ "$LOAD_LOCAL_IMAGES" -eq 1 ]; then
    install_node
    install_podman
  fi

  if [ "$PUSH_IMAGES" -eq 1 ]; then
    ensure_ghcr_login
    CREATE_PULL_SECRET=1
  fi

  if [ "$PUSH_IMAGES" -eq 1 ] || [ "$LOAD_LOCAL_IMAGES" -eq 1 ]; then
    build_images
  fi

  if [ "$PUSH_IMAGES" -eq 1 ]; then
    push_images
  fi

  if [ "$LOAD_LOCAL_IMAGES" -eq 1 ]; then
    load_images_into_containerd
  fi

  if [ "$CREATE_PULL_SECRET" -eq 1 ] && ! command -v podman >/dev/null 2>&1; then
    install_podman
  fi

  apply_manifests
}

run_phase_postdeploy() {
  if [ "$SETUP_DRY_RUN" -eq 1 ]; then
    log "DRY_RUN: would execute postdeploy phase."
    return
  fi

  install_metrics_server
  install_monitoring_stack
  install_kyverno
  apply_admission_policies
  patch_default_pvc_alert_exclusions
  apply_monitoring_alert_rules
  run_post_deploy_api_health_check
  run_post_deploy_synthetic_check
}

main() {
  validate_public_scheme
  validate_tls_config
  validate_setup_phase_config
  validate_image_reference_policy
  validate_preload_config
  validate_longhorn_tuning_config
  validate_autocleanup_config
  validate_storage_guard_config
  validate_vm_network_config
  validate_container_runtime_config
  validate_auth_and_cors_config
  validate_postgres_config
  validate_external_secrets_config
  validate_cdi_upload_config
  validate_cpu_manager_config
  validate_monitoring_config
  validate_metrics_server_config
  validate_admission_policy_config
  validate_kubelet_serving_csr_autoapproval_config
  configure_admin_bootstrap_credentials
  validate_post_deploy_api_health_config
  validate_synthetic_check_config
  validate_helm_deploy_config

  if phase_enabled prereqs; then
    run_phase_prereqs
  fi
  if [ "$SETUP_DRY_RUN" -eq 1 ]; then
    if phase_enabled deploy; then
      run_phase_deploy
    fi
    if phase_enabled postdeploy; then
      run_phase_postdeploy
    fi
    log "Done (dry run)."
    return
  fi

  if ! phase_enabled prereqs && { phase_enabled deploy || phase_enabled postdeploy; }; then
    ensure_cluster_runtime_context
  fi
  if ! phase_enabled prereqs && phase_enabled deploy; then
    ensure_cdi_installed
    configure_cdi_upload_proxy_url
    install_external_secrets_operator
    prepare_rendered_manifests
  fi

  log_runtime_configuration

  if phase_enabled deploy; then
    run_storage_preflight_checks
    run_phase_deploy
  fi
  if phase_enabled postdeploy; then
    run_phase_postdeploy
  fi

  log "Done."
}

main "$@"
