"""add template concurrency limit columns

Revision ID: 0014
Revises: 0013
Create Date: 2026-03-06 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
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


def _add_max_active_column(table_name: str) -> None:
    if table_name not in _table_names():
        return
    if "max_active_instances" in _column_names(table_name):
        return
    op.add_column(
        table_name,
        sa.Column("max_active_instances", sa.Integer(), nullable=False, server_default="2"),
    )
    op.execute(sa.text(f"UPDATE {table_name} SET max_active_instances = 2 WHERE max_active_instances IS NULL"))
    op.execute(sa.text(f"UPDATE {table_name} SET max_active_instances = 0 WHERE max_active_instances < 0"))


def upgrade() -> None:
    _add_max_active_column("template")
    _add_max_active_column("containertemplate")


def _drop_max_active_column(table_name: str) -> None:
    if table_name not in _table_names():
        return
    if "max_active_instances" not in _column_names(table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column("max_active_instances")


def downgrade() -> None:
    _drop_max_active_column("containertemplate")
    _drop_max_active_column("template")
