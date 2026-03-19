from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, create_engine

from .config import settings
from .migrations import run_db_migrations


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
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if SQLITE_DB else {},
    pool_pre_ping=not SQLITE_DB,
)


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
