"""add managed namespace runtime policy fields and audit namespace column

Revision ID: 0033
Revises: 0032
Create Date: 2026-03-27 22:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
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


def upgrade() -> None:
    table_names = _table_names()
    if "managednamespace" in table_names:
        cols = _column_names("managednamespace")
        if "idle_timeout_minutes_default" not in cols:
            op.add_column(
                "managednamespace",
                sa.Column("idle_timeout_minutes_default", sa.Integer(), nullable=False, server_default="30"),
            )
        if "vm_auto_delete_minutes_default" not in cols:
            op.add_column(
                "managednamespace",
                sa.Column("vm_auto_delete_minutes_default", sa.Integer(), nullable=False, server_default="60"),
            )
        if "container_auto_delete_minutes_default" not in cols:
            op.add_column(
                "managednamespace",
                sa.Column("container_auto_delete_minutes_default", sa.Integer(), nullable=False, server_default="60"),
            )
        if "queue_max_pending" not in cols:
            op.add_column(
                "managednamespace",
                sa.Column("queue_max_pending", sa.Integer(), nullable=False, server_default="25"),
            )
        if "upload_max_bytes" not in cols:
            op.add_column(
                "managednamespace",
                sa.Column("upload_max_bytes", sa.BigInteger(), nullable=False, server_default=str(60 * 1024**3)),
            )
        op.execute(
            "UPDATE managednamespace SET idle_timeout_minutes_default = 30 "
            "WHERE idle_timeout_minutes_default IS NULL OR idle_timeout_minutes_default <= 0"
        )
        op.execute(
            "UPDATE managednamespace SET vm_auto_delete_minutes_default = 60 "
            "WHERE vm_auto_delete_minutes_default IS NULL OR vm_auto_delete_minutes_default <= 0"
        )
        op.execute(
            "UPDATE managednamespace SET container_auto_delete_minutes_default = 60 "
            "WHERE container_auto_delete_minutes_default IS NULL OR container_auto_delete_minutes_default <= 0"
        )
        op.execute(
            "UPDATE managednamespace SET queue_max_pending = 25 "
            "WHERE queue_max_pending IS NULL OR queue_max_pending <= 0"
        )
        op.execute(
            f"UPDATE managednamespace SET upload_max_bytes = {60 * 1024**3} "
            "WHERE upload_max_bytes IS NULL OR upload_max_bytes <= 0"
        )

    if "adminauditevent" in table_names:
        cols = _column_names("adminauditevent")
        if "namespace" not in cols:
            op.add_column(
                "adminauditevent",
                sa.Column("namespace", sa.String(length=63), nullable=False, server_default="labs"),
            )
        op.execute("UPDATE adminauditevent SET namespace = 'labs' WHERE namespace IS NULL OR namespace = ''")
        op.execute("CREATE INDEX IF NOT EXISTS ix_adminauditevent_namespace ON adminauditevent(namespace)")


def downgrade() -> None:
    table_names = _table_names()
    if "adminauditevent" in table_names and "namespace" in _column_names("adminauditevent"):
        op.execute("DROP INDEX IF EXISTS ix_adminauditevent_namespace")
        op.drop_column("adminauditevent", "namespace")

    if "managednamespace" in table_names:
        cols = _column_names("managednamespace")
        if "upload_max_bytes" in cols:
            op.drop_column("managednamespace", "upload_max_bytes")
        if "queue_max_pending" in cols:
            op.drop_column("managednamespace", "queue_max_pending")
        if "container_auto_delete_minutes_default" in cols:
            op.drop_column("managednamespace", "container_auto_delete_minutes_default")
        if "vm_auto_delete_minutes_default" in cols:
            op.drop_column("managednamespace", "vm_auto_delete_minutes_default")
        if "idle_timeout_minutes_default" in cols:
            op.drop_column("managednamespace", "idle_timeout_minutes_default")
