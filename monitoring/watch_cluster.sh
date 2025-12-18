#!/usr/bin/env bash
# Lightweight watcher to log control-plane health, node metrics, and recent events.

set -uo pipefail

LOG_FILE="$(dirname "$(readlink -f "$0")")/cluster-watch.log"
KUBECONFIG="${KUBECONFIG:-/home/cbeis/.kube/config}"
export KUBECONFIG

log_section() {
  printf "\n[%s] %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$1" >> "$LOG_FILE"
}

run_cmd() {
  local label="$1"
  shift
  log_section "$label"
  "$@" >> "$LOG_FILE" 2>&1 || {
    echo "command failed: $*" >> "$LOG_FILE"
  }
}

log_header() {
  printf "====================\n" >> "$LOG_FILE"
  printf "Cluster watch started at %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$LOG_FILE"
  printf "====================\n" >> "$LOG_FILE"
}

log_header

while true; do
  run_cmd "apiserver readyz" kubectl get --raw='/readyz?verbose' || true
  run_cmd "apiserver healthz" kubectl get --raw='/healthz?verbose' || true
  run_cmd "core pods (kube-system)" kubectl -n kube-system get pods
  run_cmd "labs pods" kubectl -n labs get pods
  run_cmd "top nodes" kubectl top nodes
  run_cmd "recent events" kubectl get events -A --sort-by=.lastTimestamp | tail -n 15
  sleep 60
done
