"""add container template port

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-03 15:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
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


def upgrade() -> None:
    if "containertemplate" not in _table_names():
        return
    columns = _column_names("containertemplate")
    if "container_port" not in columns:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.add_column(sa.Column("container_port", sa.Integer(), nullable=False, server_default="80"))
    op.execute("UPDATE containertemplate SET container_port = 80 WHERE container_port IS NULL OR container_port <= 0")


def downgrade() -> None:
    if "containertemplate" not in _table_names():
        return
    columns = _column_names("containertemplate")
    if "container_port" in columns:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.drop_column("container_port")
