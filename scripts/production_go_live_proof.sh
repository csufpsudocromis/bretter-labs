#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-labs}"
REPORT_DIR="${REPORT_DIR:-$ROOT_DIR/artifacts/go-live}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"
NODE_EXTERNAL_HOST_OVERRIDE="${NODE_EXTERNAL_HOST:-}"
PUBLIC_SCHEME_OVERRIDE="${PUBLIC_SCHEME:-}"
BASE_VALUES_FILE="${BASE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-production.yaml}"
SITE_VALUES_FILE="${SITE_VALUES_FILE:-$ROOT_DIR/deploy/helm/values-prod-site.yaml}"
REQUIRE_SITE_VALUES_FILE="${REQUIRE_SITE_VALUES_FILE:-1}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
report_path="${REPORT_DIR}/production-go-live-${timestamp}.txt"
mkdir -p "$REPORT_DIR"
touch "$report_path"

log() {
  printf '%s\n' "$*" | tee -a "$report_path"
}

pass_check() {
  log "PASS: $1"
}

fail_count=0
fail_check() {
  log "FAIL: $1"
  fail_count=$((fail_count + 1))
}

run_check() {
  local name="$1"
  shift
  if "$@" >>"$report_path" 2>&1; then
    pass_check "$name"
  else
    fail_check "$name"
  fi
}

secret_data_value_b64() {
  local namespace="$1"
  local secret_name="$2"
  local data_key="$3"
  python3 - "$namespace" "$secret_name" "$data_key" <<'PY'
import json
import subprocess
import sys

namespace = str(sys.argv[1]).strip()
secret_name = str(sys.argv[2]).strip()
data_key = str(sys.argv[3]).strip()
raw = subprocess.check_output(["kubectl", "-n", namespace, "get", "secret", secret_name, "-o", "json"], text=True)
payload = json.loads(raw)
data = (payload or {}).get("data") or {}
print(str(data.get(data_key, "")), end="")
PY
}

log "Production go-live proof started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Namespace: $NAMESPACE"
log "Report: $report_path"
log "Base values: $BASE_VALUES_FILE"
if [ -f "$SITE_VALUES_FILE" ]; then
  log "Site values: $SITE_VALUES_FILE"
  run_check "production profile validator (strict merged values)" \
    python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$BASE_VALUES_FILE" -f "$SITE_VALUES_FILE"
else
  if [ "$REQUIRE_SITE_VALUES_FILE" = "1" ]; then
    fail_check "production profile validator (missing site values file: $SITE_VALUES_FILE)"
  else
    run_check "production profile validator (strict baseline values)" \
      python3 "$ROOT_DIR/scripts/validate_production_profile.py" --strict -f "$BASE_VALUES_FILE"
  fi
fi

run_check "backend rollout status" \
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-backend --timeout=300s
run_check "frontend rollout status" \
  kubectl -n "$NAMESPACE" rollout status deployment/bretter-frontend --timeout=300s

backend_meta="$(
  python3 - "$NAMESPACE" <<'PY'
import json
import subprocess
import sys

