"""ensure size byte columns use bigint

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-02 00:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_bigint(column: dict[str, object]) -> bool:
    col_type = column.get("type")
    if isinstance(col_type, sa.BigInteger):
        return True
    rendered = str(col_type).upper()
    return "BIGINT" in rendered


def _alter_size_column(table_name: str) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {col["name"]: col for col in insp.get_columns(table_name)}
    column = cols.get("size_bytes")
    if not column or _is_bigint(column):
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table_name, recreate="always") as batch_op:
            batch_op.alter_column(
                "size_bytes",
                existing_type=column["type"],
                type_=sa.BigInteger(),
                existing_nullable=bool(column.get("nullable")),
            )
        return

    op.alter_column(
        table_name,
        "size_bytes",
        existing_type=column["type"],
        type_=sa.BigInteger(),
        existing_nullable=bool(column.get("nullable")),
        postgresql_using="size_bytes::bigint",
    )


def upgrade() -> None:
    _alter_size_column("image")
    _alter_size_column("imageuploadtask")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table_name in ("imageuploadtask", "image"):
        cols = {col["name"]: col for col in insp.get_columns(table_name)}
        column = cols.get("size_bytes")
        if not column or not _is_bigint(column):
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table_name, recreate="always") as batch_op:
                batch_op.alter_column(
                    "size_bytes",
                    existing_type=column["type"],
                    type_=sa.Integer(),
                    existing_nullable=bool(column.get("nullable")),
                )
            continue
        op.alter_column(
            table_name,
            "size_bytes",
            existing_type=column["type"],
            type_=sa.Integer(),
            existing_nullable=bool(column.get("nullable")),
            postgresql_using="size_bytes::integer",
        )
