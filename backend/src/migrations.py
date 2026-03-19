from pathlib import Path
from typing import Iterable

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

APP_TABLES: set[str] = {
    "adminauditevent",
    "config",
    "connecttoken",
    "containerimage",
    "containerinstance",
    "containertemplate",
    "image",
    "imageuploadtask",
    "instance",
    "oidcloginstate",
    "template",
    "teamquota",
    "token",
    "user",
}
ALEMBIC_BASELINE_REVISION = "0001"


def _alembic_config(database_url: str) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    ini_path = backend_root / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names())


def _has_any_app_tables(tables: Iterable[str]) -> bool:
    return any(name in APP_TABLES for name in tables)


def _current_heads(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return set(context.get_current_heads() or ())


def _expected_heads(cfg: Config, expected_revision: str | None = None) -> set[str]:
    expected = str(expected_revision or "").strip()
    if expected:
        return {expected}
    script = ScriptDirectory.from_config(cfg)
    return set(script.get_heads())


def assert_schema_ready(*, engine: Engine, database_url: str, expected_revision: str | None = None) -> None:
    cfg = _alembic_config(database_url)
    current_heads = _current_heads(engine)
    expected_heads = _expected_heads(cfg, expected_revision=expected_revision)
    if current_heads != expected_heads:
        current = ",".join(sorted(current_heads)) or "<none>"
        expected = ",".join(sorted(expected_heads)) or "<none>"
        raise RuntimeError(
            "Database schema revision mismatch after startup migration. "
            f"expected={expected} current={current}. Check Alembic migration state."
        )

    tables = _table_names(engine)
    missing_tables = sorted(APP_TABLES - tables)
    if missing_tables:
        raise RuntimeError(
            "Database schema is missing required application tables after startup migration: "
            + ", ".join(missing_tables)
        )


def run_db_migrations(
    *,
    engine: Engine,
    database_url: str,
    expected_revision: str | None = None,
    require_schema_ready: bool = True,
) -> None:
    cfg = _alembic_config(database_url)
    tables = _table_names(engine)
    if "alembic_version" not in tables and _has_any_app_tables(tables):
        # Legacy installs existed before Alembic. Stamp a known baseline and apply forward migrations.
        command.stamp(cfg, ALEMBIC_BASELINE_REVISION)
    command.upgrade(cfg, "head")
    if require_schema_ready:
        assert_schema_ready(engine=engine, database_url=database_url, expected_revision=expected_revision)
