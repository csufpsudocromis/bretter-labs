#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

NAMESPACE="${NAMESPACE:-labs}"
BASE_VALUES_FILE="${BASE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-production.yaml}"
SITE_VALUES_FILE="${SITE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-prod-site.yaml}"
REQUIRE_SITE_VALUES_FILE="${REQUIRE_SITE_VALUES_FILE:-1}"
RUNTIME_SECRET_NAME="${RUNTIME_SECRET_NAME:-bretter-runtime-secrets}"
RUNTIME_SECRET_KEY="${RUNTIME_SECRET_KEY:-secrets_encryption_key}"
SIGNATURE_SECRET_NAME="${SIGNATURE_SECRET_NAME:-bretter-cosign-public-key}"
SIGNATURE_SECRET_KEY="${SIGNATURE_SECRET_KEY:-cosign.pub}"
SKIP_CLUSTER_CHECKS="${SKIP_CLUSTER_CHECKS:-0}"
PREDEPLOY_VERIFY_NODE_IMAGE_PULLS="${PREDEPLOY_VERIFY_NODE_IMAGE_PULLS:-1}"
PREDEPLOY_IMAGE_PULL_TIMEOUT_SECONDS="${PREDEPLOY_IMAGE_PULL_TIMEOUT_SECONDS:-120}"
PREDEPLOY_IMAGE_PULL_SECRET_NAME="${PREDEPLOY_IMAGE_PULL_SECRET_NAME:-ghcr-creds}"

fail_count=0
VALUES_FILES=()

log() {
  printf '%s\n' "$*"
}

pass_check() {
  log "PASS: $1"
}

fail_check() {
  log "FAIL: $1"
  fail_count=$((fail_count + 1))
}

run_check() {
  local name="$1"
  shift
  if "$@"; then
    pass_check "$name"
  else
    fail_check "$name"
  fi
}

collect_value_files() {
  VALUES_FILES=("$BASE_VALUES_FILE")
  if [ -f "$SITE_VALUES_FILE" ]; then
    VALUES_FILES+=("$SITE_VALUES_FILE")
    return
  fi
  if [ "$REQUIRE_SITE_VALUES_FILE" = "1" ]; then
    fail_check "site values file exists (${SITE_VALUES_FILE})"
  else
    log "Site values file not found (${SITE_VALUES_FILE}); continuing with baseline values only."
  fi
}

validate_toggle() {
  local name="$1"
  local value="$2"
  case "$value" in
    0|1) ;;
    *)
      fail_check "${name} must be either 0 or 1 (found ${value})."
      ;;
  esac
}

validate_positive_int() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [ "$value" -lt 1 ]; then
    fail_check "${name} must be an integer >= 1 (found ${value})."
  fi
}

fetch_secret_key_data_b64() {
  local namespace="$1"
  local secret_name="$2"
  local secret_key="$3"
  kubectl -n "$namespace" get secret "$secret_name" -o "go-template={{ index .data \"$secret_key\" }}" 2>/dev/null || true
}

read_image_refs_from_values() {
  "$PYTHON_BIN" - "${VALUES_FILES[@]}" <<'PY'
import sys
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"values file must contain a mapping: {path}")
    return raw


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


merged: dict[str, Any] = {}
for item in sys.argv[1:]:
    path = Path(item)
    merged = deep_merge(merged, read_yaml(path))

app = merged.get("appTemplateValues") or {}
for key in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "RUNNER_IMAGE"):
    value = str(app.get(key) or "").strip()
    if value:
        print(f"{key}={value}")
PY
}

list_schedulable_nodes() {
  "$PYTHON_BIN" - <<'PY'
import json
import subprocess
import sys

raw = subprocess.check_output(["kubectl", "get", "nodes", "-o", "json"], text=True)
payload = json.loads(raw)
for item in payload.get("items", []):
    if (item.get("spec") or {}).get("unschedulable") is True:
        continue
    ready = False
    for condition in (item.get("status") or {}).get("conditions", []):
        if condition.get("type") == "Ready" and condition.get("status") == "True":
            ready = True
            break
    if not ready:
        continue
    name = str((item.get("metadata") or {}).get("name") or "").strip()
    if name:
        print(name)
PY
}

