from pathlib import Path

from sqlalchemy import create_engine, inspect

from src.main import app
from src.migrations import run_db_migrations


def test_openapi_operation_ids_are_unique() -> None:
    schema = app.openapi()
    seen: dict[str, tuple[str, str]] = {}
    duplicates: list[tuple[str, str, str]] = []

    for path, methods in schema.get("paths", {}).items():
        for method, spec in methods.items():
            if not isinstance(spec, dict):
                continue
            operation_id = str(spec.get("operationId") or "").strip()
            if not operation_id:
                continue
            current = (method.upper(), path)
            if operation_id in seen and seen[operation_id] != current:
                prior_method, prior_path = seen[operation_id]
                duplicates.append((operation_id, f"{prior_method} {prior_path}", f"{current[0]} {current[1]}"))
            else:
                seen[operation_id] = current

    assert not duplicates, f"duplicate OpenAPI operation IDs found: {duplicates}"


def test_alembic_upgrade_head_on_clean_db(tmp_path: Path) -> None:
    db_path = tmp_path / "clean.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    run_db_migrations(engine=engine, database_url=database_url)
    run_db_migrations(engine=engine, database_url=database_url)

    with engine.connect() as conn:
        table_names = set(inspect(conn).get_table_names())

    assert "alembic_version" in table_names
    assert "template" in table_names
    assert "containertemplate" in table_names
    assert "connecttoken" in table_names
