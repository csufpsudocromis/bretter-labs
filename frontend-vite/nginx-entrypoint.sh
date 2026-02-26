#!/usr/bin/env sh
set -eu

CONF_PATH="/etc/nginx/conf.d/default.conf"

if [ -f /tls/tls.crt ] && [ -f /tls/tls.key ]; then
  cat >"$CONF_PATH" <<'EOF'
server {
  listen 80;
  server_name _;
  return 301 https://$host:30073$request_uri;
}

server {
  listen 443 ssl;
  server_name _;
  ssl_certificate /tls/tls.crt;
  ssl_certificate_key /tls/tls.key;
  root /usr/share/nginx/html;

  location / {
    try_files $uri /index.html;
  }
}
EOF
else
  cat >"$CONF_PATH" <<'EOF'
server {
  listen 80;
  server_name _;
  root /usr/share/nginx/html;

  location / {
    try_files $uri /index.html;
  }
}
EOF
fi

exec nginx -g "daemon off;"
