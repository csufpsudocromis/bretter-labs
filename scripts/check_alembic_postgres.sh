#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

ALEMBIC_POSTGRES_URL="${ALEMBIC_POSTGRES_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:5432/bretter_ci_gate}"
ALEMBIC_POSTGRES_ADMIN_URL="${ALEMBIC_POSTGRES_ADMIN_URL:-postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres}"
ALEMBIC_POSTGRES_WAIT_SECONDS="${ALEMBIC_POSTGRES_WAIT_SECONDS:-60}"

if ! "$PYTHON_BIN" -c "import psycopg" >/dev/null 2>&1; then
  echo "ERROR: psycopg is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c "import sqlalchemy" >/dev/null 2>&1; then
  echo "ERROR: sqlalchemy is not installed for ${PYTHON_BIN}. Install backend/requirements-dev.txt." >&2
  exit 1
fi

echo "Running Alembic PostgreSQL gate against: ${ALEMBIC_POSTGRES_URL}"
PYTHONPATH="$ROOT_DIR/backend" \
ALEMBIC_POSTGRES_URL="$ALEMBIC_POSTGRES_URL" \
ALEMBIC_POSTGRES_ADMIN_URL="$ALEMBIC_POSTGRES_ADMIN_URL" \
ALEMBIC_POSTGRES_WAIT_SECONDS="$ALEMBIC_POSTGRES_WAIT_SECONDS" \
  "$PYTHON_BIN" - <<'PY'
import os
import time
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text

from src.migrations import run_db_migrations


def _to_psycopg_dsn(dsn: str) -> str:
    raw = str(dsn or "").strip()
    return raw.replace("+psycopg", "", 1)


def _wait_for_postgres(admin_dsn: str, timeout_seconds: int) -> None:
    deadline = time.time() + max(5, timeout_seconds)
    last_error: Exception | None = None
    pg_admin_dsn = _to_psycopg_dsn(admin_dsn)
    while time.time() < deadline:
        try:
            with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return
        except Exception as exc:  # pragma: no cover - defensive wait loop
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL did not become ready within {timeout_seconds}s: {last_error}")


def _database_name(dsn: str) -> str:
    name = (urlparse(dsn).path or "").lstrip("/")
    if not name:
        raise RuntimeError(f"Unable to determine database name from DSN: {dsn!r}")
    return name


def main() -> int:
    database_url = os.environ["ALEMBIC_POSTGRES_URL"]
    admin_url = os.environ["ALEMBIC_POSTGRES_ADMIN_URL"]
    wait_seconds = int(os.environ.get("ALEMBIC_POSTGRES_WAIT_SECONDS", "60"))

    _wait_for_postgres(admin_url, timeout_seconds=wait_seconds)
    db_name = _database_name(database_url)

    with psycopg.connect(_to_psycopg_dsn(admin_url), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = {}").format(
                    sql.Literal(db_name)
                )
            )
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

    engine = create_engine(database_url, future=True)
    run_db_migrations(engine=engine, database_url=database_url, require_schema_ready=True)
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one()
        table_count = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        ).scalar_one()
    engine.dispose()

    print(f"PASS: Alembic PostgreSQL gate succeeded (revision={revision}, table_count={table_count}).")
    return 0


raise SystemExit(main())
PY
