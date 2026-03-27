"""add per-template enabled namespaces allowlist

Revision ID: 0032
Revises: 0031
Create Date: 2026-03-27 23:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
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
    if "template" in _table_names() and "enabled_namespaces_json" not in _column_names("template"):
        op.add_column(
            "template",
            sa.Column("enabled_namespaces_json", sa.String(), nullable=False, server_default=sa.text("'[]'")),
        )
    if "containertemplate" in _table_names() and "enabled_namespaces_json" not in _column_names("containertemplate"):
        op.add_column(
            "containertemplate",
            sa.Column("enabled_namespaces_json", sa.String(), nullable=False, server_default=sa.text("'[]'")),
        )
    if "template" in _table_names():
        op.execute(
            "UPDATE template SET enabled_namespaces_json = '[]' WHERE enabled_namespaces_json IS NULL OR enabled_namespaces_json = ''"
        )
    if "containertemplate" in _table_names():
        op.execute(
            "UPDATE containertemplate SET enabled_namespaces_json = '[]' WHERE enabled_namespaces_json IS NULL OR enabled_namespaces_json = ''"
        )


def downgrade() -> None:
    if "containertemplate" in _table_names() and "enabled_namespaces_json" in _column_names("containertemplate"):
        op.drop_column("containertemplate", "enabled_namespaces_json")
    if "template" in _table_names() and "enabled_namespaces_json" in _column_names("template"):
        op.drop_column("template", "enabled_namespaces_json")
