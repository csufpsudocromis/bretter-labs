"""add namespace ownership to image/template resources

Revision ID: 0031
Revises: 0030
Create Date: 2026-03-27 21:25:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
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


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def _ensure_column(table: str, column: str, column_type: sa.types.TypeEngine, *, server_default: str) -> None:
    if table not in _table_names():
        return
    if column in _column_names(table):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column(column, column_type, nullable=False, server_default=sa.text(server_default)))


def _ensure_index(table: str, index_name: str, columns: list[str]) -> None:
    if table not in _table_names():
        return
    if index_name in _index_names(table):
        return
    op.create_index(index_name, table, columns, unique=False)


def upgrade() -> None:
    _ensure_column("image", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("template", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("containerimage", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("containertemplate", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("imageuploadtask", "namespace", sa.String(length=63), server_default="'labs'")

    _ensure_index("image", "ix_image_namespace", ["namespace"])
    _ensure_index("template", "ix_template_namespace", ["namespace"])
    _ensure_index("containerimage", "ix_containerimage_namespace", ["namespace"])
    _ensure_index("containertemplate", "ix_containertemplate_namespace", ["namespace"])
    _ensure_index("imageuploadtask", "ix_imageuploadtask_namespace", ["namespace"])

    if "image" in _table_names():
        op.execute("UPDATE image SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
    if "template" in _table_names():
        op.execute("UPDATE template SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
    if "containerimage" in _table_names():
        op.execute("UPDATE containerimage SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
    if "containertemplate" in _table_names():
        op.execute("UPDATE containertemplate SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
    if "imageuploadtask" in _table_names():
        op.execute("UPDATE imageuploadtask SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")


def downgrade() -> None:
    if "imageuploadtask" in _table_names():
        if "ix_imageuploadtask_namespace" in _index_names("imageuploadtask"):
            op.drop_index("ix_imageuploadtask_namespace", table_name="imageuploadtask")
        if "namespace" in _column_names("imageuploadtask"):
            with op.batch_alter_table("imageuploadtask") as batch_op:
                batch_op.drop_column("namespace")
    if "containertemplate" in _table_names():
        if "ix_containertemplate_namespace" in _index_names("containertemplate"):
            op.drop_index("ix_containertemplate_namespace", table_name="containertemplate")
        if "namespace" in _column_names("containertemplate"):
            with op.batch_alter_table("containertemplate") as batch_op:
                batch_op.drop_column("namespace")
    if "containerimage" in _table_names():
        if "ix_containerimage_namespace" in _index_names("containerimage"):
            op.drop_index("ix_containerimage_namespace", table_name="containerimage")
        if "namespace" in _column_names("containerimage"):
            with op.batch_alter_table("containerimage") as batch_op:
                batch_op.drop_column("namespace")
    if "template" in _table_names():
        if "ix_template_namespace" in _index_names("template"):
            op.drop_index("ix_template_namespace", table_name="template")
        if "namespace" in _column_names("template"):
            with op.batch_alter_table("template") as batch_op:
                batch_op.drop_column("namespace")
    if "image" in _table_names():
        if "ix_image_namespace" in _index_names("image"):
            op.drop_index("ix_image_namespace", table_name="image")
        if "namespace" in _column_names("image"):
            with op.batch_alter_table("image") as batch_op:
                batch_op.drop_column("namespace")
