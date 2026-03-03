"""container template runtime hardening fields

Revision ID: 0009
Revises: 0008
Create Date: 2026-03-03 17:45:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
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
    if "containertemplate" not in _table_names():
        return
    columns = _column_names("containertemplate")
    with op.batch_alter_table("containertemplate") as batch_op:
        if "healthcheck_protocol" not in columns:
            batch_op.add_column(
                sa.Column("healthcheck_protocol", sa.String(length=16), nullable=False, server_default=sa.text("'tcp'"))
            )
        if "healthcheck_path" not in columns:
            batch_op.add_column(
                sa.Column("healthcheck_path", sa.String(length=256), nullable=False, server_default=sa.text("'/'"))
            )
        if "startup_timeout_seconds" not in columns:
            batch_op.add_column(
                sa.Column("startup_timeout_seconds", sa.Integer(), nullable=False, server_default="300")
            )
        if "expose_strategy" not in columns:
            batch_op.add_column(
                sa.Column("expose_strategy", sa.String(length=16), nullable=False, server_default=sa.text("'nodeport'"))
            )
        if "run_as_non_root" not in columns:
            batch_op.add_column(
                sa.Column("run_as_non_root", sa.Boolean(), nullable=False, server_default=sa.text("false"))
            )
        if "read_only_root_filesystem" not in columns:
            batch_op.add_column(
                sa.Column("read_only_root_filesystem", sa.Boolean(), nullable=False, server_default=sa.text("false"))
            )

    op.execute(
        """
        UPDATE containertemplate
        SET
            healthcheck_protocol = CASE
                WHEN healthcheck_protocol IN ('tcp', 'http') THEN healthcheck_protocol
                ELSE 'tcp'
            END,
            healthcheck_path = CASE
                WHEN healthcheck_path IS NULL OR trim(healthcheck_path) = '' THEN '/'
                WHEN substr(trim(healthcheck_path), 1, 1) = '/' THEN trim(healthcheck_path)
                ELSE '/' || trim(healthcheck_path)
            END,
            startup_timeout_seconds = CASE
                WHEN startup_timeout_seconds IS NULL OR startup_timeout_seconds < 10 THEN 300
                ELSE startup_timeout_seconds
            END,
            expose_strategy = CASE
                WHEN expose_strategy IN ('nodeport', 'ingress') THEN expose_strategy
                ELSE 'nodeport'
            END,
            run_as_non_root = COALESCE(run_as_non_root, false),
            read_only_root_filesystem = COALESCE(read_only_root_filesystem, false)
        """
    )


def downgrade() -> None:
    if "containertemplate" not in _table_names():
        return
    columns = _column_names("containertemplate")
    with op.batch_alter_table("containertemplate") as batch_op:
        if "read_only_root_filesystem" in columns:
            batch_op.drop_column("read_only_root_filesystem")
        if "run_as_non_root" in columns:
            batch_op.drop_column("run_as_non_root")
        if "expose_strategy" in columns:
            batch_op.drop_column("expose_strategy")
        if "startup_timeout_seconds" in columns:
            batch_op.drop_column("startup_timeout_seconds")
        if "healthcheck_path" in columns:
            batch_op.drop_column("healthcheck_path")
        if "healthcheck_protocol" in columns:
            batch_op.drop_column("healthcheck_protocol")
