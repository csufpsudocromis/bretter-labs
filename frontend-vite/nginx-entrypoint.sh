#!/usr/bin/env sh
set -eu

CONF_PATH="/etc/nginx/conf.d/default.conf"
TRUST_BUNDLE="/tmp/backend-upstream-ca.pem"

if [ -f /tls/tls.crt ] && [ -f /tls/tls.key ]; then
  if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    cat /etc/ssl/certs/ca-certificates.crt /tls/tls.crt >"$TRUST_BUNDLE"
  else
    cp /tls/tls.crt "$TRUST_BUNDLE"
  fi
  cat >"$CONF_PATH" <<'EOF'
server {
  listen 8080;
  server_name _;
  return 301 https://$host:30073$request_uri;
}

server {
  listen 8443 ssl;
  server_name _;
  ssl_certificate /tls/tls.crt;
  ssl_certificate_key /tls/tls.key;
  root /usr/share/nginx/html;

  location /api/ {
    proxy_pass https://bretter-backend:8000/;
    proxy_ssl_trusted_certificate /tmp/backend-upstream-ca.pem;
    proxy_ssl_verify on;
    proxy_ssl_verify_depth 3;
    proxy_ssl_server_name on;
    proxy_ssl_name localhost;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /api;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location /auth/ {
    proxy_pass https://bretter-backend:8000;
    proxy_ssl_trusted_certificate /tmp/backend-upstream-ca.pem;
    proxy_ssl_verify on;
    proxy_ssl_verify_depth 3;
    proxy_ssl_server_name on;
    proxy_ssl_name localhost;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location /user/ {
    proxy_pass https://bretter-backend:8000;
    proxy_ssl_trusted_certificate /tmp/backend-upstream-ca.pem;
    proxy_ssl_verify on;
    proxy_ssl_verify_depth 3;
    proxy_ssl_server_name on;
    proxy_ssl_name localhost;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location / {
    try_files $uri /index.html;
  }
}
EOF
else
  cat >"$CONF_PATH" <<'EOF'
server {
  listen 8443;
  server_name _;
  root /usr/share/nginx/html;

  location /api/ {
    proxy_pass http://bretter-backend:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /api;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location /auth/ {
    proxy_pass http://bretter-backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location /user/ {
    proxy_pass http://bretter-backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location / {
    try_files $uri /index.html;
  }
}
EOF
fi

# nginx.conf in this image sets pid under /run, which is not writable for non-root.
# Copy to /tmp, rewrite pid there, and launch nginx with the writable config path.
NGINX_CONF_RUNTIME="/tmp/nginx.conf"
cp /etc/nginx/nginx.conf "$NGINX_CONF_RUNTIME"
sed -i 's#^\s*pid\s\+/run/nginx\.pid;#pid /tmp/nginx.pid;#' "$NGINX_CONF_RUNTIME"

exec nginx -c "$NGINX_CONF_RUNTIME" -g "daemon off;"
