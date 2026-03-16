from pathlib import Path
from datetime import timedelta

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

from src.auth import (
    connect_token_storage_key,
    lookup_session_token,
    session_token_storage_key,
    validate_connect_session,
)
from src.db import engine
from src.tables import ConnectToken, Token
from src.time_utils import utc_now


def _alembic_config(database_url: str) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_lookup_does_not_fallback_to_plaintext_rows(reset_db):
    raw_token = "legacy-plaintext-token"
    with Session(engine) as session:
        session.add(Token(token=raw_token, username="alice", issued_at=utc_now()))
        session.add(
            ConnectToken(
                token="legacy-connect-token",
                username="alice",
                instance_id="ct-legacy",
                resource_type="container",
                token_type="session",
                issued_at=utc_now(),
                expires_at=utc_now() + timedelta(minutes=10),
            )
        )
        session.commit()

        assert lookup_session_token(session, raw_token) is None

        with pytest.raises(HTTPException, match="invalid connect session"):
            validate_connect_session(
                session,
                token_value="legacy-connect-token",
                instance_id="ct-legacy",
                resource_type="container",
            )


def test_alembic_migrates_legacy_plaintext_token_rows_to_hashed_keys(tmp_path):
    db_path = tmp_path / "legacy-tokens.db"
    database_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(database_url)

    command.upgrade(cfg, "0018")

    migration_engine = create_engine(database_url, connect_args={"check_same_thread": False})
    with migration_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO user (username, password_hash, is_admin, force_password_change, role, team)
                VALUES ('legacy-user', 'hash', 0, 0, 'user', 'default')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO token (token, username, issued_at)
                VALUES (:token, 'legacy-user', CURRENT_TIMESTAMP)
                """
            ),
            {"token": "legacy-session-token"},
        )
        conn.execute(
            text(
                """
                INSERT INTO connecttoken (token, username, instance_id, resource_type, token_type, issued_at, expires_at, used_at)
                VALUES (:token, 'legacy-user', 'ct-legacy', 'container', 'grant', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                """
            ),
            {"token": "legacy-connect-token"},
        )

    command.upgrade(cfg, "head")

    with migration_engine.connect() as conn:
        migrated_session = conn.execute(
            text("SELECT token FROM token WHERE username = 'legacy-user'")
        ).scalar_one_or_none()
        migrated_connect = conn.execute(
            text("SELECT token FROM connecttoken WHERE username = 'legacy-user'")
        ).scalar_one_or_none()

    assert migrated_session == session_token_storage_key("legacy-session-token")
    assert migrated_connect == connect_token_storage_key("legacy-connect-token")
    assert migrated_session is not None and migrated_session.startswith("sha256:")
    assert migrated_connect is not None and migrated_connect.startswith("sha256:")
