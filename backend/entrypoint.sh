#!/usr/bin/env sh
set -eu

UVICORN_ARGS="backend.src.main:app --host 0.0.0.0 --port 8000"
if [ -f /tls/tls.crt ] && [ -f /tls/tls.key ]; then
  exec uvicorn $UVICORN_ARGS --ssl-certfile /tls/tls.crt --ssl-keyfile /tls/tls.key
fi

exec uvicorn $UVICORN_ARGS
