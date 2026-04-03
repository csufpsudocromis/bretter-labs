"""add iso image catalog and scratch image metadata

Revision ID: 0035
Revises: 0034
Create Date: 2026-04-03 15:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
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


def _ensure_column(table: str, column: sa.Column) -> None:
    if table not in _table_names():
        return
    if column.name in _column_names(table):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    _ensure_column(
        "image",
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default=sa.text("'uploaded'")),
    )
    _ensure_column("image", sa.Column("installer_iso_id", sa.String(length=64), nullable=True))
    _ensure_column("image", sa.Column("installer_iso_filename", sa.String(length=255), nullable=True))
    _ensure_column("image", sa.Column("installer_os_type", sa.String(length=32), nullable=True))
    _ensure_column("image", sa.Column("installer_disk_size_gib", sa.Integer(), nullable=True))
    if "image" in _table_names():
        op.execute("UPDATE image SET source_kind = 'uploaded' WHERE source_kind IS NULL OR trim(source_kind) = ''")

    if "isoimage" not in _table_names():
        op.create_table(
            "isoimage",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("filename", sa.String(), nullable=False),
            sa.Column("tenant", sa.String(), nullable=False, server_default=sa.text("'global'")),
            sa.Column("namespace", sa.String(), nullable=False, server_default=sa.text("'labs'")),
            sa.Column("shared_catalog", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("checksum", sa.String(), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_isoimage_id", "isoimage", ["id"], unique=False)
        op.create_index("ix_isoimage_namespace", "isoimage", ["namespace"], unique=False)
        op.create_index("ix_isoimage_tenant", "isoimage", ["tenant"], unique=False)
        op.create_index("ix_isoimage_shared_catalog", "isoimage", ["shared_catalog"], unique=False)


def downgrade() -> None:
    if "isoimage" in _table_names():
        op.drop_index("ix_isoimage_shared_catalog", table_name="isoimage")
        op.drop_index("ix_isoimage_tenant", table_name="isoimage")
        op.drop_index("ix_isoimage_namespace", table_name="isoimage")
        op.drop_index("ix_isoimage_id", table_name="isoimage")
        op.drop_table("isoimage")
    if "image" in _table_names():
        columns = _column_names("image")
        with op.batch_alter_table("image") as batch_op:
            if "installer_disk_size_gib" in columns:
                batch_op.drop_column("installer_disk_size_gib")
            if "installer_os_type" in columns:
                batch_op.drop_column("installer_os_type")
            if "installer_iso_filename" in columns:
                batch_op.drop_column("installer_iso_filename")
            if "installer_iso_id" in columns:
                batch_op.drop_column("installer_iso_id")
            if "source_kind" in columns:
                batch_op.drop_column("source_kind")
