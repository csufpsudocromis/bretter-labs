"""add oidc role mapping settings

Revision ID: 0023
Revises: 0022
Create Date: 2026-03-19 21:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
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
    if "config" not in _table_names():
        return
    cols = _column_names("config")
    with op.batch_alter_table("config") as batch_op:
        if "sso_role_claim" not in cols:
            batch_op.add_column(sa.Column("sso_role_claim", sa.String(), nullable=False, server_default="groups"))
        if "sso_default_role" not in cols:
            batch_op.add_column(sa.Column("sso_default_role", sa.String(), nullable=False, server_default="user"))
        if "sso_role_mappings_json" not in cols:
            batch_op.add_column(
                sa.Column("sso_role_mappings_json", sa.String(), nullable=False, server_default=sa.text("'{}'"))
            )
        if "sso_auto_create_users" not in cols:
            batch_op.add_column(
                sa.Column("sso_auto_create_users", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        if "sso_sync_roles_on_login" not in cols:
            batch_op.add_column(
                sa.Column("sso_sync_roles_on_login", sa.Boolean(), nullable=False, server_default=sa.true())
            )

    op.execute("UPDATE config SET sso_role_claim = 'groups' WHERE sso_role_claim IS NULL OR sso_role_claim = ''")
    op.execute("UPDATE config SET sso_default_role = 'user' WHERE sso_default_role IS NULL OR sso_default_role = ''")
    op.execute("UPDATE config SET sso_role_mappings_json = '{}' WHERE sso_role_mappings_json IS NULL")
    op.execute("UPDATE config SET sso_auto_create_users = TRUE WHERE sso_auto_create_users IS NULL")
    op.execute("UPDATE config SET sso_sync_roles_on_login = TRUE WHERE sso_sync_roles_on_login IS NULL")


def downgrade() -> None:
    if "config" not in _table_names():
        return
    cols = _column_names("config")
    with op.batch_alter_table("config") as batch_op:
        if "sso_sync_roles_on_login" in cols:
            batch_op.drop_column("sso_sync_roles_on_login")
        if "sso_auto_create_users" in cols:
            batch_op.drop_column("sso_auto_create_users")
        if "sso_role_mappings_json" in cols:
            batch_op.drop_column("sso_role_mappings_json")
        if "sso_default_role" in cols:
            batch_op.drop_column("sso_default_role")
        if "sso_role_claim" in cols:
            batch_op.drop_column("sso_role_claim")
