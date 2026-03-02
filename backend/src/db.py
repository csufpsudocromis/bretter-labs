from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings


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
    SQLModel.metadata.create_all(engine)
    if SQLITE_DB:
        return
    # Existing clusters may already have int4 size columns from older schemas.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE IF EXISTS image ALTER COLUMN size_bytes TYPE BIGINT"))
        conn.execute(text("ALTER TABLE IF EXISTS imageuploadtask ALTER COLUMN size_bytes TYPE BIGINT"))


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
