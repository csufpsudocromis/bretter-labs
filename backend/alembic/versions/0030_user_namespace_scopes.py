"""add namespace scopes json to user

Revision ID: 0030
Revises: 0029
Create Date: 2026-03-27 19:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table_name)}


def upgrade() -> None:
    if "user" not in _table_names():
        return
    columns = _column_names("user")
    if "namespace_scopes_json" not in columns:
        op.add_column(
            "user",
            sa.Column("namespace_scopes_json", sa.String(), nullable=False, server_default=sa.text("'[]'")),
        )


def downgrade() -> None:
    if "user" not in _table_names():
        return
    columns = _column_names("user")
    if "namespace_scopes_json" in columns:
        op.drop_column("user", "namespace_scopes_json")
