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
CREATE_PULL_SECRET="${CREATE_PULL_SECRET:-0}"
CONTROL_NODE="${CONTROL_NODE:-}"
NODE_EXTERNAL_HOST="${NODE_EXTERNAL_HOST:-}"
PUBLIC_SCHEME="${PUBLIC_SCHEME:-https}"
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
  sudo_cmd apt-get install -y ca-certificates curl gnupg lsb-release git python3 python3-venv python3-pip
}

install_node() {
  local need_node=1
  if command -v node >/dev/null 2>&1; then
    local major
    major="$(node -v | sed -E 's/^v([0-9]+).*/\\1/')"
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

  local ns control_node node_external_host backend_image frontend_image runner_image public_scheme
  local backend_data_hostpath golden_images_hostpath
  ns="$(escape_sed_replacement "$NAMESPACE")"
  control_node="$(escape_sed_replacement "$CONTROL_NODE")"
  node_external_host="$(escape_sed_replacement "$NODE_EXTERNAL_HOST")"
  backend_image="$(escape_sed_replacement "$BACKEND_IMAGE")"
  frontend_image="$(escape_sed_replacement "$FRONTEND_IMAGE")"
  runner_image="$(escape_sed_replacement "$RUNNER_IMAGE")"
  public_scheme="$(escape_sed_replacement "$PUBLIC_SCHEME")"
  backend_data_hostpath="$(escape_sed_replacement "$BACKEND_DATA_HOSTPATH")"
  golden_images_hostpath="$(escape_sed_replacement "$GOLDEN_IMAGES_HOSTPATH")"

  sed \
    -e "s/__NAMESPACE__/${ns}/g" \
    -e "s/__CONTROL_NODE__/${control_node}/g" \
    -e "s/__NODE_EXTERNAL_HOST__/${node_external_host}/g" \
    -e "s/__BACKEND_IMAGE__/${backend_image}/g" \
    -e "s/__FRONTEND_IMAGE__/${frontend_image}/g" \
    -e "s/__RUNNER_IMAGE__/${runner_image}/g" \
    -e "s/__PUBLIC_SCHEME__/${public_scheme}/g" \
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
}

push_images() {
  log "Pushing backend image..."
  podman push "$BACKEND_IMAGE"

  log "Pushing frontend image..."
  podman push "$FRONTEND_IMAGE"
}

load_images_into_containerd() {
  if ! command -v ctr >/dev/null 2>&1; then
    fail "ctr is required to load local images into containerd."
  fi

  local backend_tar frontend_tar
  backend_tar="$(mktemp /tmp/bretter-backend-image.XXXXXX.tar)"
  frontend_tar="$(mktemp /tmp/bretter-frontend-image.XXXXXX.tar)"

  log "Saving local backend image tar..."
  podman save -o "$backend_tar" "$BACKEND_IMAGE"
  log "Saving local frontend image tar..."
  podman save -o "$frontend_tar" "$FRONTEND_IMAGE"

  log "Importing backend image into containerd..."
  sudo_cmd ctr -n k8s.io images import "$backend_tar"
  log "Importing frontend image into containerd..."
  sudo_cmd ctr -n k8s.io images import "$frontend_tar"

  rm -f "$backend_tar" "$frontend_tar"
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
  require_apt
  install_base_packages
  install_kubectl
  ensure_kubeconfig
  detect_control_node
  detect_node_external_host
  prepare_rendered_manifests

  log "Using control node: $CONTROL_NODE"
  log "Using node external host for API/UI: $NODE_EXTERNAL_HOST"
  log "Using public scheme: $PUBLIC_SCHEME"
  log "Using backend data hostPath: $BACKEND_DATA_HOSTPATH"
  log "Using golden images hostPath: $GOLDEN_IMAGES_HOSTPATH"

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
