"""add vm template console provider

Revision ID: 0020
Revises: 0019
Create Date: 2026-03-17 09:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
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
    if "console_provider" not in _column_names("template"):
        with op.batch_alter_table("template") as batch_op:
            batch_op.add_column(
                sa.Column("console_provider", sa.String(length=16), nullable=False, server_default=sa.text("'spice'"))
            )
    op.execute(
        """
        UPDATE template
        SET console_provider = CASE
            WHEN console_provider IS NULL OR trim(console_provider) = '' THEN 'spice'
            WHEN lower(trim(console_provider)) IN ('guacamole_rdp', 'guacamole-rdp', 'guac-rdp', 'rdp') THEN 'guacamole_rdp'
            WHEN lower(trim(console_provider)) IN ('guacamole', 'guac', 'novnc', 'vnc') THEN 'guacamole'
            WHEN lower(trim(console_provider)) = 'spice' THEN 'spice'
            ELSE 'spice'
        END
        """
    )


def downgrade() -> None:
    if "template" not in _table_names():
        return
    if "console_provider" in _column_names("template"):
        with op.batch_alter_table("template") as batch_op:
            batch_op.drop_column("console_provider")
