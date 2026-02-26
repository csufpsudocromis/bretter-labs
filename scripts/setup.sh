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
PUBLIC_SCHEME="${PUBLIC_SCHEME:-https}"
TLS_ENABLED="${TLS_ENABLED:-1}"
TLS_SECRET_NAME="${TLS_SECRET_NAME:-bretter-tls}"
TLS_CERT_FILE="${TLS_CERT_FILE:-}"
TLS_KEY_FILE="${TLS_KEY_FILE:-}"
BACKEND_DATA_HOSTPATH="${BACKEND_DATA_HOSTPATH:-/var/lib/bretter-labs/backend-data}"
GOLDEN_IMAGES_HOSTPATH="${GOLDEN_IMAGES_HOSTPATH:-/var/lib/bretter-labs/golden-images}"

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
  sync_longhorn_reserved_capacity
  ensure_longhorn_vm_storage_class

  if [ -z "$VM_STORAGE_CLASS" ]; then
    VM_STORAGE_CLASS="$LONGHORN_VM_STORAGE_CLASS"
    log "VM_STORAGE_CLASS not set; defaulting to $VM_STORAGE_CLASS for clone-based VM disks."
  fi
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

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

render_manifest_template() {
  local input="$1"
  local output="$2"

  local ns control_node node_external_host backend_image frontend_image runner_image public_scheme tls_secret_name
  local runner_node_selector_value
  local vm_storage_class backend_data_hostpath golden_images_hostpath
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
  backend_data_hostpath="$(escape_sed_replacement "$BACKEND_DATA_HOSTPATH")"
  golden_images_hostpath="$(escape_sed_replacement "$GOLDEN_IMAGES_HOSTPATH")"

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
    -e "s#__BACKEND_DATA_HOSTPATH__#${backend_data_hostpath}#g" \
    -e "s#__GOLDEN_IMAGES_HOSTPATH__#${golden_images_hostpath}#g" \
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

apply_manifests() {
  log "Ensuring namespace $NAMESPACE"
  kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || kubectl create ns "$NAMESPACE"

  ensure_tls_secret
  ensure_golden_images_claim
  reconcile_backend_data_pv
  ensure_pull_secret

  if [ -f "$ROOT_DIR/runner/spice-embed.html" ]; then
    log "Updating spice-embed ConfigMap"
    kubectl -n "$NAMESPACE" create configmap spice-embed \
      --from-file=spice-embed.html="$ROOT_DIR/runner/spice-embed.html" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi

  log "Applying base manifests"
  kubectl apply -f "$RENDERED_APP_MANIFEST"

  log "Waiting for rollout"
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=300s
}

main() {
  validate_public_scheme
  validate_tls_config
  validate_preload_config
  validate_longhorn_tuning_config
  require_apt
  install_base_packages
  install_kubectl
  ensure_kubeconfig
  tune_longhorn_for_phase2
  detect_control_node
  detect_node_external_host
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
  log "Using golden images hostPath: $GOLDEN_IMAGES_HOSTPATH"
  log "Using VM storage class: $VM_STORAGE_CLASS"
  log "Longhorn tuning enabled: $LONGHORN_TUNE"

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
  log "Done."
}

main "$@"
