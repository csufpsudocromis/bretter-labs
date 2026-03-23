#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALUES_FILE="${VALUES_FILE:-$ROOT_DIR/deploy/helm/values-production.yaml}"
BASE_VALUES_FILE="${BASE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values.yaml}"

if [ ! -f "$VALUES_FILE" ]; then
  echo "ERROR: values file not found: $VALUES_FILE" >&2
  exit 1
fi
if [ ! -f "$BASE_VALUES_FILE" ]; then
  echo "ERROR: base values file not found: $BASE_VALUES_FILE" >&2
  exit 1
fi

if ! command -v helm >/dev/null 2>&1; then
  echo "ERROR: helm is required for tenant isolation audit." >&2
  exit 1
fi

use_rg=0
if command -v rg >/dev/null 2>&1; then
  use_rg=1
fi

has_pattern() {
  local pattern="$1"
  local file="$2"
  if [ "$use_rg" -eq 1 ]; then
    rg -q "$pattern" "$file"
  else
    grep -Eq "$pattern" "$file"
  fi
}

if ! has_pattern '^[[:space:]]*TEAM_NAMESPACE_MODE:[[:space:]]*per_team[[:space:]]*$' "$VALUES_FILE"; then
  echo "ERROR: TEAM_NAMESPACE_MODE must be per_team in production values." >&2
  exit 1
fi
if ! has_pattern '^[[:space:]]*TEAM_NAMESPACE_PREFIX:[[:space:]]*' "$VALUES_FILE"; then
  echo "ERROR: TEAM_NAMESPACE_PREFIX must be set in production values." >&2
  exit 1
fi

if has_pattern 'resources:[[:space:]]*\["\*"' "$ROOT_DIR/deploy/helm/files/app.yaml.tpl"; then
  echo "ERROR: wildcard resources detected in app RBAC template." >&2
  exit 1
fi
if has_pattern 'verbs:[[:space:]]*\["\*"' "$ROOT_DIR/deploy/helm/files/app.yaml.tpl"; then
  echo "ERROR: wildcard verbs detected in app RBAC template." >&2
  exit 1
fi

if ! has_pattern 'name:[[:space:]]*default-deny-ingress' "$ROOT_DIR/scripts/bootstrap_team_namespace.sh"; then
  echo "ERROR: tenant bootstrap script is missing default-deny-ingress policy." >&2
  exit 1
fi
if ! has_pattern 'name:[[:space:]]*default-deny-egress' "$ROOT_DIR/scripts/bootstrap_team_namespace.sh"; then
  echo "ERROR: tenant bootstrap script is missing default-deny-egress policy." >&2
  exit 1
fi
if ! has_pattern 'name:[[:space:]]*allow-same-namespace-traffic' "$ROOT_DIR/scripts/bootstrap_team_namespace.sh"; then
  echo "ERROR: tenant bootstrap script is missing allow-same-namespace-traffic policy." >&2
  exit 1
fi

rendered="$(mktemp /tmp/bretter-tenant-audit.XXXXXX.yaml)"
trap 'rm -f "$rendered"' EXIT
helm template bretter-labs "$ROOT_DIR/deploy/helm" -f "$BASE_VALUES_FILE" -f "$VALUES_FILE" >"$rendered"

if ! has_pattern 'name:[[:space:]]*bretter-default-deny-ingress' "$rendered"; then
  echo "ERROR: rendered production manifests are missing bretter-default-deny-ingress." >&2
  exit 1
fi
if ! has_pattern 'name:[[:space:]]*bretter-backend-restrict-egress' "$rendered"; then
  echo "ERROR: rendered production manifests are missing bretter-backend-restrict-egress." >&2
  exit 1
fi
if ! has_pattern 'name:[[:space:]]*bretter-frontend-restrict-egress' "$rendered"; then
  echo "ERROR: rendered production manifests are missing bretter-frontend-restrict-egress." >&2
  exit 1
fi

echo "PASS: tenant isolation audit passed (values, RBAC, network policies, bootstrap guardrails)."
