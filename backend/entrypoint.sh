#!/usr/bin/env sh
set -eu

UVICORN_WORKERS="${UVICORN_WORKERS:-1}"
case "$UVICORN_WORKERS" in
  '' | *[!0-9]* | 0)
    echo "UVICORN_WORKERS must be an integer >= 1." >&2
    exit 1
    ;;
esac

UVICORN_ARGS="backend.src.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}"
if [ -f /tls/tls.crt ] && [ -f /tls/tls.key ]; then
  exec uvicorn $UVICORN_ARGS --ssl-certfile /tls/tls.crt --ssl-keyfile /tls/tls.key
fi

exec uvicorn $UVICORN_ARGS
