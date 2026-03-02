"""add background overlay opacity to site settings

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-02 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {column["name"] for column in insp.get_columns("config")}


def upgrade() -> None:
    columns = _column_names()
    if "theme_bg_image_overlay_opacity" in columns:
        return
    with op.batch_alter_table("config") as batch_op:
        batch_op.add_column(
            sa.Column("theme_bg_image_overlay_opacity", sa.Float(), nullable=False, server_default="0.0")
        )
    op.execute("UPDATE config SET theme_bg_image_overlay_opacity = 0.0 WHERE theme_bg_image_overlay_opacity IS NULL")


def downgrade() -> None:
    columns = _column_names()
    if "theme_bg_image_overlay_opacity" not in columns:
        return
    with op.batch_alter_table("config") as batch_op:
        batch_op.drop_column("theme_bg_image_overlay_opacity")
