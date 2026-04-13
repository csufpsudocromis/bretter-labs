"""add description field for iso images

Revision ID: 0037
Revises: 0036
Create Date: 2026-04-13 15:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
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


def _ensure_column(table: str, column: sa.Column) -> None:
    if table not in _table_names():
        return
    if column.name in _column_names(table):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    _ensure_column(
        "isoimage",
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    if "isoimage" in _table_names():
        op.execute("UPDATE isoimage SET description = '' WHERE description IS NULL")


def downgrade() -> None:
    if "isoimage" not in _table_names():
        return
    columns = _column_names("isoimage")
    if "description" in columns:
        with op.batch_alter_table("isoimage") as batch_op:
            batch_op.drop_column("description")
