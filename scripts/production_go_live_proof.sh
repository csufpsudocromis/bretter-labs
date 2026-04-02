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
RUN_CRD_OPERATOR_CANARY="${RUN_CRD_OPERATOR_CANARY:-auto}"
CRD_CANARY_TEMPLATE_ID="${CRD_CANARY_TEMPLATE_ID:-}"
CRD_CANARY_WAIT_SECONDS="${CRD_CANARY_WAIT_SECONDS:-300}"
CRD_CANARY_RUNNING_SLO_SECONDS="${CRD_CANARY_RUNNING_SLO_SECONDS:-180}"
CRD_CANARY_DELETE_WAIT_SECONDS="${CRD_CANARY_DELETE_WAIT_SECONDS:-180}"
RUN_RESTORE_DRILL="${RUN_RESTORE_DRILL:-0}"
RESTORE_DRILL_KEEP_DB="${RESTORE_DRILL_KEEP_DB:-0}"
RUN_RESTORE_DRILL_CLEAN_NAMESPACE="${RUN_RESTORE_DRILL_CLEAN_NAMESPACE:-1}"
POST_DEPLOY_AUTH_SECRET_NAME="${POST_DEPLOY_AUTH_SECRET_NAME:-bretter-postdeploy-auth}"
POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY="${POST_DEPLOY_AUTH_ADMIN_USERNAME_KEY:-admin_username}"
POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY="${POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY:-admin_password}"
POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY="${POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY:-synthetic_username}"
POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY="${POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY:-synthetic_password}"
POST_DEPLOY_AUTH_LAB_ADMIN_USERNAME_KEY="${POST_DEPLOY_AUTH_LAB_ADMIN_USERNAME_KEY:-lab_admin_username}"
POST_DEPLOY_AUTH_LAB_ADMIN_PASSWORD_KEY="${POST_DEPLOY_AUTH_LAB_ADMIN_PASSWORD_KEY:-lab_admin_password}"
POST_DEPLOY_AUTH_NAMESPACE_ADMIN_USERNAME_KEY="${POST_DEPLOY_AUTH_NAMESPACE_ADMIN_USERNAME_KEY:-namespace_admin_username}"
POST_DEPLOY_AUTH_NAMESPACE_ADMIN_PASSWORD_KEY="${POST_DEPLOY_AUTH_NAMESPACE_ADMIN_PASSWORD_KEY:-namespace_admin_password}"
RDP_SLO_AUTH_SECRET_NAME="${RDP_SLO_AUTH_SECRET_NAME:-bretter-userflow-slo-api-auth}"
RDP_SLO_AUTH_PASSWORD_KEY="${RDP_SLO_AUTH_PASSWORD_KEY:-password}"
SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK="${SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK:-0}"
SYNTHETIC_IMAGE_UPLOAD_FILE="${SYNTHETIC_IMAGE_UPLOAD_FILE:-}"
SYNTHETIC_IMAGE_UPLOAD_NAME="${SYNTHETIC_IMAGE_UPLOAD_NAME:-}"
SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS="${SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS:-1200}"

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

