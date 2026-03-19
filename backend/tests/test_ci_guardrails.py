import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy import text

from src.main import app
from src.migrations import assert_schema_ready, run_db_migrations


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
    assert "adminauditevent" in table_names


def test_alembic_schema_guard_rejects_unexpected_expected_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "expected-revision.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    run_db_migrations(engine=engine, database_url=database_url)

    with pytest.raises(RuntimeError, match="Database schema revision mismatch"):
        run_db_migrations(
            engine=engine,
            database_url=database_url,
            expected_revision="0001",
            require_schema_ready=True,
        )


def test_alembic_schema_guard_detects_missing_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-table.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})

    run_db_migrations(engine=engine, database_url=database_url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE connecttoken"))

    with pytest.raises(RuntimeError, match="missing required application tables"):
        assert_schema_ready(engine=engine, database_url=database_url)


def test_release_version_files_are_consistent() -> None:
    root = Path(__file__).resolve().parents[2]
    semver_pattern = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert semver_pattern.match(version), f"VERSION is not valid semantic version: {version}"

    frontend_package = json.loads((root / "frontend-vite" / "package.json").read_text(encoding="utf-8"))
    assert str(frontend_package.get("version") or "").strip() == version

    frontend_lock = json.loads((root / "frontend-vite" / "package-lock.json").read_text(encoding="utf-8"))
    assert str(frontend_lock.get("version") or "").strip() == version
    assert str(frontend_lock.get("packages", {}).get("", {}).get("version") or "").strip() == version

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert f"## [{version}]" in changelog
