"""add connect token table

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-05 21:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {index["name"] for index in insp.get_indexes(table_name)}


def upgrade() -> None:
    if "connecttoken" not in _table_names():
        op.create_table(
            "connecttoken",
            sa.Column("token", sa.String(), nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("instance_id", sa.String(), nullable=False),
            sa.Column("resource_type", sa.String(), nullable=False),
            sa.Column("token_type", sa.String(), nullable=False),
            sa.Column("issued_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["username"], ["user.username"]),
            sa.PrimaryKeyConstraint("token"),
        )
    existing = _index_names("connecttoken")
    if "ix_connecttoken_username" not in existing:
        op.create_index("ix_connecttoken_username", "connecttoken", ["username"], unique=False)
    if "ix_connecttoken_instance_id" not in existing:
        op.create_index("ix_connecttoken_instance_id", "connecttoken", ["instance_id"], unique=False)


def downgrade() -> None:
    if "connecttoken" not in _table_names():
        return
    op.drop_table("connecttoken")
