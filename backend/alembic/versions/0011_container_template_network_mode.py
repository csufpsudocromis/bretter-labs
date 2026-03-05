"""add container template network mode

Revision ID: 0011
Revises: 0010
Create Date: 2026-03-05 17:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
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
    tables = _table_names()
    if "containertemplate" not in tables:
        return
    cols = _column_names("containertemplate")
    if "network_mode" not in cols:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.add_column(
                sa.Column("network_mode", sa.String(length=16), nullable=False, server_default=sa.text("'bridge'"))
            )
    op.execute(
        """
        UPDATE containertemplate
        SET network_mode = CASE
            WHEN network_mode IS NULL OR trim(network_mode) = '' THEN 'bridge'
            WHEN lower(trim(network_mode)) IN ('bridge','none','isolated','unrestricted') THEN lower(trim(network_mode))
            ELSE 'bridge'
        END
        """
    )


def downgrade() -> None:
    tables = _table_names()
    if "containertemplate" not in tables:
        return
    cols = _column_names("containertemplate")
    if "network_mode" in cols:
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.drop_column("network_mode")
