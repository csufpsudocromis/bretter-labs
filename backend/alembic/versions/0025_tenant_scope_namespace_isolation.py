"""add tenant scope and namespace isolation columns

Revision ID: 0025
Revises: 0024
Create Date: 2026-03-20 23:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
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


def _ensure_column(
    table: str,
    column: str,
    column_type: sa.types.TypeEngine,
    *,
    server_default: str,
    nullable: bool = False,
) -> None:
    if table not in _table_names():
        return
    cols = _column_names(table)
    if column in cols:
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column(column, column_type, nullable=nullable, server_default=sa.text(server_default)))


def _ensure_index(table: str, index_name: str, columns: list[str]) -> None:
    if table not in _table_names():
        return
    if index_name in _index_names(table):
        return
    op.create_index(index_name, table, columns, unique=False)


def upgrade() -> None:
    _ensure_column("image", "tenant", sa.String(length=64), server_default="'global'")
    _ensure_column("template", "tenant", sa.String(length=64), server_default="'global'")
    _ensure_column("containerimage", "tenant", sa.String(length=64), server_default="'global'")
    _ensure_column("containertemplate", "tenant", sa.String(length=64), server_default="'global'")
    _ensure_column("imageuploadtask", "tenant", sa.String(length=64), server_default="'global'")
    _ensure_column("instance", "tenant", sa.String(length=64), server_default="'default'")
    _ensure_column("instance", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("containerinstance", "tenant", sa.String(length=64), server_default="'default'")
    _ensure_column("containerinstance", "namespace", sa.String(length=63), server_default="'labs'")
    _ensure_column("adminauditevent", "tenant", sa.String(length=64), server_default="'global'")

    _ensure_index("image", "ix_image_tenant", ["tenant"])
    _ensure_index("template", "ix_template_tenant", ["tenant"])
    _ensure_index("containerimage", "ix_containerimage_tenant", ["tenant"])
    _ensure_index("containertemplate", "ix_containertemplate_tenant", ["tenant"])
    _ensure_index("imageuploadtask", "ix_imageuploadtask_tenant", ["tenant"])
    _ensure_index("instance", "ix_instance_tenant", ["tenant"])
    _ensure_index("instance", "ix_instance_namespace", ["namespace"])
    _ensure_index("containerinstance", "ix_containerinstance_tenant", ["tenant"])
    _ensure_index("containerinstance", "ix_containerinstance_namespace", ["namespace"])
    _ensure_index("adminauditevent", "ix_adminauditevent_tenant", ["tenant"])

    if "image" in _table_names():
        op.execute("UPDATE image SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''")
    if "template" in _table_names():
        op.execute("UPDATE template SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''")
    if "containerimage" in _table_names():
        op.execute("UPDATE containerimage SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''")
    if "containertemplate" in _table_names():
        op.execute("UPDATE containertemplate SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''")
    if "imageuploadtask" in _table_names():
        op.execute("UPDATE imageuploadtask SET tenant = 'global' WHERE tenant IS NULL OR tenant = ''")
    if "adminauditevent" in _table_names():
        op.execute(
            """
            UPDATE adminauditevent
            SET tenant = COALESCE(
                (SELECT u.team FROM "user" AS u WHERE u.username = adminauditevent.actor),
                'global'
            )
            WHERE tenant IS NULL OR tenant = ''
            """
        )
    if "instance" in _table_names():
        op.execute(
            """
            UPDATE "instance"
            SET tenant = COALESCE(
                (SELECT u.team FROM "user" AS u WHERE u.username = "instance".owner),
                'default'
            )
            WHERE tenant IS NULL OR tenant = ''
            """
        )
        op.execute("UPDATE \"instance\" SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
    if "containerinstance" in _table_names():
        op.execute(
            """
            UPDATE containerinstance
            SET tenant = COALESCE(
                (SELECT u.team FROM "user" AS u WHERE u.username = containerinstance.owner),
                'default'
            )
            WHERE tenant IS NULL OR tenant = ''
            """
        )
        op.execute("UPDATE containerinstance SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")


def downgrade() -> None:
    if "adminauditevent" in _table_names() and "ix_adminauditevent_tenant" in _index_names("adminauditevent"):
        op.drop_index("ix_adminauditevent_tenant", table_name="adminauditevent")
    if "containerinstance" in _table_names():
        index_names = _index_names("containerinstance")
        if "ix_containerinstance_namespace" in index_names:
            op.drop_index("ix_containerinstance_namespace", table_name="containerinstance")
        if "ix_containerinstance_tenant" in index_names:
            op.drop_index("ix_containerinstance_tenant", table_name="containerinstance")
    if "instance" in _table_names():
        index_names = _index_names("instance")
        if "ix_instance_namespace" in index_names:
            op.drop_index("ix_instance_namespace", table_name="instance")
        if "ix_instance_tenant" in index_names:
            op.drop_index("ix_instance_tenant", table_name="instance")
    if "imageuploadtask" in _table_names() and "ix_imageuploadtask_tenant" in _index_names("imageuploadtask"):
        op.drop_index("ix_imageuploadtask_tenant", table_name="imageuploadtask")
    if "containertemplate" in _table_names() and "ix_containertemplate_tenant" in _index_names("containertemplate"):
        op.drop_index("ix_containertemplate_tenant", table_name="containertemplate")
    if "containerimage" in _table_names() and "ix_containerimage_tenant" in _index_names("containerimage"):
        op.drop_index("ix_containerimage_tenant", table_name="containerimage")
    if "template" in _table_names() and "ix_template_tenant" in _index_names("template"):
        op.drop_index("ix_template_tenant", table_name="template")
    if "image" in _table_names() and "ix_image_tenant" in _index_names("image"):
        op.drop_index("ix_image_tenant", table_name="image")

    if "adminauditevent" in _table_names() and "tenant" in _column_names("adminauditevent"):
        with op.batch_alter_table("adminauditevent") as batch_op:
            batch_op.drop_column("tenant")
    if "containerinstance" in _table_names():
        cols = _column_names("containerinstance")
        with op.batch_alter_table("containerinstance") as batch_op:
            if "namespace" in cols:
                batch_op.drop_column("namespace")
            if "tenant" in cols:
                batch_op.drop_column("tenant")
    if "instance" in _table_names():
        cols = _column_names("instance")
        with op.batch_alter_table("instance") as batch_op:
            if "namespace" in cols:
                batch_op.drop_column("namespace")
            if "tenant" in cols:
                batch_op.drop_column("tenant")
    if "imageuploadtask" in _table_names() and "tenant" in _column_names("imageuploadtask"):
        with op.batch_alter_table("imageuploadtask") as batch_op:
            batch_op.drop_column("tenant")
    if "containertemplate" in _table_names() and "tenant" in _column_names("containertemplate"):
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.drop_column("tenant")
    if "containerimage" in _table_names() and "tenant" in _column_names("containerimage"):
        with op.batch_alter_table("containerimage") as batch_op:
            batch_op.drop_column("tenant")
    if "template" in _table_names() and "tenant" in _column_names("template"):
        with op.batch_alter_table("template") as batch_op:
            batch_op.drop_column("tenant")
    if "image" in _table_names() and "tenant" in _column_names("image"):
        with op.batch_alter_table("image") as batch_op:
            batch_op.drop_column("tenant")
