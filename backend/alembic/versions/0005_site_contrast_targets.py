"""add site contrast target settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-02 14:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {column["name"] for column in insp.get_columns("config")}


def upgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "theme_contrast_body" not in columns:
            batch_op.add_column(sa.Column("theme_contrast_body", sa.Float(), nullable=False, server_default="4.5"))
        if "theme_contrast_button" not in columns:
            batch_op.add_column(sa.Column("theme_contrast_button", sa.Float(), nullable=False, server_default="4.5"))
        if "theme_contrast_tile" not in columns:
            batch_op.add_column(sa.Column("theme_contrast_tile", sa.Float(), nullable=False, server_default="4.5"))
        if "theme_contrast_tile_border" not in columns:
            batch_op.add_column(
                sa.Column("theme_contrast_tile_border", sa.Float(), nullable=False, server_default="1.5")
            )
    op.execute("UPDATE config SET theme_contrast_body = 4.5 WHERE theme_contrast_body IS NULL")
    op.execute("UPDATE config SET theme_contrast_button = 4.5 WHERE theme_contrast_button IS NULL")
    op.execute("UPDATE config SET theme_contrast_tile = 4.5 WHERE theme_contrast_tile IS NULL")
    op.execute("UPDATE config SET theme_contrast_tile_border = 1.5 WHERE theme_contrast_tile_border IS NULL")


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "theme_contrast_tile_border" in columns:
            batch_op.drop_column("theme_contrast_tile_border")
        if "theme_contrast_tile" in columns:
            batch_op.drop_column("theme_contrast_tile")
        if "theme_contrast_button" in columns:
            batch_op.drop_column("theme_contrast_button")
        if "theme_contrast_body" in columns:
            batch_op.drop_column("theme_contrast_body")