run_node_image_pull_smoke_checks() {
  local image_line image_name image_ref
  local node pod_name safe_node image_pull_secret_yaml
  local image_idx
  local -a image_lines=() nodes=()

  case "$PREDEPLOY_VERIFY_NODE_IMAGE_PULLS" in
    0)
      log "Node image pull checks skipped (PREDEPLOY_VERIFY_NODE_IMAGE_PULLS=0)."
      return 0
      ;;
    1) ;;
    *)
      fail_check "PREDEPLOY_VERIFY_NODE_IMAGE_PULLS must be either 0 or 1 (found ${PREDEPLOY_VERIFY_NODE_IMAGE_PULLS})."
      return 1
      ;;
  esac

  if ! mapfile -t image_lines < <(read_image_refs_from_values); then
    fail_check "read image refs from values files"
    return 1
  fi
  if [ "${#image_lines[@]}" -eq 0 ]; then
    fail_check "resolve image refs for node pull checks"
    return 1
  fi
  if ! mapfile -t nodes < <(list_schedulable_nodes); then
    fail_check "list schedulable nodes for image pull checks"
    return 1
  fi
  if [ "${#nodes[@]}" -eq 0 ]; then
    fail_check "find at least one schedulable Ready node for image pull checks"
    return 1
  fi

  for node in "${nodes[@]}"; do
    for image_idx in "${!image_lines[@]}"; do
      image_line="${image_lines[$image_idx]}"
      image_name="${image_line%%=*}"
      image_ref="${image_line#*=}"
      safe_node="$(printf '%s' "$node" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')"
      safe_node="${safe_node:0:20}"
      pod_name="preflight-pull-${safe_node}-${image_idx}"
      image_pull_secret_yaml=""
      if [ -n "$PREDEPLOY_IMAGE_PULL_SECRET_NAME" ]; then
        image_pull_secret_yaml="  imagePullSecrets:
    - name: ${PREDEPLOY_IMAGE_PULL_SECRET_NAME}"
      fi

      kubectl -n "$NAMESPACE" delete pod "$pod_name" --ignore-not-found=true >/dev/null 2>&1 || true
      kubectl -n "$NAMESPACE" apply -f - <<EOF >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  nodeName: ${node}
${image_pull_secret_yaml}
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
  containers:
    - name: pull-smoke
      image: ${image_ref}
      imagePullPolicy: Always
      command:
        - /bin/sh
        - -lc
        - |
          set -eu
          echo "pull-smoke ${image_name}"
          sleep 20
EOF

      if kubectl -n "$NAMESPACE" wait --for=condition=Ready "pod/${pod_name}" --timeout="${PREDEPLOY_IMAGE_PULL_TIMEOUT_SECONDS}s" >/dev/null 2>&1; then
        pass_check "node image pull check (${node} ${image_name})"
      else
        fail_check "node image pull check (${node} ${image_name})"
        kubectl -n "$NAMESPACE" describe pod "$pod_name" | sed -n '1,140p' || true
      fi
      kubectl -n "$NAMESPACE" delete pod "$pod_name" --ignore-not-found=true >/dev/null 2>&1 || true
    done
  done
}

log "Deploy preflight started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: ${NAMESPACE}"
validate_toggle "REQUIRE_SITE_VALUES_FILE" "$REQUIRE_SITE_VALUES_FILE"
validate_positive_int "PREDEPLOY_IMAGE_PULL_TIMEOUT_SECONDS" "$PREDEPLOY_IMAGE_PULL_TIMEOUT_SECONDS"
collect_value_files
if [ "${#VALUES_FILES[@]}" -gt 0 ]; then
  log "Values files:"
  printf '  - %s\n' "${VALUES_FILES[@]}"
fi

validate_cmd=("$PYTHON_BIN" "$ROOT_DIR/scripts/validate_production_profile.py" --strict)
for value_file in "${VALUES_FILES[@]}"; do
  validate_cmd+=(-f "$value_file")
done
run_check "strict production values validation (merged values files)" "${validate_cmd[@]}"

if [ "$SKIP_CLUSTER_CHECKS" = "1" ]; then
  log "Cluster checks skipped (SKIP_CLUSTER_CHECKS=1)."
  if [ "$fail_count" -ne 0 ]; then
    exit 1
  fi
  exit 0
fi

run_check "kubectl access" bash -lc "kubectl version --request-timeout=10s >/dev/null"
run_check "namespace exists" bash -lc "kubectl get namespace '$NAMESPACE' >/dev/null"

runtime_secret_data="$(fetch_secret_key_data_b64 "$NAMESPACE" "$RUNTIME_SECRET_NAME" "$RUNTIME_SECRET_KEY")"
if [ -n "$runtime_secret_data" ]; then
  pass_check "runtime secret/key present (${RUNTIME_SECRET_NAME}/${RUNTIME_SECRET_KEY})"
else
  fail_check "runtime secret/key present (${RUNTIME_SECRET_NAME}/${RUNTIME_SECRET_KEY})"
fi

signature_secret_data="$(fetch_secret_key_data_b64 "$NAMESPACE" "$SIGNATURE_SECRET_NAME" "$SIGNATURE_SECRET_KEY")"
if [ -n "$signature_secret_data" ]; then
  pass_check "signature key secret present (${SIGNATURE_SECRET_NAME}/${SIGNATURE_SECRET_KEY})"
else
  fail_check "signature key secret present (${SIGNATURE_SECRET_NAME}/${SIGNATURE_SECRET_KEY})"
fi

run_node_image_pull_smoke_checks || true

if [ "$fail_count" -ne 0 ]; then
  log "Deploy preflight failed with ${fail_count} check(s)."
  exit 1
fi

log "Deploy preflight passed."
