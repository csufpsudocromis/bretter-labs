#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-labs}"
EXCEPTION_NAME="${EXCEPTION_NAME:-bretter-dev-unsigned-images}"
POLICY_NAME="${POLICY_NAME:-bretter-verify-image-signatures}"
RULE_NAME="${RULE_NAME:-verify-image-signatures}"
LABEL_KEY="${LABEL_KEY:-security.bretter-labs.io/allow-unsigned-dev}"
LABEL_VALUE="${LABEL_VALUE:-true}"
MODE="${MODE:-apply}"
PRODUCTION_PROFILE="${PRODUCTION_PROFILE:-0}"

case "$MODE" in
  apply | delete) ;;
  *)
    echo "ERROR: MODE must be apply or delete." >&2
    exit 1
    ;;
esac

if [ "$MODE" = "delete" ]; then
  kubectl -n "$NAMESPACE" delete policyexception "$EXCEPTION_NAME" --ignore-not-found >/dev/null 2>&1 || true
  echo "PASS: deleted dev signature exception ${NAMESPACE}/${EXCEPTION_NAME} (if present)."
  exit 0
fi

case "$(printf '%s' "$PRODUCTION_PROFILE" | tr '[:upper:]' '[:lower:]')" in
  1 | true | yes | on)
    echo "ERROR: refusing to apply dev signature exception when PRODUCTION_PROFILE=${PRODUCTION_PROFILE}." >&2
    exit 1
    ;;
esac

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl is required." >&2
  exit 1
fi

kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: kyverno.io/v2
kind: PolicyException
metadata:
  name: ${EXCEPTION_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/part-of: bretter-labs
    security.bretter-labs.io/dev-exception: "true"
spec:
  background: false
  exceptions:
    - policyName: ${POLICY_NAME}
      ruleNames:
        - ${RULE_NAME}
  match:
    any:
      - resources:
          kinds:
            - Pod
          namespaces:
            - ${NAMESPACE}
          selector:
            matchLabels:
              ${LABEL_KEY}: "${LABEL_VALUE}"
EOF

echo "PASS: applied dev signature exception ${NAMESPACE}/${EXCEPTION_NAME}."
echo "Pods in ${NAMESPACE} must carry ${LABEL_KEY}=${LABEL_VALUE} to bypass ${POLICY_NAME}/${RULE_NAME}."
