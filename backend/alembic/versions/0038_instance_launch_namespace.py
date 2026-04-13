"""add launch_namespace to instance

Revision ID: 0038
Revises: 0037
Create Date: 2026-04-13 15:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: Union[str, Sequence[str], None] = "0037"
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
    if "instance" not in _table_names():
        return
    if "launch_namespace" not in _column_names("instance"):
        with op.batch_alter_table("instance") as batch_op:
            batch_op.add_column(
                sa.Column("launch_namespace", sa.String(length=63), nullable=False, server_default=sa.text("'labs'"))
            )
    op.execute(
        "UPDATE instance SET launch_namespace = COALESCE(NULLIF(trim(namespace), ''), 'labs') "
        "WHERE launch_namespace IS NULL OR trim(launch_namespace) = ''"
    )
    with op.batch_alter_table("instance") as batch_op:
        batch_op.alter_column("launch_namespace", server_default=None)
    op.execute("CREATE INDEX IF NOT EXISTS ix_instance_launch_namespace ON instance (launch_namespace)")


def downgrade() -> None:
    if "instance" not in _table_names():
        return
    if "launch_namespace" in _column_names("instance"):
        with op.batch_alter_table("instance") as batch_op:
            batch_op.drop_column("launch_namespace")
