"""add admin audit events table

Revision ID: 0022
Revises: 0021
Create Date: 2026-03-19 20:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def upgrade() -> None:
    table_names = _table_names()
    if "adminauditevent" not in table_names:
        op.create_table(
            "adminauditevent",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("actor", sa.String(length=128), nullable=False, server_default=sa.text("'unknown'")),
            sa.Column("action", sa.String(length=128), nullable=False),
            sa.Column("target_type", sa.String(length=64), nullable=False),
            sa.Column("target_id", sa.String(length=128), nullable=False, server_default=sa.text("''")),
            sa.Column("detail", sa.String(length=512), nullable=False, server_default=sa.text("''")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    index_names = _index_names("adminauditevent")
    if "ix_adminauditevent_actor" not in index_names:
        op.create_index("ix_adminauditevent_actor", "adminauditevent", ["actor"], unique=False)
    if "ix_adminauditevent_action" not in index_names:
        op.create_index("ix_adminauditevent_action", "adminauditevent", ["action"], unique=False)
    if "ix_adminauditevent_target_type" not in index_names:
        op.create_index("ix_adminauditevent_target_type", "adminauditevent", ["target_type"], unique=False)
    if "ix_adminauditevent_created_at" not in index_names:
        op.create_index("ix_adminauditevent_created_at", "adminauditevent", ["created_at"], unique=False)


def downgrade() -> None:
    table_names = _table_names()
    if "adminauditevent" not in table_names:
        return

    index_names = _index_names("adminauditevent")
    if "ix_adminauditevent_created_at" in index_names:
        op.drop_index("ix_adminauditevent_created_at", table_name="adminauditevent")
    if "ix_adminauditevent_target_type" in index_names:
        op.drop_index("ix_adminauditevent_target_type", table_name="adminauditevent")
    if "ix_adminauditevent_action" in index_names:
        op.drop_index("ix_adminauditevent_action", table_name="adminauditevent")
    if "ix_adminauditevent_actor" in index_names:
        op.drop_index("ix_adminauditevent_actor", table_name="adminauditevent")
    op.drop_table("adminauditevent")
