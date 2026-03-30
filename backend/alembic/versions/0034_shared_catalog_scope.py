"""add shared catalog scope flags for images and templates

Revision ID: 0034
Revises: 0033
Create Date: 2026-03-27 22:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {column["name"] for column in insp.get_columns(table_name)}


def _ensure_bool_column(table: str, column: str) -> None:
    if table not in _table_names():
        return
    if column in _column_names(table):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.text("false")))


def upgrade() -> None:
    for table in ("image", "template", "containerimage", "containertemplate"):
        _ensure_bool_column(table, "shared_catalog")
    for table in ("image", "template", "containerimage", "containertemplate"):
        if table in _table_names():
            op.execute(f"UPDATE {table} SET shared_catalog = false WHERE shared_catalog IS NULL")


def downgrade() -> None:
    for table in ("containertemplate", "containerimage", "template", "image"):
        if table in _table_names() and "shared_catalog" in _column_names(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column("shared_catalog")