namespace = str(sys.argv[1] if len(sys.argv) > 1 else "labs").strip() or "labs"
raw = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "deployment", "bretter-backend", "-o", "json"], text=True
)
payload = json.loads(raw)
spec = (((payload or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
containers = spec.get("containers") or []
backend = next((item for item in containers if item.get("name") == "backend"), None)
if backend is None:
    print("backend container not found in deployment", file=sys.stderr)
    sys.exit(1)

env_values: dict[str, str] = {}
env_secret_refs: dict[str, tuple[str, str]] = {}
for row in backend.get("env") or []:
    name = str(row.get("name") or "").strip()
    if not name:
        continue
    if "value" in row:
        env_values[name] = str(row.get("value") or "").strip()
    value_from = row.get("valueFrom") or {}
    secret_ref = value_from.get("secretKeyRef") or {}
    secret_name = str(secret_ref.get("name") or "").strip()
    secret_key = str(secret_ref.get("key") or "").strip()
    if secret_name and secret_key:
        env_secret_refs[name] = (secret_name, secret_key)


def is_truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


errors: list[str] = []
if not is_truthy(env_values.get("BLABS_PRODUCTION_PROFILE", "")):
    errors.append("BLABS_PRODUCTION_PROFILE is not enabled in backend deployment env.")
if not is_truthy(env_values.get("BLABS_CORS_ENTERPRISE_PROFILE", "")):
    errors.append("BLABS_CORS_ENTERPRISE_PROFILE is not enabled in backend deployment env.")
if not is_truthy(env_values.get("BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED", "")):
    errors.append("BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED is not enabled in backend deployment env.")

cors_origins = env_values.get("BLABS_CORS_ALLOWED_ORIGINS", "")
if not cors_origins:
    errors.append("BLABS_CORS_ALLOWED_ORIGINS is empty in backend deployment env.")
elif "localhost" in cors_origins.lower() or "127.0.0.1" in cors_origins:
    errors.append("BLABS_CORS_ALLOWED_ORIGINS still contains localhost/127.0.0.1.")

if "BLABS_ADMIN_DEFAULT_PASSWORD" in env_values or "BLABS_ADMIN_DEFAULT_PASSWORD" in env_secret_refs:
    errors.append("BLABS_ADMIN_DEFAULT_PASSWORD is still present in backend deployment env.")

runtime_secret = env_secret_refs.get("BLABS_SECRETS_ENCRYPTION_KEY")
if runtime_secret is None:
    errors.append("BLABS_SECRETS_ENCRYPTION_KEY is not wired via secretKeyRef.")

signature_key_ref = env_values.get("BLABS_CONTAINER_SIGNATURE_KEY_REF", "")
if not signature_key_ref:
    errors.append("BLABS_CONTAINER_SIGNATURE_KEY_REF is empty in backend deployment env.")

volume_secret_names: dict[str, str] = {}
for vol in spec.get("volumes") or []:
    name = str(vol.get("name") or "").strip()
    secret = vol.get("secret") or {}
    secret_name = str(secret.get("secretName") or "").strip()
    if name:
        volume_secret_names[name] = secret_name

signature_secret_name = volume_secret_names.get("container-signature-key", "")
if signature_key_ref.startswith("/etc/bretter-signing/") and not signature_secret_name:
    errors.append("container-signature-key volume secret is missing while key ref uses /etc/bretter-signing/.")

if errors:
    for item in errors:
        print(item, file=sys.stderr)
    sys.exit(1)

runtime_secret_name = ""
runtime_secret_key = ""
if runtime_secret is not None:
    runtime_secret_name, runtime_secret_key = runtime_secret

print(f"runtime_secret_name={runtime_secret_name}")
print(f"runtime_secret_key={runtime_secret_key}")
print(f"signature_key_ref={signature_key_ref}")
print(f"signature_secret_name={signature_secret_name}")
print(f"node_external_host={env_values.get('BLABS_KUBE_NODE_EXTERNAL_HOST', '')}")
print(f"public_scheme={env_values.get('BLABS_PUBLIC_SCHEME', '')}")
print(f"cors_allowed_origins={cors_origins}")
PY
)" || fail_check "backend deployment production env + secret wiring"

runtime_secret_name=""
runtime_secret_key=""
signature_key_ref=""
signature_secret_name=""
node_external_host=""
public_scheme=""
cors_allowed_origins=""
if [ "$fail_count" -eq 0 ] || [ -n "$backend_meta" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      runtime_secret_name) runtime_secret_name="$value" ;;
      runtime_secret_key) runtime_secret_key="$value" ;;
      signature_key_ref) signature_key_ref="$value" ;;
      signature_secret_name) signature_secret_name="$value" ;;
      node_external_host) node_external_host="$value" ;;
      public_scheme) public_scheme="$value" ;;
      cors_allowed_origins) cors_allowed_origins="$value" ;;
    esac
  done <<<"$backend_meta"
  if [ -n "$backend_meta" ]; then
    pass_check "backend deployment production env + secret wiring"
  fi
fi

if [ -n "$runtime_secret_name" ] && [ -n "$runtime_secret_key" ]; then
  runtime_secret_b64="$(secret_data_value_b64 "$NAMESPACE" "$runtime_secret_name" "$runtime_secret_key" 2>/dev/null || true)"
  if [ -n "$runtime_secret_b64" ]; then
    pass_check "runtime secret exists with encryption key data"
  else
    fail_check "runtime secret exists with encryption key data"
  fi
else
  fail_check "runtime secret exists with encryption key data"
fi

if [[ "$signature_key_ref" == /etc/bretter-signing/* ]]; then
  signature_key_file="$(basename "$signature_key_ref")"
  if [ -z "$signature_secret_name" ] || [ -z "$signature_key_file" ]; then
    fail_check "signature key secret exists with expected key file"
  else
    signature_key_b64="$(secret_data_value_b64 "$NAMESPACE" "$signature_secret_name" "$signature_key_file" 2>/dev/null || true)"
    if [ -n "$signature_key_b64" ]; then
      pass_check "signature key secret exists with expected key file"
    else
      fail_check "signature key secret exists with expected key file"
    fi
  fi
fi

node_external_host="${NODE_EXTERNAL_HOST_OVERRIDE:-$node_external_host}"
public_scheme="${PUBLIC_SCHEME_OVERRIDE:-$public_scheme}"
if [ -z "$public_scheme" ]; then
  public_scheme="https"
fi
if [ -z "$node_external_host" ]; then
  fail_check "api health endpoint check (missing NODE_EXTERNAL_HOST)"
else
  health_url="${public_scheme}://${node_external_host}:30073/api/health"
  curl_tls_flags=()
  if [ "$public_scheme" = "https" ]; then
    curl_tls_flags+=(--insecure)
  fi
  attempts=$(((HEALTH_TIMEOUT_SECONDS + 4) / 5))
  if [ "$attempts" -lt 1 ]; then
    attempts=1
  fi
  health_ok=0
  for ((i = 1; i <= attempts; i++)); do
    response="$(curl -fsS --max-time 10 "${curl_tls_flags[@]}" "$health_url" 2>/dev/null || true)"
    if printf '%s' "$response" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      health_ok=1
      break
    fi
    sleep 5
  done
  if [ "$health_ok" -eq 1 ]; then
    pass_check "api health endpoint check (${health_url})"
  else
    fail_check "api health endpoint check (${health_url})"
  fi
fi

log "CORS origins seen in backend env: ${cors_allowed_origins:-<none>}"
if [ "$fail_count" -eq 0 ]; then
  log "Production go-live proof passed."
else
  log "Production go-live proof failed with ${fail_count} failing checks."
fi
log "Report written to: $report_path"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
