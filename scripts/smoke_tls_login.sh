#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for cmd in docker curl openssl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $cmd" >&2
    exit 1
  fi
done

tmp_dir="$(mktemp -d /tmp/bretter-tls-login.XXXXXX)"
tls_dir="${tmp_dir}/tls"
mkdir -p "$tls_dir"

network_name="blabs-smoke-tls-$RANDOM-$RANDOM"
backend_name="blabs-backend-smoke-$RANDOM"
frontend_name="blabs-frontend-smoke-$RANDOM"
backend_image="bretter-backend:ci-smoke-tls"
frontend_image="bretter-frontend:ci-smoke-tls"
bootstrap_password="ci-smoke-bootstrap-secret"

cleanup() {
  docker rm -f "$frontend_name" >/dev/null 2>&1 || true
  docker rm -f "$backend_name" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "[smoke-tls] generating temporary TLS cert"
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "${tls_dir}/tls.key" \
  -out "${tls_dir}/tls.crt" \
  -days 2 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  >/dev/null 2>&1
chmod 644 "${tls_dir}/tls.crt" "${tls_dir}/tls.key"

echo "[smoke-tls] building backend image"
docker build -t "$backend_image" -f "${ROOT_DIR}/backend/Dockerfile" "$ROOT_DIR" >/dev/null
echo "[smoke-tls] building frontend image"
docker build --build-arg VITE_API_BASE=/api -t "$frontend_image" -f "${ROOT_DIR}/frontend-vite/Dockerfile" "$ROOT_DIR" >/dev/null

echo "[smoke-tls] creating docker network"
docker network create "$network_name" >/dev/null

echo "[smoke-tls] starting backend container (TLS enabled)"
docker run -d \
  --name "$backend_name" \
  --network "$network_name" \
  --network-alias bretter-backend \
  -v "${tls_dir}:/tls:ro" \
  -e "BLABS_ADMIN_DEFAULT_PASSWORD=${bootstrap_password}" \
  -e "BLABS_DATABASE_PATH=/tmp/app.db" \
  "$backend_image" >/dev/null

echo "[smoke-tls] starting frontend container (TLS reverse proxy)"
docker run -d \
  --name "$frontend_name" \
  --network "$network_name" \
  -v "${tls_dir}:/tls:ro" \
  -p 127.0.0.1::8443 \
  "$frontend_image" >/dev/null

frontend_port="$(docker port "$frontend_name" 8443/tcp | awk -F: 'NR==1 {print $2}')"
if [ -z "$frontend_port" ]; then
  echo "ERROR: failed to discover frontend mapped port" >&2
  exit 1
fi

frontend_base="https://127.0.0.1:${frontend_port}"
health_url="${frontend_base}/api/user/settings/sso"
login_url="${frontend_base}/auth/login"

echo "[smoke-tls] waiting for frontend TLS endpoint"
max_attempts=45
attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
  status="$(curl -sk -o "${tmp_dir}/health.json" -w '%{http_code}' "$health_url" || true)"
  if [ "$status" = "200" ]; then
    break
  fi
  sleep 2
  attempt=$((attempt + 1))
done
if [ "$attempt" -gt "$max_attempts" ]; then
  echo "ERROR: frontend TLS health probe failed" >&2
  docker logs "$frontend_name" >&2 || true
  docker logs "$backend_name" >&2 || true
  exit 1
fi

echo "[smoke-tls] validating login through frontend -> backend TLS path"
login_payload="$(printf '{"username":"admin","password":"%s"}' "$bootstrap_password")"
login_status="$(
  curl -sk -o "${tmp_dir}/login.json" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    --data "$login_payload" \
    "$login_url" || true
)"
if [ "$login_status" != "200" ]; then
  echo "ERROR: login over TLS path failed with HTTP ${login_status}" >&2
  cat "${tmp_dir}/login.json" >&2 || true
  docker logs "$frontend_name" >&2 || true
  docker logs "$backend_name" >&2 || true
  exit 1
fi
if ! grep -q '"username":"admin"' "${tmp_dir}/login.json"; then
  echo "ERROR: login response did not contain admin profile payload" >&2
  cat "${tmp_dir}/login.json" >&2 || true
  exit 1
fi

echo "[smoke-tls] TLS login smoke check passed"
