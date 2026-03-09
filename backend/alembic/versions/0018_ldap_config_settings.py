"""add ldap config settings

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-09 16:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
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
        if "ldap_enabled" not in cols:
            batch_op.add_column(sa.Column("ldap_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "ldap_server_uri" not in cols:
            batch_op.add_column(sa.Column("ldap_server_uri", sa.String(), nullable=False, server_default=""))
        if "ldap_bind_dn" not in cols:
            batch_op.add_column(sa.Column("ldap_bind_dn", sa.String(), nullable=False, server_default=""))
        if "ldap_bind_password" not in cols:
            batch_op.add_column(sa.Column("ldap_bind_password", sa.String(), nullable=False, server_default=""))
        if "ldap_user_base_dn" not in cols:
            batch_op.add_column(sa.Column("ldap_user_base_dn", sa.String(), nullable=False, server_default=""))
        if "ldap_user_filter" not in cols:
            batch_op.add_column(
                sa.Column("ldap_user_filter", sa.String(), nullable=False, server_default="(uid={username})")
            )
        if "ldap_start_tls" not in cols:
            batch_op.add_column(sa.Column("ldap_start_tls", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "ldap_insecure_skip_verify" not in cols:
            batch_op.add_column(
                sa.Column("ldap_insecure_skip_verify", sa.Boolean(), nullable=False, server_default=sa.false())
            )
        if "ldap_timeout_seconds" not in cols:
            batch_op.add_column(sa.Column("ldap_timeout_seconds", sa.Integer(), nullable=False, server_default="10"))
        if "ldap_auto_create_users" not in cols:
            batch_op.add_column(
                sa.Column("ldap_auto_create_users", sa.Boolean(), nullable=False, server_default=sa.true())
            )


def downgrade() -> None:
    if "config" not in _table_names():
        return
    cols = _column_names("config")
    with op.batch_alter_table("config") as batch_op:
        if "ldap_auto_create_users" in cols:
            batch_op.drop_column("ldap_auto_create_users")
        if "ldap_timeout_seconds" in cols:
            batch_op.drop_column("ldap_timeout_seconds")
        if "ldap_insecure_skip_verify" in cols:
            batch_op.drop_column("ldap_insecure_skip_verify")
        if "ldap_start_tls" in cols:
            batch_op.drop_column("ldap_start_tls")
        if "ldap_user_filter" in cols:
            batch_op.drop_column("ldap_user_filter")
        if "ldap_user_base_dn" in cols:
            batch_op.drop_column("ldap_user_base_dn")
        if "ldap_bind_password" in cols:
            batch_op.drop_column("ldap_bind_password")
        if "ldap_bind_dn" in cols:
            batch_op.drop_column("ldap_bind_dn")
        if "ldap_server_uri" in cols:
            batch_op.drop_column("ldap_server_uri")
        if "ldap_enabled" in cols:
            batch_op.drop_column("ldap_enabled")
