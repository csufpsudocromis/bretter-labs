"""add image upload task reconcile fields

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-20 18:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
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
    if "imageuploadtask" not in _table_names():
        return

    cols = _column_names("imageuploadtask")
    with op.batch_alter_table("imageuploadtask") as batch_op:
        if "stage" not in cols:
            batch_op.add_column(sa.Column("stage", sa.String(), nullable=False, server_default="queued"))
        if "progress_percent" not in cols:
            batch_op.add_column(sa.Column("progress_percent", sa.Integer(), nullable=True))
        if "retry_count" not in cols:
            batch_op.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
        if "max_retries" not in cols:
            batch_op.add_column(sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"))
        if "next_retry_at" not in cols:
            batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(), nullable=True))
        if "last_retry_error" not in cols:
            batch_op.add_column(sa.Column("last_retry_error", sa.String(), nullable=True))
        if "finalize_started_at" not in cols:
            batch_op.add_column(sa.Column("finalize_started_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE imageuploadtask SET stage = status WHERE stage IS NULL OR stage = ''")
    op.execute("UPDATE imageuploadtask SET stage = 'queued' WHERE stage IS NULL OR stage = ''")
    op.execute("UPDATE imageuploadtask SET retry_count = 0 WHERE retry_count IS NULL")
    op.execute("UPDATE imageuploadtask SET max_retries = 0 WHERE max_retries IS NULL")


def downgrade() -> None:
    if "imageuploadtask" not in _table_names():
        return

    cols = _column_names("imageuploadtask")
    with op.batch_alter_table("imageuploadtask") as batch_op:
        if "finalize_started_at" in cols:
            batch_op.drop_column("finalize_started_at")
        if "last_retry_error" in cols:
            batch_op.drop_column("last_retry_error")
        if "next_retry_at" in cols:
            batch_op.drop_column("next_retry_at")
        if "max_retries" in cols:
            batch_op.drop_column("max_retries")
        if "retry_count" in cols:
            batch_op.drop_column("retry_count")
        if "progress_percent" in cols:
            batch_op.drop_column("progress_percent")
        if "stage" in cols:
            batch_op.drop_column("stage")