secret_data_value_plain() {
  local namespace="$1"
  local secret_name="$2"
  local data_key="$3"
  local encoded
  encoded="$(secret_data_value_b64 "$namespace" "$secret_name" "$data_key" 2>/dev/null || true)"
  if [ -z "$encoded" ]; then
    return 1
  fi
  printf '%s' "$encoded" | base64 -d 2>/dev/null || return 1
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
if not is_truthy(env_values.get("BLABS_REQUIRE_SCHEMA_READY", "")):
    errors.append("BLABS_REQUIRE_SCHEMA_READY is not enabled in backend deployment env.")
if not is_truthy(env_values.get("BLABS_CORS_ENTERPRISE_PROFILE", "")):
    errors.append("BLABS_CORS_ENTERPRISE_PROFILE is not enabled in backend deployment env.")
if not is_truthy(env_values.get("BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED", "")):
    errors.append("BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED is not enabled in backend deployment env.")
if not str(env_values.get("BLABS_KUBE_VM_STORAGE_CLASS", "")).strip():
    errors.append("BLABS_KUBE_VM_STORAGE_CLASS is empty in backend deployment env.")
orchestration_backend = str(env_values.get("BLABS_ORCHESTRATION_BACKEND", "")).strip().lower()
if orchestration_backend not in {"dual", "crd"}:
    errors.append("BLABS_ORCHESTRATION_BACKEND must be dual or crd in production deployment env.")

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

for key in (
    "BLABS_DATABASE_POOL_SIZE",
    "BLABS_DATABASE_POOL_TIMEOUT_SECONDS",
    "BLABS_DATABASE_POOL_RECYCLE_SECONDS",
    "BLABS_DATABASE_STATEMENT_TIMEOUT_MS",
    "BLABS_DATABASE_SLOW_QUERY_MS",
):
    raw = str(env_values.get(key, "")).strip()
    if not raw:
        errors.append(f"{key} is empty in backend deployment env.")
        continue
    if not raw.isdigit():
        errors.append(f"{key} must be an integer in backend deployment env.")

vm_requires_privileged_runtime = (
    is_truthy(env_values.get("BLABS_KUBE_USE_KVM", "true"))
    or is_truthy(env_values.get("BLABS_VM_RUNNER_PRIVILEGED", "false"))
    or str(env_values.get("BLABS_VM_NET_BACKEND", "")).strip().lower() == "tap-nat"
)
vm_privileged_isolation_enabled = is_truthy(env_values.get("BLABS_VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED", "false"))
if vm_requires_privileged_runtime and not vm_privileged_isolation_enabled:
    errors.append("BLABS_VM_PRIVILEGED_RUNTIME_ISOLATION_ENABLED must be enabled when privileged VM runners are required.")
if vm_privileged_isolation_enabled and not str(env_values.get("BLABS_VM_PRIVILEGED_NAMESPACE_PREFIX", "")).strip():
    errors.append("BLABS_VM_PRIVILEGED_NAMESPACE_PREFIX is empty while privileged runtime isolation is enabled.")

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

blocked_mounts: list[str] = []
blocked_prefixes = ("/app/backend/src", "/app/backend/backend/src")
for mount in backend.get("volumeMounts") or []:
    mount_path = str((mount or {}).get("mountPath") or "").strip()
    if not mount_path:
        continue
    for prefix in blocked_prefixes:
        if mount_path == prefix or mount_path.startswith(f"{prefix}/"):
            blocked_mounts.append(mount_path)
            break
if blocked_mounts and not is_truthy(env_values.get("BLABS_ALLOW_CODE_MOUNT_OVERRIDES", "")):
    mounts_joined = ", ".join(sorted(set(blocked_mounts)))
    errors.append(f"immutable backend code mount override detected: {mounts_joined}")

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
print(f"orchestration_backend={env_values.get('BLABS_ORCHESTRATION_BACKEND', '')}")
print(f"kube_vm_storage_class={env_values.get('BLABS_KUBE_VM_STORAGE_CLASS', '')}")
PY
)" || fail_check "backend deployment production env + secret wiring"

runtime_secret_name=""
runtime_secret_key=""
signature_key_ref=""
signature_secret_name=""
node_external_host=""
public_scheme=""
cors_allowed_origins=""
orchestration_backend=""
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
      orchestration_backend) orchestration_backend="$value" ;;
    esac
  done <<<"$backend_meta"
  if [ -n "$backend_meta" ]; then
    pass_check "backend deployment production env + secret wiring"
  fi
fi

should_run_crd_canary=0
case "$(printf '%s' "$RUN_CRD_OPERATOR_CANARY" | tr '[:upper:]' '[:lower:]')" in
  1 | true | yes | on)
    should_run_crd_canary=1
    ;;
  0 | false | no | off)
    should_run_crd_canary=0
    ;;
  auto)
    case "$(printf '%s' "$orchestration_backend" | tr '[:upper:]' '[:lower:]')" in
      crd | dual) should_run_crd_canary=1 ;;
      *) should_run_crd_canary=0 ;;
    esac
    ;;
  *)
    fail_check "RUN_CRD_OPERATOR_CANARY must be one of: auto, 0, 1."
    should_run_crd_canary=0
    ;;
esac

