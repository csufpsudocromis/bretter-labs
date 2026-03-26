#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-labs}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
BACKEND_SERVICE="${BACKEND_SERVICE:-bretter-backend}"
LOCAL_PORT="${LOCAL_PORT:-18080}"
WAIT_SECONDS="${WAIT_SECONDS:-30}"
TAIL_LINES="${TAIL_LINES:-250}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 1
  fi
}

contains_cmd() {
  if command -v rg >/dev/null 2>&1; then
    rg -i "$1"
    return
  fi
  grep -Ei "$1"
}

require_cmd kubectl
require_cmd curl

pf_log="$(mktemp /tmp/bretter-connectivity-portforward.XXXXXX.log)"
PF_PID=""
cleanup() {
  if [ -n "$PF_PID" ] && kill -0 "$PF_PID" >/dev/null 2>&1; then
    kill "$PF_PID" >/dev/null 2>&1 || true
    wait "$PF_PID" >/dev/null 2>&1 || true
  fi
  rm -f "$pf_log"
}
trap cleanup EXIT

echo "== bretter connectivity diagnostics =="
echo "namespace=$NAMESPACE monitoring_namespace=$MONITORING_NAMESPACE service=$BACKEND_SERVICE"

echo
echo "-- deployments --"
kubectl -n "$NAMESPACE" get deploy bretter-backend bretter-frontend

echo
echo "-- runtime pods --"
kubectl -n "$NAMESPACE" get pods -o wide | contains_cmd '^vm-|^virt-launcher-|^ct-|^NAME'

echo
echo "-- monitoring wiring --"
if kubectl -n "$MONITORING_NAMESPACE" get servicemonitor bretter-backend >/dev/null 2>&1; then
  kubectl -n "$MONITORING_NAMESPACE" get servicemonitor bretter-backend -o wide
else
  echo "WARN: ServiceMonitor/bretter-backend not found in namespace $MONITORING_NAMESPACE"
fi

if kubectl -n "$MONITORING_NAMESPACE" get prometheusrule bretter-labs-alerts >/dev/null 2>&1; then
  kubectl -n "$MONITORING_NAMESPACE" get prometheusrule bretter-labs-alerts -o yaml | contains_cmd 'BretterWebsocket'
else
  echo "WARN: PrometheusRule/bretter-labs-alerts not found in namespace $MONITORING_NAMESPACE"
fi

echo
echo "-- backend health + websocket metrics snapshot --"
kubectl -n "$NAMESPACE" port-forward "svc/${BACKEND_SERVICE}" "${LOCAL_PORT}:8000" >"$pf_log" 2>&1 &
PF_PID="$!"
deadline=$(($(date +%s) + WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS "http://127.0.0.1:${LOCAL_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:${LOCAL_PORT}/health" >/dev/null 2>&1; then
  echo "ERROR: backend health probe over port-forward failed." >&2
  cat "$pf_log" >&2
  exit 1
fi
curl -fsS "http://127.0.0.1:${LOCAL_PORT}/health"
echo
metrics_payload="$(curl -fsS "http://127.0.0.1:${LOCAL_PORT}/metrics" 2>/dev/null || true)"
if [ -z "$metrics_payload" ]; then
  echo "WARN: backend /metrics returned empty payload."
else
  printf '%s\n' "$metrics_payload" | contains_cmd '^blabs_ws_proxy_'
fi

echo
echo "-- backend websocket/connect log sample --"
kubectl -n "$NAMESPACE" logs deploy/bretter-backend --tail="$TAIL_LINES" | contains_cmd 'websocket|connect.*failed|guacamole|proxy failed' || true

echo
echo "PASS: diagnostics snapshot completed."
