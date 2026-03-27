"""add configurable RBAC roles JSON to config

Revision ID: 0029
Revises: 0028
Create Date: 2026-03-27 15:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
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
    if "config" not in _table_names():
        return
    columns = _column_names("config")
    if "rbac_roles_json" not in columns:
        op.add_column(
            "config",
            sa.Column("rbac_roles_json", sa.String(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    if "config" not in _table_names():
        return
    columns = _column_names("config")
    if "rbac_roles_json" in columns:
        op.drop_column("config", "rbac_roles_json")
