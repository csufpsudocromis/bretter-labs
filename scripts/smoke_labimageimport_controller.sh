#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

NAMESPACE="${NAMESPACE:-labs}"
WAIT_SECONDS="${LABIMAGEIMPORT_SMOKE_WAIT_SECONDS:-120}"
POLL_SECONDS="${LABIMAGEIMPORT_SMOKE_POLL_SECONDS:-2}"
METRICS_PORT="${LABIMAGEIMPORT_CONTROLLER_METRICS_PORT:-19410}"
CONTROLLER_LOG_PATH="${LABIMAGEIMPORT_CONTROLLER_LOG_PATH:-/tmp/blabs-labimageimport-controller-smoke.log}"
CONTROLLER_PID=""

if ! [[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]] || [ "$WAIT_SECONDS" -lt 10 ]; then
  echo "ERROR: LABIMAGEIMPORT_SMOKE_WAIT_SECONDS must be an integer >= 10 (found ${WAIT_SECONDS})." >&2
  exit 1
fi

# shellcheck disable=SC2317,SC2329  # Invoked indirectly via EXIT trap.
cleanup() {
  if [ -n "$CONTROLLER_PID" ] && kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
    kill "$CONTROLLER_PID" >/dev/null 2>&1 || true
    wait "$CONTROLLER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE" >/dev/null
kubectl apply -k "$ROOT_DIR/deploy/crds" >/dev/null

(
  export BLABS_KUBE_NAMESPACE="$NAMESPACE"
  export BLABS_IMAGE_IMPORT_BACKEND=crd
  export BLABS_LABIMAGEIMPORT_CONTROLLER_ENABLED=1
  export BLABS_LABIMAGEIMPORT_CONTROLLER_LEADER_ELECTION_ENABLED=1
  export BLABS_LABIMAGEIMPORT_CONTROLLER_POLL_SECONDS=2
  export BLABS_LABIMAGEIMPORT_CONTROLLER_RETRY_PERIOD_SECONDS=2
  export BLABS_LABIMAGEIMPORT_CONTROLLER_METRICS_BIND=127.0.0.1
  export BLABS_LABIMAGEIMPORT_CONTROLLER_METRICS_PORT="$METRICS_PORT"
  export BLABS_DATABASE_PATH="${BLABS_DATABASE_PATH:-/tmp/blabs-labimageimport-controller-smoke.db}"
  export BLABS_REQUIRE_SCHEMA_READY=0
  cd "$ROOT_DIR"
  "$PYTHON_BIN" -m backend.src.tools.labimageimport_controller >"$CONTROLLER_LOG_PATH" 2>&1
) &
CONTROLLER_PID="$!"

deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "http://127.0.0.1:${METRICS_PORT}/metrics" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
    echo "ERROR: LabImageImport controller exited before metrics endpoint became ready." >&2
    cat "$CONTROLLER_LOG_PATH" >&2 || true
    exit 1
  fi
  sleep "$POLL_SECONDS"
done

metrics_payload="$(curl -fsS "http://127.0.0.1:${METRICS_PORT}/metrics" 2>/dev/null || true)"
if [ -z "$metrics_payload" ]; then
  echo "ERROR: LabImageImport controller metrics endpoint did not become ready in time." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
if ! curl -fsS "http://127.0.0.1:${METRICS_PORT}/livez" >/dev/null 2>&1; then
  echo "ERROR: LabImageImport controller liveness endpoint failed." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "http://127.0.0.1:${METRICS_PORT}/readyz" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
    echo "ERROR: LabImageImport controller exited before readiness endpoint became healthy." >&2
    cat "$CONTROLLER_LOG_PATH" >&2 || true
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
if ! curl -fsS "http://127.0.0.1:${METRICS_PORT}/readyz" >/dev/null 2>&1; then
  echo "ERROR: LabImageImport controller readiness endpoint failed." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
if ! grep -q "blabs_labimageimport_controller_ready" <<<"$metrics_payload"; then
  echo "ERROR: LabImageImport metrics payload missing controller readiness metric." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi
if ! grep -q "blabs_labimageimport_watchdog_scanned_total" <<<"$metrics_payload"; then
  echo "ERROR: LabImageImport metrics payload missing watchdog metric." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi

sleep "$POLL_SECONDS"
if ! kill -0 "$CONTROLLER_PID" >/dev/null 2>&1; then
  echo "ERROR: LabImageImport controller exited unexpectedly after readiness." >&2
  cat "$CONTROLLER_LOG_PATH" >&2 || true
  exit 1
fi

echo "PASS: LabImageImport controller smoke passed."
