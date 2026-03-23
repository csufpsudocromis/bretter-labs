"""add cluster runtime namespace and kubeconfig secret reference fields

Revision ID: 0027
Revises: 0026
Create Date: 2026-03-23 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
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


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if table_name not in _table_names():
        return
    if column.name in _column_names(table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    _add_column_if_missing(
        "cluster",
        sa.Column("runtime_namespace", sa.String(length=63), nullable=False, server_default=sa.text("'labs'")),
    )
    _add_column_if_missing(
        "cluster",
        sa.Column("kubeconfig_secret_name", sa.String(length=253), nullable=False, server_default=sa.text("''")),
    )
    _add_column_if_missing(
        "cluster",
        sa.Column(
            "kubeconfig_secret_namespace",
            sa.String(length=63),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    _add_column_if_missing(
        "cluster",
        sa.Column(
            "kubeconfig_secret_key", sa.String(length=253), nullable=False, server_default=sa.text("'kubeconfig'")
        ),
    )
    if "cluster" in _table_names():
        op.execute(
            "UPDATE cluster SET runtime_namespace = 'labs' WHERE runtime_namespace IS NULL OR trim(runtime_namespace) = ''"
        )
        op.execute(
            "UPDATE cluster SET kubeconfig_secret_key = 'kubeconfig' "
            "WHERE kubeconfig_secret_key IS NULL OR trim(kubeconfig_secret_key) = ''"
        )


def downgrade() -> None:
    if "cluster" not in _table_names():
        return
    cols = _column_names("cluster")
    if "kubeconfig_secret_key" in cols:
        with op.batch_alter_table("cluster") as batch_op:
            batch_op.drop_column("kubeconfig_secret_key")
    cols = _column_names("cluster")
    if "kubeconfig_secret_namespace" in cols:
        with op.batch_alter_table("cluster") as batch_op:
            batch_op.drop_column("kubeconfig_secret_namespace")
    cols = _column_names("cluster")
    if "kubeconfig_secret_name" in cols:
        with op.batch_alter_table("cluster") as batch_op:
            batch_op.drop_column("kubeconfig_secret_name")
    cols = _column_names("cluster")
    if "runtime_namespace" in cols:
        with op.batch_alter_table("cluster") as batch_op:
            batch_op.drop_column("runtime_namespace")
