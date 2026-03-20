#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-labs}"
CRD_NAME="${CRD_NAME:-canary-$(date +%s)}"
CRD_CANARY_TEMPLATE_ID="${CRD_CANARY_TEMPLATE_ID:-}"
CRD_CANARY_OWNER="${CRD_CANARY_OWNER:-admin}"
CRD_CANARY_WAIT_SECONDS="${CRD_CANARY_WAIT_SECONDS:-300}"
CRD_CANARY_RUNNING_SLO_SECONDS="${CRD_CANARY_RUNNING_SLO_SECONDS:-180}"
CRD_CANARY_DELETE_WAIT_SECONDS="${CRD_CANARY_DELETE_WAIT_SECONDS:-180}"

if [ -z "$CRD_CANARY_TEMPLATE_ID" ]; then
  echo "ERROR: CRD_CANARY_TEMPLATE_ID is required for LabInstance canary." >&2
  exit 1
fi

require_int() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || [ "$value" -lt 1 ]; then
    echo "ERROR: ${name} must be an integer >= 1 (found ${value})." >&2
    exit 1
  fi
}

require_int "CRD_CANARY_WAIT_SECONDS" "$CRD_CANARY_WAIT_SECONDS"
require_int "CRD_CANARY_RUNNING_SLO_SECONDS" "$CRD_CANARY_RUNNING_SLO_SECONDS"
require_int "CRD_CANARY_DELETE_WAIT_SECONDS" "$CRD_CANARY_DELETE_WAIT_SECONDS"

kubectl get crd labinstances.labs.bretter.io >/dev/null

start_epoch="$(date +%s)"
cat <<EOF | kubectl -n "$NAMESPACE" apply -f -
apiVersion: labs.bretter.io/v1alpha1
kind: LabInstance
metadata:
  name: ${CRD_NAME}
spec:
  owner:
    username: ${CRD_CANARY_OWNER}
  templateRef:
    name: ${CRD_CANARY_TEMPLATE_ID}
  workload:
    kind: vm
    consoleProvider: spice
  resources:
    cpuMillicores: 1000
    memoryMiB: 2048
    diskGiB: 40
  network:
    mode: bridge
  idleTimeoutMinutes: 30
  lifecycle:
    desiredState: running
EOF

phase=""
deadline=$((start_epoch + CRD_CANARY_WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  phase="$(kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  case "${phase,,}" in
    running) break ;;
    failed)
      echo "ERROR: canary LabInstance entered Failed phase before Running." >&2
      kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o yaml || true
      exit 1
      ;;
  esac
  sleep 5
done

if [ "${phase,,}" != "running" ]; then
  echo "ERROR: canary LabInstance did not reach Running within ${CRD_CANARY_WAIT_SECONDS}s (phase=${phase:-<empty>})." >&2
  kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o yaml || true
  exit 1
fi

running_seconds=$(($(date +%s) - start_epoch))
if [ "$running_seconds" -gt "$CRD_CANARY_RUNNING_SLO_SECONDS" ]; then
  echo "ERROR: canary Running SLO breached (${running_seconds}s > ${CRD_CANARY_RUNNING_SLO_SECONDS}s)." >&2
  exit 1
fi

kubectl -n "$NAMESPACE" patch labinstance "$CRD_NAME" --type=merge -p '{"spec":{"lifecycle":{"desiredState":"stopped"}}}' >/dev/null
deadline=$(($(date +%s) + CRD_CANARY_WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  phase="$(kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [ "${phase,,}" = "stopped" ]; then
    break
  fi
  sleep 5
done
if [ "${phase,,}" != "stopped" ]; then
  echo "ERROR: canary LabInstance did not reach Stopped phase after desiredState patch." >&2
  kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o yaml || true
  exit 1
fi

kubectl -n "$NAMESPACE" delete labinstance "$CRD_NAME" --wait=false >/dev/null
deadline=$(($(date +%s) + CRD_CANARY_DELETE_WAIT_SECONDS))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" >/dev/null 2>&1; then
    echo "PASS: LabInstance canary passed (running_seconds=${running_seconds})."
    exit 0
  fi
  sleep 3
done

echo "ERROR: canary LabInstance delete did not complete within ${CRD_CANARY_DELETE_WAIT_SECONDS}s." >&2
kubectl -n "$NAMESPACE" get labinstance "$CRD_NAME" -o yaml || true
exit 1
