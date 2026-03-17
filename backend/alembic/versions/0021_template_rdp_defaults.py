"""add vm template rdp default credentials

Revision ID: 0021
Revises: 0020
Create Date: 2026-03-17 16:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
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
    if "template" not in _table_names():
        return
    cols = _column_names("template")
    if "rdp_default_username" not in cols:
        with op.batch_alter_table("template") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "rdp_default_username",
                    sa.String(length=128),
                    nullable=False,
                    server_default=sa.text("''"),
                )
            )
    if "rdp_default_password" not in cols:
        with op.batch_alter_table("template") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "rdp_default_password",
                    sa.String(length=1024),
                    nullable=False,
                    server_default=sa.text("''"),
                )
            )
    op.execute("UPDATE template SET rdp_default_username = '' WHERE rdp_default_username IS NULL")
    op.execute("UPDATE template SET rdp_default_password = '' WHERE rdp_default_password IS NULL")


def downgrade() -> None:
    if "template" not in _table_names():
        return
    cols = _column_names("template")
    if "rdp_default_password" in cols:
        with op.batch_alter_table("template") as batch_op:
            batch_op.drop_column("rdp_default_password")
    if "rdp_default_username" in cols:
        with op.batch_alter_table("template") as batch_op:
            batch_op.drop_column("rdp_default_username")
