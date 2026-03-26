"""add managed namespace settings table

Revision ID: 0028
Revises: 0027
Create Date: 2026-03-26 15:12:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return set(insp.get_table_names())


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def _create_indexes_if_missing() -> None:
    existing = _index_names("managednamespace")
    if "ix_managednamespace_id" not in existing:
        op.create_index("ix_managednamespace_id", "managednamespace", ["id"], unique=False)
    if "ix_managednamespace_namespace" not in existing:
        op.create_index("ix_managednamespace_namespace", "managednamespace", ["namespace"], unique=False)
    if "ix_managednamespace_team_label" not in existing:
        op.create_index("ix_managednamespace_team_label", "managednamespace", ["team_label"], unique=False)


def upgrade() -> None:
    if "managednamespace" not in _table_names():
        op.create_table(
            "managednamespace",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("namespace", sa.String(), nullable=False),
            sa.Column("team_label", sa.String(), nullable=False, server_default=sa.text("'default'")),
            sa.Column("security_profile", sa.String(), nullable=False, server_default=sa.text("'baseline'")),
            sa.Column("enforce_network_policies", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("max_pods", sa.String(), nullable=False, server_default=sa.text("'200'")),
            sa.Column("max_services", sa.String(), nullable=False, server_default=sa.text("'100'")),
            sa.Column("max_persistent_volume_claims", sa.String(), nullable=False, server_default=sa.text("'200'")),
            sa.Column("requests_cpu", sa.String(), nullable=False, server_default=sa.text("'8'")),
            sa.Column("limits_cpu", sa.String(), nullable=False, server_default=sa.text("'16'")),
            sa.Column("requests_memory", sa.String(), nullable=False, server_default=sa.text("'16Gi'")),
            sa.Column("limits_memory", sa.String(), nullable=False, server_default=sa.text("'32Gi'")),
            sa.Column("requests_storage", sa.String(), nullable=False, server_default=sa.text("'2Ti'")),
            sa.Column("limit_min_cpu", sa.String(), nullable=False, server_default=sa.text("'50m'")),
            sa.Column("limit_min_memory", sa.String(), nullable=False, server_default=sa.text("'64Mi'")),
            sa.Column(
                "limit_default_request_cpu",
                sa.String(),
                nullable=False,
                server_default=sa.text("'250m'"),
            ),
            sa.Column(
                "limit_default_request_memory",
                sa.String(),
                nullable=False,
                server_default=sa.text("'256Mi'"),
            ),
            sa.Column("limit_default_cpu", sa.String(), nullable=False, server_default=sa.text("'2'")),
            sa.Column("limit_default_memory", sa.String(), nullable=False, server_default=sa.text("'2Gi'")),
            sa.Column("limit_max_cpu", sa.String(), nullable=False, server_default=sa.text("'8'")),
            sa.Column("limit_max_memory", sa.String(), nullable=False, server_default=sa.text("'16Gi'")),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("last_reconciled_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("namespace", name="uq_managednamespace_namespace"),
        )
    _create_indexes_if_missing()


def downgrade() -> None:
    if "managednamespace" in _table_names():
        op.drop_table("managednamespace")
