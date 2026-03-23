#!/usr/bin/env bash
# shellcheck disable=SC2317,SC2329  # cleanup() is invoked indirectly via EXIT trap.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

NAMESPACE="${NAMESPACE:-labs}"
WAIT_SECONDS="${LABINSTANCE_SMOKE_WAIT_SECONDS:-120}"
POLL_SECONDS="${LABINSTANCE_SMOKE_POLL_SECONDS:-2}"
METRICS_PORT="${LABINSTANCE_CONTROLLER_METRICS_PORT:-19408}"
ORCHESTRATION_BACKEND="${BLABS_ORCHESTRATION_BACKEND:-crd}"
LABINSTANCE_DESIRED_STATE_MODE="${LABINSTANCE_DESIRED_STATE_MODE:-auto}"
INSTANCE_NAME="smoke-labinstance-$(date +%s)"
CONTROLLER_LOG_PATH="${LABINSTANCE_CONTROLLER_LOG_PATH:-/tmp/blabs-labinstance-controller-smoke.log}"
CONTROLLER_PID=""
DESIRED_STATE_MODE=""
DESIRED_STATE_CREATE_YAML=""
DESIRED_STATE_PATCH_JSON=""

if ! [[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]] || [ "$WAIT_SECONDS" -lt 10 ]; then
  echo "ERROR: LABINSTANCE_SMOKE_WAIT_SECONDS must be an integer >= 10 (found ${WAIT_SECONDS})." >&2
  exit 1
fi

cleanup() {
  kubectl -n "$NAMESPACE" delete labinstance "$INSTANCE_NAME" --ignore-not-found=true >/dev/null 2>&1 || true
  if [ -n "$CONTROLLER_PID" ] && kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
    kill "$CONTROLLER_PID" >/dev/null 2>&1 || true
    wait "$CONTROLLER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

resolve_desired_state_mode() {
  local requested mode_value
  requested="$(printf '%s' "$LABINSTANCE_DESIRED_STATE_MODE" | tr '[:upper:]' '[:lower:]')"
  case "$requested" in
    lifecycle | nested)
      DESIRED_STATE_MODE="lifecycle"
      return
      ;;
    legacy | flat)
      DESIRED_STATE_MODE="legacy"
      return
      ;;
    auto | "") ;;
    *)
      echo "ERROR: LABINSTANCE_DESIRED_STATE_MODE must be one of: auto, lifecycle, legacy." >&2
      exit 1
      ;;
  esac

  mode_value="$(
    kubectl get crd labinstances.labs.bretter.io \
      -o jsonpath='{.spec.versions[?(@.name=="v1alpha1")].schema.openAPIV3Schema.properties.spec.properties.lifecycle.type}' \
      2>/dev/null || true
  )"
  if [ "$mode_value" = "object" ]; then
    DESIRED_STATE_MODE="lifecycle"
  else
    DESIRED_STATE_MODE="legacy"
  fi
}

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE" >/dev/null
kubectl apply -k "$ROOT_DIR/deploy/crds" >/dev/null
resolve_desired_state_mode
if [ "$DESIRED_STATE_MODE" = "lifecycle" ]; then
  DESIRED_STATE_CREATE_YAML=$'  lifecycle:\n    desiredState: running'
  DESIRED_STATE_PATCH_JSON='{"spec":{"lifecycle":{"desiredState":"stopped"}}}'
else
  DESIRED_STATE_CREATE_YAML='  desiredState: running'
  DESIRED_STATE_PATCH_JSON='{"spec":{"desiredState":"stopped"}}'
fi

(
  export BLABS_KUBE_NAMESPACE="$NAMESPACE"
  export BLABS_LABINSTANCE_CONTROLLER_ENABLED=1
  export BLABS_LABINSTANCE_CONTROLLER_DRY_RUN=1
  export BLABS_LABINSTANCE_CONTROLLER_POLL_SECONDS=2
  export BLABS_LABINSTANCE_CONTROLLER_STUCK_SECONDS=300
  export BLABS_LABINSTANCE_CONTROLLER_METRICS_BIND=127.0.0.1
  export BLABS_LABINSTANCE_CONTROLLER_METRICS_PORT="$METRICS_PORT"
  export BLABS_ORCHESTRATION_BACKEND="$ORCHESTRATION_BACKEND"
  export BLABS_DATABASE_PATH="${BLABS_DATABASE_PATH:-/tmp/blabs-controller-smoke.db}"
  export BLABS_REQUIRE_SCHEMA_READY=0
  cd "$ROOT_DIR"
  "$PYTHON_BIN" -m backend.src.tools.labinstance_controller >"$CONTROLLER_LOG_PATH" 2>&1
) &
CONTROLLER_PID="$!"

deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
    echo "ERROR: LabInstance controller exited before metrics endpoint became ready." >&2
    cat "$CONTROLLER_LOG_PATH" >&2 || true
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
if ! curl -fsS "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null 2>&1; then
  echo "ERROR: LabInstance controller metrics endpoint did not become ready in time." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
if ! curl -fsS "http://127.0.0.1:${METRICS_PORT}/livez" >/dev/null 2>&1; then
  echo "ERROR: LabInstance controller liveness endpoint failed." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
if ! curl -fsS "http://127.0.0.1:${METRICS_PORT}/readyz" >/dev/null 2>&1; then
  echo "ERROR: LabInstance controller readiness endpoint failed." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi

cat <<EOF | kubectl -n "$NAMESPACE" apply -f - >/dev/null
apiVersion: labs.bretter.io/v1alpha1
kind: LabInstance
metadata:
  name: ${INSTANCE_NAME}
spec:
  owner:
    username: smoke-user
  templateRef:
    name: smoke-template
  workload:
    kind: vm
    consoleProvider: spice
  resources:
    cpuMillicores: 1000
    memoryMiB: 2048
  network:
    mode: bridge
  idleTimeoutMinutes: 30
${DESIRED_STATE_CREATE_YAML}
EOF

phase=""
deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  phase="$(kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [ "${phase,,}" = "running" ]; then
    break
  fi
  sleep "$POLL_SECONDS"
done
if [ "${phase,,}" != "running" ]; then
  echo "ERROR: LabInstance did not reach Running in dry-run smoke (phase=${phase:-<empty>})." >&2
  kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" -o yaml || true
  exit 1
fi

kubectl -n "$NAMESPACE" patch labinstance "$INSTANCE_NAME" --type=merge -p "$DESIRED_STATE_PATCH_JSON" >/dev/null
phase=""
deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  phase="$(kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [ "${phase,,}" = "stopped" ]; then
    break
  fi
  sleep "$POLL_SECONDS"
done
if [ "${phase,,}" != "stopped" ]; then
  echo "ERROR: LabInstance did not reach Stopped after desiredState patch." >&2
  kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" -o yaml || true
  exit 1
fi

kubectl -n "$NAMESPACE" delete labinstance "$INSTANCE_NAME" --wait=false >/dev/null
deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" >/dev/null 2>&1; then
    echo "PASS: LabInstance controller dry-run smoke passed."
    exit 0
  fi
  sleep "$POLL_SECONDS"
done

echo "ERROR: LabInstance was not deleted in dry-run smoke." >&2
kubectl -n "$NAMESPACE" get labinstance "$INSTANCE_NAME" -o yaml || true
exit 1
