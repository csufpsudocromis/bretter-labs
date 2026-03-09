"""add team namespace quotas and user team field

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-09 10:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {col["name"] for col in insp.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def upgrade() -> None:
    tables = _table_names()
    if "user" in tables and "team" not in _column_names("user"):
        op.add_column(
            "user",
            sa.Column("team", sa.String(length=64), nullable=False, server_default="default"),
        )
        op.execute(sa.text('UPDATE "user" SET team = \'default\' WHERE team IS NULL OR team = \'\''))
    if "user" in tables and "ix_user_team" not in _index_names("user"):
        op.create_index(op.f("ix_user_team"), "user", ["team"], unique=False)

    if "teamquota" not in tables:
        op.create_table(
            "teamquota",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("team", sa.String(), nullable=False),
            sa.Column("namespace", sa.String(), nullable=False),
            sa.Column("max_concurrent_labs", sa.Integer(), nullable=True),
            sa.Column("max_cpu_millicores", sa.Integer(), nullable=True),
            sa.Column("max_memory_mb", sa.Integer(), nullable=True),
            sa.Column("max_storage_gib", sa.Integer(), nullable=True),
            sa.Column("idle_timeout_minutes_cap", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("team", "namespace", name="uq_teamquota_team_namespace"),
        )
    if "ix_teamquota_id" not in _index_names("teamquota"):
        op.create_index(op.f("ix_teamquota_id"), "teamquota", ["id"], unique=False)
    if "ix_teamquota_team" not in _index_names("teamquota"):
        op.create_index(op.f("ix_teamquota_team"), "teamquota", ["team"], unique=False)
    if "ix_teamquota_namespace" not in _index_names("teamquota"):
        op.create_index(op.f("ix_teamquota_namespace"), "teamquota", ["namespace"], unique=False)


def downgrade() -> None:
    tables = _table_names()
    if "teamquota" in tables:
        index_names = _index_names("teamquota")
        if "ix_teamquota_namespace" in index_names:
            op.drop_index(op.f("ix_teamquota_namespace"), table_name="teamquota")
        if "ix_teamquota_team" in index_names:
            op.drop_index(op.f("ix_teamquota_team"), table_name="teamquota")
        if "ix_teamquota_id" in index_names:
            op.drop_index(op.f("ix_teamquota_id"), table_name="teamquota")
        op.drop_table("teamquota")

    if "user" in tables:
        index_names = _index_names("user")
        col_names = _column_names("user")
        if "ix_user_team" in index_names:
            op.drop_index(op.f("ix_user_team"), table_name="user")
        if "team" in col_names:
            op.drop_column("user", "team")