if [ "$should_run_crd_canary" -eq 1 ]; then
  if [ -z "$CRD_CANARY_TEMPLATE_ID" ]; then
    CRD_CANARY_TEMPLATE_ID="$(
      python3 - "$NAMESPACE" <<'PY'
import subprocess
import sys

namespace = str(sys.argv[1] if len(sys.argv) > 1 else "labs").strip() or "labs"
pod = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "pod", "-l", "app=bretter-postgres", "-o", "jsonpath={.items[0].metadata.name}"],
    text=True,
).strip()
if not pod:
    print("", end="")
    raise SystemExit(0)
query = "SELECT id FROM template WHERE enabled = 1 ORDER BY created_at ASC LIMIT 1;"
out = subprocess.check_output(
    [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod,
        "-c",
        "postgres",
        "--",
        "sh",
        "-c",
        f"set -eu; export PGPASSWORD=\"$POSTGRES_PASSWORD\"; psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc '{query}'",
    ],
    text=True,
).strip()
print(out, end="")
PY
    )" || true
    if [ -n "$CRD_CANARY_TEMPLATE_ID" ]; then
      log "Auto-selected CRD canary template id: ${CRD_CANARY_TEMPLATE_ID}"
    fi
  fi
  if [ -z "$CRD_CANARY_TEMPLATE_ID" ]; then
    fail_check "operator LabInstance canary (missing CRD_CANARY_TEMPLATE_ID)"
  else
    run_check "operator LabInstance canary" \
      env \
      NAMESPACE="$NAMESPACE" \
      CRD_CANARY_TEMPLATE_ID="$CRD_CANARY_TEMPLATE_ID" \
      CRD_CANARY_WAIT_SECONDS="$CRD_CANARY_WAIT_SECONDS" \
      CRD_CANARY_RUNNING_SLO_SECONDS="$CRD_CANARY_RUNNING_SLO_SECONDS" \
      CRD_CANARY_DELETE_WAIT_SECONDS="$CRD_CANARY_DELETE_WAIT_SECONDS" \
      "$ROOT_DIR/scripts/crd_canary_labinstance.sh"
  fi
else
  log "CRD operator canary skipped (RUN_CRD_OPERATOR_CANARY=${RUN_CRD_OPERATOR_CANARY}, backend=${orchestration_backend:-db})."
fi

backup_replication_enabled="0"
backup_replication_secret_name="bretter-postgres-backup-replication"
backup_replication_secret_key="aws_secret_access_key"
backup_replication_object_lock_mode=""
backup_replication_object_lock_days="0"
backup_meta="$(
  python3 - "$BASE_VALUES_FILE" "$SITE_VALUES_FILE" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"values file must contain a top-level mapping: {path}")
    return payload


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


base_path = Path(sys.argv[1])
site_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
values = read_yaml(base_path)
if site_path and site_path.exists():
    values = merge(values, read_yaml(site_path))
app = values.get("appTemplateValues")
if not isinstance(app, dict):
    app = {}

enabled = "1" if is_truthy(app.get("ENABLE_POSTGRES_BACKUP_REPLICATION", "0")) else "0"
secret_name = str(app.get("POSTGRES_BACKUP_REPLICATION_SECRET_NAME", "bretter-postgres-backup-replication") or "").strip()
secret_key = str(app.get("POSTGRES_BACKUP_REPLICATION_SECRET_ACCESS_KEY_KEY", "aws_secret_access_key") or "").strip()
object_lock_mode = str(app.get("POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_MODE", "") or "").strip()
object_lock_days = str(app.get("POSTGRES_BACKUP_REPLICATION_OBJECT_LOCK_DAYS", "0") or "").strip()
print(f"backup_replication_enabled={enabled}")
print(f"backup_replication_secret_name={secret_name}")
print(f"backup_replication_secret_key={secret_key}")
print(f"backup_replication_object_lock_mode={object_lock_mode}")
print(f"backup_replication_object_lock_days={object_lock_days}")
PY
)" || fail_check "backup replication values parse"
if [ -n "$backup_meta" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      backup_replication_enabled) backup_replication_enabled="$value" ;;
      backup_replication_secret_name) backup_replication_secret_name="$value" ;;
      backup_replication_secret_key) backup_replication_secret_key="$value" ;;
      backup_replication_object_lock_mode) backup_replication_object_lock_mode="$value" ;;
      backup_replication_object_lock_days) backup_replication_object_lock_days="$value" ;;
    esac
  done <<<"$backup_meta"
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

