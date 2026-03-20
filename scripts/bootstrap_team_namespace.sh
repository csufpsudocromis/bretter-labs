#!/usr/bin/env bash
set -euo pipefail

TEAM="${TEAM:-${1:-}}"
TEAM_NAMESPACE_MODE="${TEAM_NAMESPACE_MODE:-per_team}"
TEAM_NAMESPACE_PREFIX="${TEAM_NAMESPACE_PREFIX:-labs-team-}"
TENANT_NAMESPACE="${TENANT_NAMESPACE:-}"
CPU_REQUESTS="${CPU_REQUESTS:-8}"
CPU_LIMITS="${CPU_LIMITS:-16}"
MEMORY_REQUESTS="${MEMORY_REQUESTS:-16Gi}"
MEMORY_LIMITS="${MEMORY_LIMITS:-32Gi}"
STORAGE_REQUESTS="${STORAGE_REQUESTS:-2Ti}"
MAX_PODS="${MAX_PODS:-200}"

if [ -z "$TEAM" ]; then
  echo "ERROR: TEAM is required. Usage: TEAM=<team> scripts/bootstrap_team_namespace.sh" >&2
  exit 1
fi

case "$TEAM_NAMESPACE_MODE" in
  per_team) ;;
  *)
    echo "ERROR: TEAM_NAMESPACE_MODE must be per_team for namespace bootstrap (found: ${TEAM_NAMESPACE_MODE})." >&2
    exit 1
    ;;
esac

if [ -z "$TENANT_NAMESPACE" ]; then
  team_slug="$(printf '%s' "$TEAM" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+//; s/-+$//')"
  if [ -z "$team_slug" ]; then
    echo "ERROR: TEAM produced an empty slug after normalization." >&2
    exit 1
  fi
  TENANT_NAMESPACE="${TEAM_NAMESPACE_PREFIX}${team_slug}"
fi

if [[ ! "$TENANT_NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  echo "ERROR: TENANT_NAMESPACE must be a valid Kubernetes namespace name (found: ${TENANT_NAMESPACE})." >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl is required." >&2
  exit 1
fi

echo "Bootstrapping tenant namespace '${TENANT_NAMESPACE}' for team '${TEAM}'..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${TENANT_NAMESPACE}
  labels:
    app.kubernetes.io/part-of: bretter-labs
    labs.bretter.io/tenant: "true"
    labs.bretter.io/team: "${TEAM}"
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: bretter-tenant-quota
  namespace: ${TENANT_NAMESPACE}
spec:
  hard:
    pods: "${MAX_PODS}"
    services: "100"
    persistentvolumeclaims: "200"
    requests.cpu: "${CPU_REQUESTS}"
    limits.cpu: "${CPU_LIMITS}"
    requests.memory: "${MEMORY_REQUESTS}"
    limits.memory: "${MEMORY_LIMITS}"
    requests.storage: "${STORAGE_REQUESTS}"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: bretter-tenant-default-limits
  namespace: ${TENANT_NAMESPACE}
spec:
  limits:
    - type: Container
      min:
        cpu: 50m
        memory: 64Mi
      defaultRequest:
        cpu: 250m
        memory: 256Mi
      default:
        cpu: "2"
        memory: 2Gi
      max:
        cpu: "8"
        memory: 16Gi
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-ingress
  namespace: ${TENANT_NAMESPACE}
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress: []
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-egress
  namespace: ${TENANT_NAMESPACE}
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress: []
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns-egress
  namespace: ${TENANT_NAMESPACE}
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-same-namespace-traffic
  namespace: ${TENANT_NAMESPACE}
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector: {}
  egress:
    - to:
        - podSelector: {}
EOF

echo "PASS: tenant namespace bootstrap applied."
echo "namespace=${TENANT_NAMESPACE}"
echo "team=${TEAM}"
