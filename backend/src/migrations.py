from pathlib import Path
from typing import Iterable

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

APP_TABLES: set[str] = {
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


def run_db_migrations(*, engine: Engine, database_url: str) -> None:
    cfg = _alembic_config(database_url)
    tables = _table_names(engine)
    if "alembic_version" not in tables and _has_any_app_tables(tables):
        # Legacy installs existed before Alembic. Stamp a known baseline and apply forward migrations.
        command.stamp(cfg, ALEMBIC_BASELINE_REVISION)
    command.upgrade(cfg, "head")