if [ "$backup_replication_enabled" = "1" ]; then
  if [ -n "$backup_replication_secret_name" ] && [ -n "$backup_replication_secret_key" ]; then
    backup_replication_secret_b64="$(secret_data_value_b64 "$NAMESPACE" "$backup_replication_secret_name" "$backup_replication_secret_key" 2>/dev/null || true)"
    if [ -n "$backup_replication_secret_b64" ]; then
      pass_check "backup replication secret exists with expected key"
    else
      fail_check "backup replication secret exists with expected key"
    fi
  else
    fail_check "backup replication secret exists with expected key"
  fi
  run_check "postgres backup replication cronjob present" \
    kubectl -n "$NAMESPACE" get cronjob bretter-postgres-backup-replication
  run_check "backup replication object lock env policy" \
    python3 - "$NAMESPACE" "$backup_replication_object_lock_mode" "$backup_replication_object_lock_days" <<'PY'
import json
import sys
from datetime import UTC, datetime
import subprocess

namespace = str(sys.argv[1] if len(sys.argv) > 1 else "labs").strip() or "labs"
required_mode = str(sys.argv[2] if len(sys.argv) > 2 else "").strip().upper()
required_days_raw = str(sys.argv[3] if len(sys.argv) > 3 else "0").strip()
try:
    required_days = int(required_days_raw or "0")
except ValueError:
    required_days = 0
if not required_mode:
    raise SystemExit("required object lock mode is empty in merged production values")
if required_days < 1:
    raise SystemExit("required object lock days is invalid in merged production values")

