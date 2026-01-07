#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
BACKEND_IMAGE="${BACKEND_IMAGE:-ghcr.io/csufpsudocromis/bretter-backend:latest}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-ghcr.io/csufpsudocromis/bretter-frontend:latest}"
KUBECONFIG_PATH="${KUBECONFIG:-}"
APPLY_GOLDEN_PVC="${APPLY_GOLDEN_PVC:-0}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"
CREATE_PULL_SECRET="${CREATE_PULL_SECRET:-0}"

log() {
  echo "==> $*"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
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
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo_cmd -E bash -
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
  fi
  if ! kubectl version --client >/dev/null 2>&1; then
    fail "kubectl is not working. Check your PATH or installation."
  fi
  if ! kubectl get ns >/dev/null 2>&1; then
    fail "kubectl cannot reach a cluster. Ensure KUBECONFIG is set correctly."
  fi
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

build_and_push_images() {
  log "Building backend image: $BACKEND_IMAGE"
  podman build -t "$BACKEND_IMAGE" -f "$ROOT_DIR/backend/Dockerfile" "$ROOT_DIR"
  log "Pushing backend image..."
  podman push "$BACKEND_IMAGE"

  log "Building frontend image: $FRONTEND_IMAGE"
  podman build -t "$FRONTEND_IMAGE" -f "$ROOT_DIR/frontend-vite/Dockerfile" "$ROOT_DIR"
  log "Pushing frontend image..."
  podman push "$FRONTEND_IMAGE"
}

apply_manifests() {
  log "Ensuring namespace $NAMESPACE"
  kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || kubectl create ns "$NAMESPACE"

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
  else
    log "Skipping image pull secret (set CREATE_PULL_SECRET=1 if images are private)"
  fi

  if [ -f "$ROOT_DIR/runner/spice-embed.html" ]; then
    log "Updating spice-embed ConfigMap"
    kubectl -n "$NAMESPACE" create configmap spice-embed \
      --from-file=spice-embed.html="$ROOT_DIR/runner/spice-embed.html" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi

  if [ "$APPLY_GOLDEN_PVC" -eq 1 ] && [ -f "$ROOT_DIR/deploy/golden-pvc.yaml" ]; then
    log "Applying golden-images PVC (ensure storageClassName is set correctly)"
    kubectl apply -f "$ROOT_DIR/deploy/golden-pvc.yaml"
  fi

  log "Applying base manifests"
  kubectl apply -f "$ROOT_DIR/deploy/app.yaml"

  log "Setting images on deployments"
  kubectl -n "$NAMESPACE" set image deployment/bretter-backend backend="$BACKEND_IMAGE"
  kubectl -n "$NAMESPACE" set image deployment/bretter-frontend frontend="$FRONTEND_IMAGE"

  log "Waiting for rollout"
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=180s
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=180s
}

main() {
  require_apt
  install_base_packages
  install_kubectl
  ensure_kubeconfig

  if [ "$PUSH_IMAGES" -eq 1 ]; then
    install_node
    install_podman
    ensure_ghcr_login
    build_and_push_images
    CREATE_PULL_SECRET=1
  fi

  if [ "$CREATE_PULL_SECRET" -eq 1 ] && ! command -v podman >/dev/null 2>&1; then
    install_podman
  fi

  apply_manifests
  log "Done."
}

main "$@"
