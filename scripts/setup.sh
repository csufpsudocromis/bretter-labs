#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
BACKEND_IMAGE="${BACKEND_IMAGE:-ghcr.io/csufpsudocromis/bretter-backend:latest}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-ghcr.io/csufpsudocromis/bretter-frontend:latest}"
RUNNER_IMAGE="${RUNNER_IMAGE:-ghcr.io/csufpsudocromis/win-vm-runner:latest}"
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
VM_NET_BACKEND="${VM_NET_BACKEND:-tap-nat}"
CONTAINER_INGRESS_ENABLED="${CONTAINER_INGRESS_ENABLED:-0}"
CONTAINER_INGRESS_CLASS="${CONTAINER_INGRESS_CLASS:-}"
CONTAINER_INGRESS_BASE_DOMAIN="${CONTAINER_INGRESS_BASE_DOMAIN:-}"
CONTAINER_INGRESS_ANNOTATIONS_JSON="${CONTAINER_INGRESS_ANNOTATIONS_JSON:-{}}"
CONTAINER_IMAGE_PREPULL_ENABLED="${CONTAINER_IMAGE_PREPULL_ENABLED:-1}"
CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS="${CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS:-45}"
BACKEND_DATA_HOSTPATH="${BACKEND_DATA_HOSTPATH:-/var/lib/bretter-labs/backend-data}"
GOLDEN_IMAGES_HOSTPATH="${GOLDEN_IMAGES_HOSTPATH:-/var/lib/bretter-labs/golden-images}"
POSTGRES_DATA_HOSTPATH="${POSTGRES_DATA_HOSTPATH:-/var/lib/bretter-labs/postgres-data}"
POSTGRES_USER="${POSTGRES_USER:-bretter}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-bretterpass}"
POSTGRES_DB="${POSTGRES_DB:-bretterlabs}"
CDI_NAMESPACE="${CDI_NAMESPACE:-cdi}"
INSTALL_CDI="${INSTALL_CDI:-1}"
CDI_VERSION="${CDI_VERSION:-v1.61.0}"
CDI_UPLOAD_NODEPORT="${CDI_UPLOAD_NODEPORT:-30443}"
CDI_UPLOAD_PROXY_URL="${CDI_UPLOAD_PROXY_URL:-}"
CPU_MANAGER_STATIC="${CPU_MANAGER_STATIC:-0}"
ENABLE_MONITORING="${ENABLE_MONITORING:-1}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
MONITORING_RELEASE_NAME="${MONITORING_RELEASE_NAME:-kube-prometheus-stack}"
MONITORING_CHART_VERSION="${MONITORING_CHART_VERSION:-}"
MONITORING_RESTART_ALERT_COUNT="${MONITORING_RESTART_ALERT_COUNT:-3}"
MONITORING_DV_STALE_MINUTES="${MONITORING_DV_STALE_MINUTES:-60}"
MONITORING_WARM_POOL_MIN_READY="${MONITORING_WARM_POOL_MIN_READY:-1}"
HELM_VERSION="${HELM_VERSION:-v3.15.4}"
ENABLE_METRICS_SERVER="${ENABLE_METRICS_SERVER:-1}"
METRICS_SERVER_MANIFEST_URL="${METRICS_SERVER_MANIFEST_URL:-https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml}"

RENDERED_APP_MANIFEST=""
RENDERED_GOLDEN_HOSTPATH_MANIFEST=""
RENDERED_GOLDEN_PVC_MANIFEST=""

log() {
  echo "==> $*"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
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
}

validate_postgres_config() {
  [ -n "$POSTGRES_USER" ] || fail "POSTGRES_USER cannot be empty."
  [ -n "$POSTGRES_PASSWORD" ] || fail "POSTGRES_PASSWORD cannot be empty."
  [ -n "$POSTGRES_DB" ] || fail "POSTGRES_DB cannot be empty."
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
  if [ -n "$MONITORING_CHART_VERSION" ] && ! [[ "$MONITORING_CHART_VERSION" =~ ^v?[0-9]+(\.[0-9]+){2}$ ]]; then
    fail "MONITORING_CHART_VERSION must look like X.Y.Z (or be empty for latest chart)."
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
  if [ "$ENABLE_METRICS_SERVER" -eq 1 ] && [ -z "$METRICS_SERVER_MANIFEST_URL" ]; then
    fail "METRICS_SERVER_MANIFEST_URL cannot be empty when ENABLE_METRICS_SERVER=1."
  fi
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
    "${RENDERED_APP_MANIFEST:-}" \
    "${RENDERED_GOLDEN_HOSTPATH_MANIFEST:-}" \
    "${RENDERED_GOLDEN_PVC_MANIFEST:-}"
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
  local i
  for i in $(seq 1 30); do
    if kubectl api-resources --api-group=upload.cdi.kubevirt.io 2>/dev/null | awk '{print $1}' | grep -qx "uploadtokenrequests"; then
      return
    fi
    sleep 2
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

install_metrics_server() {
  if [ "$ENABLE_METRICS_SERVER" -ne 1 ]; then
    log "Skipping metrics-server install (ENABLE_METRICS_SERVER=0)."
    return
  fi

  log "Installing metrics-server from ${METRICS_SERVER_MANIFEST_URL}..."
  kubectl apply -f "$METRICS_SERVER_MANIFEST_URL"

  local args
  args="$(kubectl -n kube-system get deployment metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null || true)"
  if ! grep -q -- "--kubelet-insecure-tls" <<<"$args"; then
    kubectl -n kube-system patch deployment metrics-server --type='json' \
      -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
  fi
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=600s
}

apply_monitoring_alert_rules() {
  if [ "$ENABLE_MONITORING" -ne 1 ]; then
    return
  fi

  log "Applying monitoring alert rules..."
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
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}"}
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
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}"}
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
                  kubelet_volume_stats_available_bytes{namespace="${NAMESPACE}"}
                  /
                  kubelet_volume_stats_capacity_bytes{namespace="${NAMESPACE}"}
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

