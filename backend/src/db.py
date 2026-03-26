from contextlib import contextmanager
import logging
from pathlib import Path
import time
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, create_engine

from .config import settings
from .migrations import run_db_migrations

logger = logging.getLogger(__name__)
_SLOW_QUERY_TIMER_KEY = "blabs_slow_query_started_at"


def _database_url() -> str:
    raw = (settings.database_url or "").strip()
    if not raw:
        db_path = Path(settings.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}"
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw[len("postgresql://") :]
    return raw


DATABASE_URL = _database_url()
SQLITE_DB = DATABASE_URL.startswith("sqlite")


def _create_engine():
    if SQLITE_DB:
        return create_engine(
            DATABASE_URL,
            echo=False,
            connect_args={"check_same_thread": False},
            pool_pre_ping=False,
        )

    connect_args: dict[str, object] = {}
    statement_timeout_ms = max(0, int(getattr(settings, "database_statement_timeout_ms", 0) or 0))
    if statement_timeout_ms > 0:
        connect_args["options"] = f"-c statement_timeout={statement_timeout_ms}"

    return create_engine(
        DATABASE_URL,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=max(1, int(settings.database_pool_size or 1)),
        max_overflow=max(0, int(settings.database_pool_max_overflow or 0)),
        pool_timeout=max(1, int(settings.database_pool_timeout_seconds or 1)),
        pool_recycle=max(30, int(settings.database_pool_recycle_seconds or 30)),
    )


engine = _create_engine()


@event.listens_for(engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    conn.info[_SLOW_QUERY_TIMER_KEY] = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    started_at = conn.info.pop(_SLOW_QUERY_TIMER_KEY, None)
    if started_at is None:
        return
    threshold_ms = max(0, int(getattr(settings, "database_slow_query_ms", 0) or 0))
    if threshold_ms <= 0:
        return
    elapsed_ms = (time.perf_counter() - float(started_at)) * 1000.0
    if elapsed_ms < threshold_ms:
        return
    statement_preview = " ".join(str(statement or "").split())
    logger.warning(
        "slow_query elapsed_ms=%.1f threshold_ms=%d rowcount=%s sql=%s",
        elapsed_ms,
        threshold_ms,
        getattr(cursor, "rowcount", "?"),
        statement_preview[:300],
    )


@event.listens_for(engine, "handle_error")
def _clear_slow_query_timer_on_error(exception_context):  # noqa: ANN001
    exception_context.connection.info.pop(_SLOW_QUERY_TIMER_KEY, None)


def init_db() -> None:
    run_db_migrations(
        engine=engine,
        database_url=DATABASE_URL,
        expected_revision=settings.expected_alembic_revision or None,
        require_schema_ready=bool(settings.require_schema_ready),
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
