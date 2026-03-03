"""add container image/template/runtime tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-03 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx.get("name", "") for idx in insp.get_indexes(table_name)}


def upgrade() -> None:
    tables = _table_names()

    if "containerimage" not in tables:
        op.create_table(
            "containerimage",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("image_ref", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if "containertemplate" not in tables:
        op.create_table(
            "containertemplate",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("container_image_id", sa.String(), nullable=False),
            sa.Column("cpu_millicores", sa.Integer(), nullable=False),
            sa.Column("memory_mb", sa.Integer(), nullable=False),
            sa.Column("command", sa.String(), nullable=True),
            sa.Column("args_json", sa.String(), nullable=False),
            sa.Column("env_json", sa.String(), nullable=False),
            sa.Column("auto_delete_minutes", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["container_image_id"], ["containerimage.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if "containerinstance" not in tables:
        op.create_table(
            "containerinstance",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("template_id", sa.String(), nullable=False),
            sa.Column("owner", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("pod_name", sa.String(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("last_active_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner"], ["user.username"]),
            sa.ForeignKeyConstraint(["template_id"], ["containertemplate.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "containerimage" in _table_names():
        idx = _index_names("containerimage")
        if "ix_containerimage_id" not in idx:
            op.create_index("ix_containerimage_id", "containerimage", ["id"], unique=False)
    if "containertemplate" in _table_names():
        idx = _index_names("containertemplate")
        if "ix_containertemplate_id" not in idx:
            op.create_index("ix_containertemplate_id", "containertemplate", ["id"], unique=False)
    if "containerinstance" in _table_names():
        idx = _index_names("containerinstance")
        if "ix_containerinstance_id" not in idx:
            op.create_index("ix_containerinstance_id", "containerinstance", ["id"], unique=False)


def downgrade() -> None:
    tables = _table_names()

    if "containerinstance" in tables:
        idx = _index_names("containerinstance")
        if "ix_containerinstance_id" in idx:
            op.drop_index("ix_containerinstance_id", table_name="containerinstance")
        op.drop_table("containerinstance")

    if "containertemplate" in tables:
        idx = _index_names("containertemplate")
        if "ix_containertemplate_id" in idx:
            op.drop_index("ix_containertemplate_id", table_name="containertemplate")
        op.drop_table("containertemplate")

    if "containerimage" in tables:
        idx = _index_names("containerimage")
        if "ix_containerimage_id" in idx:
            op.drop_index("ix_containerimage_id", table_name="containerimage")
        op.drop_table("containerimage")