raw = subprocess.check_output(
    ["kubectl", "-n", namespace, "get", "cronjob", "bretter-postgres-backup-replication", "-o", "json"],
    text=True,
)
payload = json.loads(raw or "{}")
containers = (
    ((((payload or {}).get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}
).get("template", {}).get("spec", {}).get("containers", [])
if not containers:
    raise SystemExit("backup replication cronjob has no containers")
container = containers[0]
env_values: dict[str, str] = {}
for item in container.get("env") or []:
    name = str(item.get("name") or "").strip()
    if not name:
        continue
    if "value" in item:
        env_values[name] = str(item.get("value") or "").strip()

mode = str(env_values.get("S3_OBJECT_LOCK_MODE", "")).strip().upper()
days_raw = str(env_values.get("S3_OBJECT_LOCK_DAYS", "")).strip()
if mode != required_mode:
    raise SystemExit(f"S3_OBJECT_LOCK_MODE mismatch (expected {required_mode}, found {mode or '<empty>'})")
if days_raw != str(required_days):
    raise SystemExit(f"S3_OBJECT_LOCK_DAYS mismatch (expected {required_days}, found {days_raw or '<empty>'})")

command_parts = [str(x or "") for x in (container.get("command") or [])]
command_blob = "\n".join(command_parts)
if "--object-lock-mode" not in command_blob or "--object-lock-retain-until-date" not in command_blob:
    raise SystemExit("backup replication command does not include object lock arguments")

if required_days < 7:
    raise SystemExit("object lock retention is below production minimum (7 days)")

print(f"object_lock_policy mode={mode} days={required_days}")
print(f"checked_at={datetime.now(UTC).isoformat()}")
PY
else
  pass_check "backup replication checks skipped (ENABLE_POSTGRES_BACKUP_REPLICATION=0)"
fi

postdeploy_admin_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_ADMIN_PASSWORD_KEY" 2>/dev/null || true)"
if [ -n "$postdeploy_admin_b64" ]; then
  pass_check "postdeploy admin auth secret exists with password key"
else
  fail_check "postdeploy admin auth secret exists with password key"
fi

postdeploy_synthetic_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY" 2>/dev/null || true)"
if [ -n "$postdeploy_synthetic_b64" ]; then
  pass_check "postdeploy synthetic auth secret exists with password key"
else
  fail_check "postdeploy synthetic auth secret exists with password key"
fi

postdeploy_lab_admin_user_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_LAB_ADMIN_USERNAME_KEY" 2>/dev/null || true)"
postdeploy_lab_admin_pass_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_LAB_ADMIN_PASSWORD_KEY" 2>/dev/null || true)"
if [ -n "$postdeploy_lab_admin_user_b64" ] && [ -n "$postdeploy_lab_admin_pass_b64" ]; then
  pass_check "postdeploy lab-admin auth keys exist (role synthetic check enabled)"
elif [ -n "$postdeploy_lab_admin_user_b64" ] || [ -n "$postdeploy_lab_admin_pass_b64" ]; then
  fail_check "postdeploy lab-admin auth keys are partially configured"
else
  log "Optional lab-admin synthetic role check credentials not configured."
fi

postdeploy_namespace_admin_user_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_NAMESPACE_ADMIN_USERNAME_KEY" 2>/dev/null || true)"
postdeploy_namespace_admin_pass_b64="$(secret_data_value_b64 "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_NAMESPACE_ADMIN_PASSWORD_KEY" 2>/dev/null || true)"
if [ -n "$postdeploy_namespace_admin_user_b64" ] && [ -n "$postdeploy_namespace_admin_pass_b64" ]; then
  pass_check "postdeploy namespace-admin auth keys exist (role synthetic check enabled)"
elif [ -n "$postdeploy_namespace_admin_user_b64" ] || [ -n "$postdeploy_namespace_admin_pass_b64" ]; then
  fail_check "postdeploy namespace-admin auth keys are partially configured"
else
  log "Optional namespace-admin synthetic role check credentials not configured."
fi

rdp_slo_auth_b64="$(secret_data_value_b64 "$NAMESPACE" "$RDP_SLO_AUTH_SECRET_NAME" "$RDP_SLO_AUTH_PASSWORD_KEY" 2>/dev/null || true)"
if [ -n "$rdp_slo_auth_b64" ]; then
  pass_check "rdp slo auth secret exists with password key"
else
  fail_check "rdp slo auth secret exists with password key"
fi

run_check "tenant namespace baseline resources" \
  python3 - "$NAMESPACE" <<'PY'
import json
import subprocess
import sys

control_namespace = str(sys.argv[1] if len(sys.argv) > 1 else "labs").strip() or "labs"
raw = subprocess.check_output(
    ["kubectl", "get", "namespace", "-l", "labs.bretter.io/tenant=true", "-o", "json"],
    text=True,
)
payload = json.loads(raw or "{}")
items = (payload or {}).get("items") or []
tenant_namespaces: list[str] = []
for item in items:
    name = str(((item or {}).get("metadata") or {}).get("name") or "").strip()
    if not name:
        continue
    if name == control_namespace:
        continue
    tenant_namespaces.append(name)

if not tenant_namespaces:
    print("No tenant namespaces found; baseline namespace-object check skipped.")
    sys.exit(0)

required_objects = {
    "resourcequota": ["bretter-tenant-quota"],
    "limitrange": ["bretter-tenant-default-limits"],
    "networkpolicy": [
        "default-deny-ingress",
        "default-deny-egress",
        "allow-dns-egress",
        "allow-same-namespace-traffic",
        "allow-control-plane-ingress",
    ],
}

errors: list[str] = []
for namespace in sorted(set(tenant_namespaces)):
    for kind, names in required_objects.items():
        for name in names:
            proc = subprocess.run(
                ["kubectl", "-n", namespace, "get", kind, name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                errors.append(f"{namespace}: missing {kind}/{name}")

if errors:
    for line in errors:
        print(line, file=sys.stderr)
    sys.exit(1)

print(f"Validated tenant baseline resources in {len(set(tenant_namespaces))} tenant namespace(s).")
PY
run_check "rdp connect-latency cronjob present" \
  kubectl -n "$NAMESPACE" get cronjob bretter-slo-rdp-connect-latency

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

if [ -z "$node_external_host" ]; then
  fail_check "post-deploy synthetic user flow check (missing NODE_EXTERNAL_HOST)"
else
  synthetic_username="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_SYNTHETIC_USERNAME_KEY" || true)"
  synthetic_password="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_SYNTHETIC_PASSWORD_KEY" || true)"
  lab_admin_username="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_LAB_ADMIN_USERNAME_KEY" || true)"
  lab_admin_password="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_LAB_ADMIN_PASSWORD_KEY" || true)"
  namespace_admin_username="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_NAMESPACE_ADMIN_USERNAME_KEY" || true)"
  namespace_admin_password="$(secret_data_value_plain "$NAMESPACE" "$POST_DEPLOY_AUTH_SECRET_NAME" "$POST_DEPLOY_AUTH_NAMESPACE_ADMIN_PASSWORD_KEY" || true)"
  if [ -z "$synthetic_password" ]; then
    fail_check "post-deploy synthetic user flow check (missing synthetic credentials)"
  else
    if [ -z "$synthetic_username" ]; then
      synthetic_username="admin"
    fi
    run_check "post-deploy synthetic user flow check" \
      env \
      SYNTHETIC_API_BASE="${public_scheme}://${node_external_host}:30073/api" \
      SYNTHETIC_USERNAME="$synthetic_username" \
      SYNTHETIC_PASSWORD="$synthetic_password" \
      SYNTHETIC_VERIFY_TLS=0 \
      SYNTHETIC_REQUIRE_TEMPLATES=1 \
      SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK="${SYNTHETIC_REQUIRE_IMAGE_UPLOAD_CHECK}" \
      SYNTHETIC_IMAGE_UPLOAD_FILE="${SYNTHETIC_IMAGE_UPLOAD_FILE}" \
      SYNTHETIC_IMAGE_UPLOAD_NAME="${SYNTHETIC_IMAGE_UPLOAD_NAME}" \
      SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS="${SYNTHETIC_IMAGE_UPLOAD_TIMEOUT_SECONDS}" \
      SYNTHETIC_LAB_ADMIN_USERNAME="${lab_admin_username}" \
      SYNTHETIC_LAB_ADMIN_PASSWORD="${lab_admin_password}" \
      SYNTHETIC_NAMESPACE_ADMIN_USERNAME="${namespace_admin_username}" \
      SYNTHETIC_NAMESPACE_ADMIN_PASSWORD="${namespace_admin_password}" \
      "$ROOT_DIR/scripts/post_deploy_synthetic_check.py"
  fi