render_manifest_template() {
  local input="$1"
  local output="$2"

  local ns control_node node_external_host backend_image frontend_image runner_image public_scheme tls_secret_name
  local runner_node_selector_value
  local vm_storage_class backend_data_hostpath golden_images_hostpath postgres_data_hostpath postgres_user postgres_password postgres_db cdi_upload_proxy_url
  local windows_machine_type windows_efi_enabled windows_cpu_model linux_machine_type linux_efi_enabled linux_cpu_model vm_net_backend
  local container_ingress_enabled container_ingress_class container_ingress_base_domain container_ingress_annotations_json
  local container_image_prepull_enabled container_image_prepull_timeout_seconds
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
  container_ingress_enabled="$(escape_sed_replacement "$CONTAINER_INGRESS_ENABLED")"
  container_ingress_class="$(escape_sed_replacement "$CONTAINER_INGRESS_CLASS")"
  container_ingress_base_domain="$(escape_sed_replacement "$CONTAINER_INGRESS_BASE_DOMAIN")"
  container_ingress_annotations_json="$(escape_sed_replacement "$CONTAINER_INGRESS_ANNOTATIONS_JSON")"
  container_image_prepull_enabled="$(escape_sed_replacement "$CONTAINER_IMAGE_PREPULL_ENABLED")"
  container_image_prepull_timeout_seconds="$(escape_sed_replacement "$CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS")"
  backend_data_hostpath="$(escape_sed_replacement "$BACKEND_DATA_HOSTPATH")"
  golden_images_hostpath="$(escape_sed_replacement "$GOLDEN_IMAGES_HOSTPATH")"
  postgres_data_hostpath="$(escape_sed_replacement "$POSTGRES_DATA_HOSTPATH")"
  postgres_user="$(escape_sed_replacement "$POSTGRES_USER")"
  postgres_password="$(escape_sed_replacement "$POSTGRES_PASSWORD")"
  postgres_db="$(escape_sed_replacement "$POSTGRES_DB")"
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
    -e "s/__CONTAINER_INGRESS_ENABLED__/${container_ingress_enabled}/g" \
    -e "s/__CONTAINER_INGRESS_CLASS__/${container_ingress_class}/g" \
    -e "s/__CONTAINER_INGRESS_BASE_DOMAIN__/${container_ingress_base_domain}/g" \
    -e "s#__CONTAINER_INGRESS_ANNOTATIONS_JSON__#${container_ingress_annotations_json}#g" \
    -e "s/__CONTAINER_IMAGE_PREPULL_ENABLED__/${container_image_prepull_enabled}/g" \
    -e "s/__CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS__/${container_image_prepull_timeout_seconds}/g" \
    -e "s#__BACKEND_DATA_HOSTPATH__#${backend_data_hostpath}#g" \
    -e "s#__GOLDEN_IMAGES_HOSTPATH__#${golden_images_hostpath}#g" \
    -e "s#__POSTGRES_DATA_HOSTPATH__#${postgres_data_hostpath}#g" \
    -e "s/__POSTGRES_USER__/${postgres_user}/g" \
    -e "s/__POSTGRES_PASSWORD__/${postgres_password}/g" \
    -e "s/__POSTGRES_DB__/${postgres_db}/g" \
    -e "s#__CDI_UPLOAD_PROXY_URL__#${cdi_upload_proxy_url}#g" \
    "$input" >"$output"
}

