"""add oidc login state table

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-09 09:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
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


def upgrade() -> None:
    if "oidcloginstate" not in _table_names():
        op.create_table(
            "oidcloginstate",
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("code_verifier", sa.String(), nullable=False),
            sa.Column("nonce", sa.String(), nullable=False),
            sa.Column("return_to", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("state"),
        )
    if "ix_oidcloginstate_state" not in _index_names("oidcloginstate"):
        op.create_index(op.f("ix_oidcloginstate_state"), "oidcloginstate", ["state"], unique=False)


def downgrade() -> None:
    if "oidcloginstate" in _table_names():
        if "ix_oidcloginstate_state" in _index_names("oidcloginstate"):
            op.drop_index(op.f("ix_oidcloginstate_state"), table_name="oidcloginstate")
        op.drop_table("oidcloginstate")