fi

case "$(printf '%s' "$RUN_RESTORE_DRILL" | tr '[:upper:]' '[:lower:]')" in
  1 | true | yes | on)
    run_check "postgres restore drill" \
      env \
      NAMESPACE="$NAMESPACE" \
      KEEP_RESTORE_DB="$RESTORE_DRILL_KEEP_DB" \
      REPORT_DIR="${REPORT_DIR}/restore-drill" \
      "$ROOT_DIR/scripts/restore_drill_postgres.sh"
    case "$(printf '%s' "$RUN_RESTORE_DRILL_CLEAN_NAMESPACE" | tr '[:upper:]' '[:lower:]')" in
      1 | true | yes | on)
        run_check "clean-namespace restore drill" \
          env \
          SOURCE_NAMESPACE="$NAMESPACE" \
          KEEP_RESTORE_NAMESPACE=0 \
          REPORT_DIR="${REPORT_DIR}/restore-drill-clean-namespace" \
          "$ROOT_DIR/scripts/restore_drill_clean_namespace.sh"
        ;;
      0 | false | no | off)
        log "Clean-namespace restore drill skipped (RUN_RESTORE_DRILL_CLEAN_NAMESPACE=${RUN_RESTORE_DRILL_CLEAN_NAMESPACE})."
        ;;
      *)
        fail_check "RUN_RESTORE_DRILL_CLEAN_NAMESPACE must be one of: 0, 1, true, false."
        ;;
    esac
    ;;
  0 | false | no | off)
    log "Postgres restore drill skipped (RUN_RESTORE_DRILL=${RUN_RESTORE_DRILL})."
    ;;
  *)
    fail_check "RUN_RESTORE_DRILL must be one of: 0, 1, true, false."
    ;;
esac

if [ "$fail_count" -eq 0 ]; then
  log "Production go-live proof passed."
else
  log "Production go-live proof failed with ${fail_count} failing checks."
fi
log "Report written to: $report_path"

if [ "$fail_count" -ne 0 ]; then
  exit 1
fi
