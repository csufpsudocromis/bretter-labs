"""container template versioning queue and security metadata

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-03 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
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


def _has_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for item in insp.get_unique_constraints(table_name):
        if item.get("name") == constraint_name:
            return True
    return False


def upgrade() -> None:
    tables = _table_names()

    if "containerimage" in tables:
        cols = _column_names("containerimage")
        with op.batch_alter_table("containerimage") as batch_op:
            if "last_scan_at" not in cols:
                batch_op.add_column(sa.Column("last_scan_at", sa.DateTime(), nullable=True))
            if "last_scan_status" not in cols:
                batch_op.add_column(
                    sa.Column("last_scan_status", sa.String(length=32), nullable=False, server_default=sa.text("'never'"))
                )
            if "last_scan_summary" not in cols:
                batch_op.add_column(
                    sa.Column("last_scan_summary", sa.String(length=512), nullable=False, server_default=sa.text("''"))
                )

    if "containertemplate" in tables:
        cols = _column_names("containertemplate")
        with op.batch_alter_table("containertemplate") as batch_op:
            if "template_key" not in cols:
                batch_op.add_column(
                    sa.Column("template_key", sa.String(length=64), nullable=False, server_default=sa.text("''"))
                )
            if "version" not in cols:
                batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
            if "is_default" not in cols:
                batch_op.add_column(
                    sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("true"))
                )
            if "readiness_http_status" not in cols:
                batch_op.add_column(
                    sa.Column("readiness_http_status", sa.Integer(), nullable=False, server_default="200")
                )
            if "readiness_success_path" not in cols:
                batch_op.add_column(sa.Column("readiness_success_path", sa.String(length=256), nullable=True))
            if "dependency_checks_json" not in cols:
                batch_op.add_column(
                    sa.Column("dependency_checks_json", sa.Text(), nullable=False, server_default=sa.text("'[]'"))
                )

        op.execute(
            """
            UPDATE containertemplate
            SET
                template_key = CASE
                    WHEN template_key IS NULL OR trim(template_key) = '' THEN id
                    ELSE template_key
                END,
                version = CASE
                    WHEN version IS NULL OR version < 1 THEN 1
                    ELSE version
                END,
                is_default = COALESCE(is_default, true),
                readiness_http_status = CASE
                    WHEN readiness_http_status IS NULL OR readiness_http_status < 100 OR readiness_http_status > 599 THEN 200
                    ELSE readiness_http_status
                END,
                readiness_success_path = CASE
                    WHEN readiness_success_path IS NULL OR trim(readiness_success_path) = '' THEN NULL
                    WHEN substr(trim(readiness_success_path), 1, 1) = '/' THEN trim(readiness_success_path)
                    ELSE '/' || trim(readiness_success_path)
                END,
                dependency_checks_json = CASE
                    WHEN dependency_checks_json IS NULL OR trim(dependency_checks_json) = '' THEN '[]'
                    ELSE dependency_checks_json
                END
            """
        )

        if not _has_constraint("containertemplate", "uq_containertemplate_key_version"):
            with op.batch_alter_table("containertemplate") as batch_op:
                batch_op.create_unique_constraint(
                    "uq_containertemplate_key_version",
                    ["template_key", "version"],
                )

    if "containerinstance" in tables:
        cols = _column_names("containerinstance")
        with op.batch_alter_table("containerinstance") as batch_op:
            if "queue_attempts" not in cols:
                batch_op.add_column(sa.Column("queue_attempts", sa.Integer(), nullable=False, server_default="0"))
            if "queue_not_before" not in cols:
                batch_op.add_column(sa.Column("queue_not_before", sa.DateTime(), nullable=True))
            if "queue_reason" not in cols:
                batch_op.add_column(sa.Column("queue_reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    tables = _table_names()

    if "containerinstance" in tables:
        cols = _column_names("containerinstance")
        with op.batch_alter_table("containerinstance") as batch_op:
            if "queue_reason" in cols:
                batch_op.drop_column("queue_reason")
            if "queue_not_before" in cols:
                batch_op.drop_column("queue_not_before")
            if "queue_attempts" in cols:
                batch_op.drop_column("queue_attempts")

    if "containertemplate" in tables:
        cols = _column_names("containertemplate")
        if _has_constraint("containertemplate", "uq_containertemplate_key_version"):
            with op.batch_alter_table("containertemplate") as batch_op:
                batch_op.drop_constraint("uq_containertemplate_key_version", type_="unique")
        with op.batch_alter_table("containertemplate") as batch_op:
            if "dependency_checks_json" in cols:
                batch_op.drop_column("dependency_checks_json")
            if "readiness_success_path" in cols:
                batch_op.drop_column("readiness_success_path")
            if "readiness_http_status" in cols:
                batch_op.drop_column("readiness_http_status")
            if "is_default" in cols:
                batch_op.drop_column("is_default")
            if "version" in cols:
                batch_op.drop_column("version")
            if "template_key" in cols:
                batch_op.drop_column("template_key")

    if "containerimage" in tables:
        cols = _column_names("containerimage")
        with op.batch_alter_table("containerimage") as batch_op:
            if "last_scan_summary" in cols:
                batch_op.drop_column("last_scan_summary")
            if "last_scan_status" in cols:
                batch_op.drop_column("last_scan_status")
            if "last_scan_at" in cols:
                batch_op.drop_column("last_scan_at")
