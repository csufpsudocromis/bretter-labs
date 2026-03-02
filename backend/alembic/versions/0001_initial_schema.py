"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_vms", sa.Integer(), nullable=False),
        sa.Column("per_user_vm_limit", sa.Integer(), nullable=False),
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("site_title", sa.String(), nullable=False),
        sa.Column("site_tagline", sa.String(), nullable=False),
        sa.Column("theme_bg_color", sa.String(), nullable=False),
        sa.Column("theme_text_color", sa.String(), nullable=False),
        sa.Column("theme_button_color", sa.String(), nullable=False),
        sa.Column("theme_button_text_color", sa.String(), nullable=False),
        sa.Column("theme_bg_image", sa.String(), nullable=False),
        sa.Column("theme_tile_bg", sa.String(), nullable=False),
        sa.Column("theme_tile_border", sa.String(), nullable=False),
        sa.Column("theme_tile_opacity", sa.Float(), nullable=False),
        sa.Column("theme_tile_border_opacity", sa.Float(), nullable=False),
        sa.Column("sso_enabled", sa.Boolean(), nullable=False),
        sa.Column("sso_provider", sa.String(), nullable=False),
        sa.Column("sso_client_id", sa.String(), nullable=False),
        sa.Column("sso_client_secret", sa.String(), nullable=False),
        sa.Column("sso_authorize_url", sa.String(), nullable=False),
        sa.Column("sso_token_url", sa.String(), nullable=False),
        sa.Column("sso_userinfo_url", sa.String(), nullable=False),
        sa.Column("sso_redirect_url", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "image",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("source_pvc", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_image_id"), "image", ["id"], unique=False)
    op.create_table(
        "user",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("force_password_change", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_index(op.f("ix_user_username"), "user", ["username"], unique=False)
    op.create_table(
        "imageuploadtask",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("image_id", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=True),
        sa.Column("source_pvc", sa.String(), nullable=True),
        sa.Column("upload_pvc", sa.String(), nullable=True),
        sa.Column("finalize_job", sa.String(), nullable=True),
        sa.Column("copy_job", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_imageuploadtask_id"), "imageuploadtask", ["id"], unique=False)
    op.create_table(
        "template",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("os_type", sa.String(), nullable=False),
        sa.Column("image_id", sa.String(), nullable=False),
        sa.Column("cpu_cores", sa.Integer(), nullable=False),
        sa.Column("ram_mb", sa.Integer(), nullable=False),
        sa.Column("auto_delete_minutes", sa.Integer(), nullable=False),
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("preclone_pool_size", sa.Integer(), nullable=False),
        sa.Column("preclone_pool_max", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("network_mode", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["image.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_template_id"), "template", ["id"], unique=False)
    op.create_table(
        "token",
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["username"], ["user.username"]),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(op.f("ix_token_token"), "token", ["token"], unique=False)
    op.create_table(
        "instance",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("disk_pvc", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_active_at", sa.DateTime(), nullable=False),
        sa.Column("console_url", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["owner"], ["user.username"]),
        sa.ForeignKeyConstraint(["template_id"], ["template.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_instance_id"), "instance", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_instance_id"), table_name="instance")
    op.drop_table("instance")
    op.drop_index(op.f("ix_token_token"), table_name="token")
    op.drop_table("token")
    op.drop_index(op.f("ix_template_id"), table_name="template")
    op.drop_table("template")
    op.drop_index(op.f("ix_imageuploadtask_id"), table_name="imageuploadtask")
    op.drop_table("imageuploadtask")
    op.drop_index(op.f("ix_user_username"), table_name="user")
    op.drop_table("user")
    op.drop_index(op.f("ix_image_id"), table_name="image")
    op.drop_table("image")
    op.drop_table("config")
