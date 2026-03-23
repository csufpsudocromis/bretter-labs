#!/usr/bin/env bash
set -euo pipefail

TEAM_A_NAMESPACE="${TEAM_A_NAMESPACE:-labs-team-impersonation-a}"
TEAM_B_NAMESPACE="${TEAM_B_NAMESPACE:-labs-team-impersonation-b}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-tenant-user}"
CLEANUP_ON_EXIT="${CLEANUP_ON_EXIT:-1}"

cleanup() {
  if [ "$CLEANUP_ON_EXIT" = "1" ]; then
    kubectl delete namespace "$TEAM_A_NAMESPACE" "$TEAM_B_NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

kubectl get namespace "$TEAM_A_NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$TEAM_A_NAMESPACE" >/dev/null
kubectl get namespace "$TEAM_B_NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$TEAM_B_NAMESPACE" >/dev/null

kubectl -n "$TEAM_A_NAMESPACE" create serviceaccount "$SERVICE_ACCOUNT_NAME" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$TEAM_A_NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tenant-pod-reader
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tenant-pod-reader
subjects:
  - kind: ServiceAccount
    name: ${SERVICE_ACCOUNT_NAME}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tenant-pod-reader
EOF

principal="system:serviceaccount:${TEAM_A_NAMESPACE}:${SERVICE_ACCOUNT_NAME}"

if ! kubectl auth can-i --quiet list pods -n "$TEAM_A_NAMESPACE" --as "$principal"; then
  echo "FAIL: tenant principal was denied pod list in own namespace (${TEAM_A_NAMESPACE})." >&2
  exit 1
fi
if kubectl auth can-i --quiet list pods -n "$TEAM_B_NAMESPACE" --as "$principal"; then
  echo "FAIL: tenant principal unexpectedly allowed pod list in foreign namespace (${TEAM_B_NAMESPACE})." >&2
  exit 1
fi
if kubectl auth can-i --quiet get secrets -n "$TEAM_B_NAMESPACE" --as "$principal"; then
  echo "FAIL: tenant principal unexpectedly allowed secret read in foreign namespace (${TEAM_B_NAMESPACE})." >&2
  exit 1
fi

echo "PASS: tenant impersonation isolation smoke passed."
