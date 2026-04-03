"""add per-image update vm cpu/ram defaults

Revision ID: 0036
Revises: 0035
Create Date: 2026-04-03 19:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
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
        "image",
        sa.Column(
            "update_cpu_cores_default",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
    )
    _ensure_column(
        "image",
        sa.Column(
            "update_ram_mb_default",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("4096"),
        ),
    )
    if "image" in _table_names():
        op.execute("UPDATE image SET update_cpu_cores_default = 2 WHERE update_cpu_cores_default IS NULL")
        op.execute("UPDATE image SET update_ram_mb_default = 4096 WHERE update_ram_mb_default IS NULL")
        op.execute("UPDATE image SET update_cpu_cores_default = 2 WHERE update_cpu_cores_default < 1")
        op.execute("UPDATE image SET update_ram_mb_default = 4096 WHERE update_ram_mb_default < 512")


def downgrade() -> None:
    if "image" not in _table_names():
        return
    columns = _column_names("image")
    with op.batch_alter_table("image") as batch_op:
        if "update_ram_mb_default" in columns:
            batch_op.drop_column("update_ram_mb_default")
        if "update_cpu_cores_default" in columns:
            batch_op.drop_column("update_cpu_cores_default")
