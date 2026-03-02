"""add site font settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-02 16:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_FONT_FAMILY = "Inter, system-ui, -apple-system, sans-serif"


def _column_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {column["name"] for column in insp.get_columns("config")}


def upgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "theme_font_family" not in columns:
            batch_op.add_column(
                sa.Column(
                    "theme_font_family",
                    sa.Text(),
                    nullable=False,
                    server_default=DEFAULT_FONT_FAMILY,
                )
            )
        if "theme_font_size_base" not in columns:
            batch_op.add_column(sa.Column("theme_font_size_base", sa.Float(), nullable=False, server_default="16.0"))
        if "theme_font_size_h1" not in columns:
            batch_op.add_column(sa.Column("theme_font_size_h1", sa.Float(), nullable=False, server_default="32.0"))
        if "theme_font_size_h2" not in columns:
            batch_op.add_column(sa.Column("theme_font_size_h2", sa.Float(), nullable=False, server_default="24.0"))
    op.execute(
        "UPDATE config SET theme_font_family = 'Inter, system-ui, -apple-system, sans-serif' "
        "WHERE theme_font_family IS NULL OR theme_font_family = ''"
    )
    op.execute("UPDATE config SET theme_font_size_base = 16.0 WHERE theme_font_size_base IS NULL")
    op.execute("UPDATE config SET theme_font_size_h1 = 32.0 WHERE theme_font_size_h1 IS NULL")
    op.execute("UPDATE config SET theme_font_size_h2 = 24.0 WHERE theme_font_size_h2 IS NULL")


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "theme_font_size_h2" in columns:
            batch_op.drop_column("theme_font_size_h2")
        if "theme_font_size_h1" in columns:
            batch_op.drop_column("theme_font_size_h1")
        if "theme_font_size_base" in columns:
            batch_op.drop_column("theme_font_size_base")
        if "theme_font_family" in columns:
            batch_op.drop_column("theme_font_family")
