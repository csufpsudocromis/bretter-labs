"""add user role column for RBAC

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-09 08:50:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
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
    if "user" not in _table_names():
        return
    if "role" not in _column_names("user"):
        op.add_column("user", sa.Column("role", sa.String(), nullable=True, server_default="user"))
    op.execute(
        sa.text(
            "UPDATE \"user\" SET role = 'platform_admin' "
            "WHERE is_admin = true AND (role IS NULL OR role = '' OR role = 'user')"
        )
    )
    op.execute(sa.text("UPDATE \"user\" SET role = 'user' WHERE role IS NULL OR role = ''"))
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column("role", existing_type=sa.String(), nullable=False, server_default="user")
        if "ix_user_role" not in _index_names("user"):
            batch_op.create_index("ix_user_role", ["role"], unique=False)


def downgrade() -> None:
    if "user" not in _table_names():
        return
    if "role" not in _column_names("user"):
        return
    with op.batch_alter_table("user") as batch_op:
        if "ix_user_role" in _index_names("user"):
            batch_op.drop_index("ix_user_role")
        batch_op.drop_column("role")
