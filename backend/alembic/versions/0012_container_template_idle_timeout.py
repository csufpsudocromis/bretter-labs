"""add container template idle timeout

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-05 19:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
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
    cols = _column_names("containertemplate")
    if "idle_timeout_minutes" not in cols:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.add_column(sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False, server_default="30"))
    op.execute(
        """
        UPDATE containertemplate
        SET idle_timeout_minutes = 30
        WHERE idle_timeout_minutes IS NULL OR idle_timeout_minutes < 1
        """
    )


def downgrade() -> None:
    if "containertemplate" not in _table_names():
        return
    cols = _column_names("containertemplate")
    if "idle_timeout_minutes" in cols:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.drop_column("idle_timeout_minutes")
