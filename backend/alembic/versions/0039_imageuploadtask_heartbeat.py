"""add last_heartbeat_at to imageuploadtask

Revision ID: 0039
Revises: 0038
Create Date: 2026-04-14 13:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039"
down_revision: Union[str, Sequence[str], None] = "0038"
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
    if "imageuploadtask" not in _table_names():
        return
    cols = _column_names("imageuploadtask")
    if "last_heartbeat_at" not in cols:
        with op.batch_alter_table("imageuploadtask") as batch_op:
            batch_op.add_column(sa.Column("last_heartbeat_at", sa.DateTime(timezone=False), nullable=True))
    op.execute(
        "UPDATE imageuploadtask SET last_heartbeat_at = updated_at "
        "WHERE last_heartbeat_at IS NULL AND updated_at IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_imageuploadtask_last_heartbeat_at ON imageuploadtask (last_heartbeat_at)")


def downgrade() -> None:
    if "imageuploadtask" not in _table_names():
        return
    if "last_heartbeat_at" in _column_names("imageuploadtask"):
        with op.batch_alter_table("imageuploadtask") as batch_op:
            batch_op.drop_column("last_heartbeat_at")
