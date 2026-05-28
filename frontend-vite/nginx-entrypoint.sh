#!/usr/bin/env sh
set -eu

CONF_PATH="/etc/nginx/conf.d/default.conf"

if [ -f /tls/tls.crt ] && [ -f /tls/tls.key ]; then
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
    proxy_ssl_verify off;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
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
    proxy_pass https://bretter-backend:8000/;
    proxy_ssl_verify off;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location / {
    try_files $uri /index.html;
  }
}
EOF
fi

exec nginx -g "daemon off;"