prepare_rendered_manifests() {
  RENDERED_APP_MANIFEST="$(mktemp /tmp/bretter-app.XXXXXX.yaml)"
  RENDERED_GOLDEN_HOSTPATH_MANIFEST="$(mktemp /tmp/bretter-golden-hostpath.XXXXXX.yaml)"
  RENDERED_GOLDEN_PVC_MANIFEST="$(mktemp /tmp/bretter-golden-pvc.XXXXXX.yaml)"

  render_manifest_template "$ROOT_DIR/deploy/app.yaml" "$RENDERED_APP_MANIFEST"
  render_manifest_template "$ROOT_DIR/deploy/golden-hostpath.yaml" "$RENDERED_GOLDEN_HOSTPATH_MANIFEST"
  render_manifest_template "$ROOT_DIR/deploy/golden-pvc.yaml" "$RENDERED_GOLDEN_PVC_MANIFEST"
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
  local vite_api_base="${VITE_API_BASE:-${PUBLIC_SCHEME}://${NODE_EXTERNAL_HOST}:30080}"

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

ensure_pull_secret() {
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

apply_manifests() {
  log "Ensuring namespace $NAMESPACE"
  kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || kubectl create ns "$NAMESPACE"

  ensure_tls_secret
  ensure_golden_images_claim
  reconcile_backend_data_pv
  reconcile_postgres_data_pv
  ensure_pull_secret
  apply_cleanup_automation

  if [ -f "$ROOT_DIR/runner/spice-embed.html" ]; then
    log "Updating spice-embed ConfigMap"
    kubectl -n "$NAMESPACE" create configmap spice-embed \
      --from-file=spice-embed.html="$ROOT_DIR/runner/spice-embed.html" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi

  log "Applying base manifests"
  kubectl apply -f "$RENDERED_APP_MANIFEST"

  log "Waiting for rollout"
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-postgres --timeout=300s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=300s
}

main() {
  validate_public_scheme
  validate_tls_config
  validate_preload_config
  validate_longhorn_tuning_config
  validate_autocleanup_config
  validate_storage_guard_config
  validate_vm_network_config
  validate_container_runtime_config
  validate_postgres_config
  validate_cdi_upload_config
  validate_cpu_manager_config
  validate_monitoring_config
  validate_metrics_server_config
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
  prepare_rendered_manifests

  log "Using control node: $CONTROL_NODE"
  log "Using node external host for API/UI: $NODE_EXTERNAL_HOST"
  if [ -n "$RUNNER_NODE_SELECTOR_VALUE" ]; then
    log "Pinning runner pods to node: $RUNNER_NODE_SELECTOR_VALUE"
  else
    log "Runner pods are not node-pinned (scheduler can choose any eligible node)."
  fi
  log "Using public scheme: $PUBLIC_SCHEME"
  log "Using TLS secret: $TLS_SECRET_NAME (enabled=$TLS_ENABLED)"
  log "Using backend data hostPath: $BACKEND_DATA_HOSTPATH"
  log "Using postgres data hostPath: $POSTGRES_DATA_HOSTPATH"
  log "Using golden images hostPath: $GOLDEN_IMAGES_HOSTPATH"
  log "Using VM storage class: $VM_STORAGE_CLASS"
  log "Using VM network backend: $VM_NET_BACKEND"
  log "Container ingress enabled: $CONTAINER_INGRESS_ENABLED (base domain: ${CONTAINER_INGRESS_BASE_DOMAIN:-disabled}, class: ${CONTAINER_INGRESS_CLASS:-default})"
  log "Container image pre-pull enabled: $CONTAINER_IMAGE_PREPULL_ENABLED (timeout: ${CONTAINER_IMAGE_PREPULL_TIMEOUT_SECONDS}s)"
  log "CPU manager static on all nodes: $CPU_MANAGER_STATIC"
  log "CDI install enabled: $INSTALL_CDI (version: $CDI_VERSION)"
  log "Using CDI upload proxy URL: ${CDI_UPLOAD_PROXY_URL:-disabled}"
  log "Monitoring stack enabled: $ENABLE_MONITORING (namespace: $MONITORING_NAMESPACE release: $MONITORING_RELEASE_NAME chart: ${MONITORING_CHART_VERSION:-latest})"
  log "Metrics-server enabled: $ENABLE_METRICS_SERVER"
  log "Longhorn tuning enabled: $LONGHORN_TUNE"
  if [ -n "$LONGHORN_DEFAULT_DATA_PATH" ]; then
    log "Longhorn default data path override: $LONGHORN_DEFAULT_DATA_PATH"
  fi
  log "Cleanup automation enabled: $ENABLE_AUTOCLEANUP (schedule: $AUTOCLEANUP_SCHEDULE)"
  log "Cleanup alert thresholds: nodefs ${AUTOCLEANUP_NODEFS_WARN_PCT}/${AUTOCLEANUP_NODEFS_CRITICAL_PCT}/${AUTOCLEANUP_NODEFS_EMERGENCY_PCT}% pvc ${AUTOCLEANUP_PVC_WARN_PCT}/${AUTOCLEANUP_PVC_CRITICAL_PCT}/${AUTOCLEANUP_PVC_EMERGENCY_PCT}%"
  log "Storage guard thresholds: warn<${SETUP_WARN_FREE_GIB}Gi, fail<${SETUP_MIN_FREE_GIB}Gi"
  run_storage_preflight_checks

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
  install_metrics_server
  install_monitoring_stack
  apply_monitoring_alert_rules
  log "Done."
}

main "$@"
