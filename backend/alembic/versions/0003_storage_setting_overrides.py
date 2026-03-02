"""add storage setting overrides to config

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-02 12:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {column["name"] for column in insp.get_columns("config")}


def upgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "storage_root_override" not in columns:
            batch_op.add_column(sa.Column("storage_root_override", sa.String(), nullable=True))
        if "kube_image_pvc_override" not in columns:
            batch_op.add_column(sa.Column("kube_image_pvc_override", sa.String(), nullable=True))
        if "kube_vm_storage_class_override" not in columns:
            batch_op.add_column(sa.Column("kube_vm_storage_class_override", sa.String(), nullable=True))


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("config") as batch_op:
        if "kube_vm_storage_class_override" in columns:
            batch_op.drop_column("kube_vm_storage_class_override")
        if "kube_image_pvc_override" in columns:
            batch_op.drop_column("kube_image_pvc_override")
        if "storage_root_override" in columns:
            batch_op.drop_column("storage_root_override")
