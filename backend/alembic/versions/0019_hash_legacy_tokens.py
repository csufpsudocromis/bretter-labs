"""hash legacy plaintext session/connect tokens

Revision ID: 0019
Revises: 0018
Create Date: 2026-03-16 19:30:00.000000

"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOKEN_HASH_PREFIX = "sha256:"


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _is_hashed_storage_key(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(_TOKEN_HASH_PREFIX) and len(normalized) == len(_TOKEN_HASH_PREFIX) + 64


def _storage_key(raw: str, *, domain: str) -> str:
    normalized = str(raw or "").strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(f"{domain}:{normalized}".encode("utf-8")).hexdigest()
    return f"{_TOKEN_HASH_PREFIX}{digest}"


def _migrate_table(*, table_name: str, domain: str) -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table(table_name, metadata, autoload_with=bind)

    rows = bind.execute(sa.select(table.c.token)).all()
    for (token_value,) in rows:
        raw_token = str(token_value or "").strip()
        if not raw_token or _is_hashed_storage_key(raw_token):
            continue
        hashed_key = _storage_key(raw_token, domain=domain)
        if not hashed_key:
            continue

        existing = bind.execute(
            sa.select(sa.literal(1))
            .select_from(table)
            .where(table.c.token == hashed_key)
            .limit(1)
        ).first()

        if existing:
            bind.execute(table.delete().where(table.c.token == raw_token))
            continue
        bind.execute(table.update().where(table.c.token == raw_token).values(token=hashed_key))


def upgrade() -> None:
    tables = _table_names()
    if "token" in tables:
        _migrate_table(table_name="token", domain="session")
    if "connecttoken" in tables:
        _migrate_table(table_name="connecttoken", domain="connect")


def downgrade() -> None:
    # No-op: plaintext token values cannot be reconstructed from hashed storage keys.
    return

