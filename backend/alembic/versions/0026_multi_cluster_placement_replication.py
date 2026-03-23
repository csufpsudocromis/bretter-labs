"""add multi-cluster placement and replication primitives

Revision ID: 0026
Revises: 0025
Create Date: 2026-03-23 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
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


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table_name)}


def _ensure_column(
    table: str,
    column: str,
    column_type: sa.types.TypeEngine,
    *,
    server_default: str,
    nullable: bool = False,
) -> None:
    if table not in _table_names():
        return
    cols = _column_names(table)
    if column in cols:
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column(column, column_type, nullable=nullable, server_default=sa.text(server_default)))


def _ensure_index(table: str, index_name: str, columns: list[str]) -> None:
    if table not in _table_names():
        return
    if index_name in _index_names(table):
        return
    op.create_index(index_name, table, columns, unique=False)


def _ensure_cluster_table() -> None:
    if "cluster" in _table_names():
        return
    op.create_table(
        "cluster",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False, server_default=sa.text("'local'")),
        sa.Column("compliance_tags_csv", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("capacity_weight", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("runtime_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_local", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("kubeconfig", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("health_status", sa.String(length=32), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("health_message", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cluster_id", "cluster", ["id"], unique=False)
    op.create_index("ix_cluster_region", "cluster", ["region"], unique=False)


def _ensure_team_placement_policy_table() -> None:
    if "teamplacementpolicy" in _table_names():
        return
    op.create_table(
        "teamplacementpolicy",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("team", sa.String(length=64), nullable=False),
        sa.Column("preferred_cluster_id", sa.String(length=64), nullable=True),
        sa.Column("hard_pin_cluster", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("required_regions_csv", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("required_compliance_tags_csv", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("allowed_cluster_ids_csv", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(
            ["preferred_cluster_id"], ["cluster.id"], name="fk_teamplacementpolicy_preferred_cluster"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team", name="uq_teamplacementpolicy_team"),
    )
    op.create_index("ix_teamplacementpolicy_id", "teamplacementpolicy", ["id"], unique=False)
    op.create_index("ix_teamplacementpolicy_team", "teamplacementpolicy", ["team"], unique=False)


def _ensure_artifact_replication_table() -> None:
    if "artifactreplication" in _table_names():
        return
    op.create_table(
        "artifactreplication",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant", sa.String(length=64), nullable=False, server_default=sa.text("'global'")),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("source_cluster_id", sa.String(length=64), nullable=False),
        sa.Column("target_cluster_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("detail", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("requested_by", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.ForeignKeyConstraint(["source_cluster_id"], ["cluster.id"], name="fk_artifactreplication_source_cluster"),
        sa.ForeignKeyConstraint(["target_cluster_id"], ["cluster.id"], name="fk_artifactreplication_target_cluster"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_type",
            "artifact_id",
            "target_cluster_id",
            name="uq_artifactreplication_artifact_target",
        ),
    )
    op.create_index("ix_artifactreplication_id", "artifactreplication", ["id"], unique=False)
    op.create_index("ix_artifactreplication_tenant", "artifactreplication", ["tenant"], unique=False)
    op.create_index("ix_artifactreplication_artifact_type", "artifactreplication", ["artifact_type"], unique=False)
    op.create_index("ix_artifactreplication_artifact_id", "artifactreplication", ["artifact_id"], unique=False)
    op.create_index("ix_artifactreplication_status", "artifactreplication", ["status"], unique=False)


def _ensure_local_cluster_row() -> None:
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(1) FROM cluster WHERE id = 'local'")).scalar()
    if int(count or 0) > 0:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO cluster (
              id, name, region, compliance_tags_csv, capacity_weight,
              enabled, schedule_enabled, runtime_enabled, is_local, kubeconfig, notes,
              health_status, health_message, created_at, updated_at
            ) VALUES (
              'local', 'Local Cluster', 'local', '', 100,
              true, true, true, true, '', 'Auto-managed local runtime cluster.',
              'unknown', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
    )


def upgrade() -> None:
    _ensure_column("image", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("imageuploadtask", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("template", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("instance", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("containerimage", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("containertemplate", "cluster_id", sa.String(length=64), server_default="'local'")
    _ensure_column("containerinstance", "cluster_id", sa.String(length=64), server_default="'local'")

    _ensure_index("image", "ix_image_cluster_id", ["cluster_id"])
    _ensure_index("imageuploadtask", "ix_imageuploadtask_cluster_id", ["cluster_id"])
    _ensure_index("template", "ix_template_cluster_id", ["cluster_id"])
    _ensure_index("instance", "ix_instance_cluster_id", ["cluster_id"])
    _ensure_index("containerimage", "ix_containerimage_cluster_id", ["cluster_id"])
    _ensure_index("containertemplate", "ix_containertemplate_cluster_id", ["cluster_id"])
    _ensure_index("containerinstance", "ix_containerinstance_cluster_id", ["cluster_id"])

    if "image" in _table_names():
        op.execute("UPDATE image SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "imageuploadtask" in _table_names():
        op.execute("UPDATE imageuploadtask SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "template" in _table_names():
        op.execute("UPDATE template SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "instance" in _table_names():
        op.execute("UPDATE \"instance\" SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "containerimage" in _table_names():
        op.execute("UPDATE containerimage SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "containertemplate" in _table_names():
        op.execute("UPDATE containertemplate SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")
    if "containerinstance" in _table_names():
        op.execute("UPDATE containerinstance SET cluster_id = 'local' WHERE cluster_id IS NULL OR cluster_id = ''")

    _ensure_cluster_table()
    _ensure_team_placement_policy_table()
    _ensure_artifact_replication_table()
    _ensure_local_cluster_row()


def downgrade() -> None:
    if "artifactreplication" in _table_names():
        for index_name in (
            "ix_artifactreplication_status",
            "ix_artifactreplication_artifact_id",
            "ix_artifactreplication_artifact_type",
            "ix_artifactreplication_tenant",
            "ix_artifactreplication_id",
        ):
            if index_name in _index_names("artifactreplication"):
                op.drop_index(index_name, table_name="artifactreplication")
        op.drop_table("artifactreplication")

    if "teamplacementpolicy" in _table_names():
        for index_name in ("ix_teamplacementpolicy_team", "ix_teamplacementpolicy_id"):
            if index_name in _index_names("teamplacementpolicy"):
                op.drop_index(index_name, table_name="teamplacementpolicy")
        op.drop_table("teamplacementpolicy")

    if "cluster" in _table_names():
        for index_name in ("ix_cluster_region", "ix_cluster_id"):
            if index_name in _index_names("cluster"):
                op.drop_index(index_name, table_name="cluster")
        op.drop_table("cluster")

    if "containerinstance" in _table_names() and "ix_containerinstance_cluster_id" in _index_names("containerinstance"):
        op.drop_index("ix_containerinstance_cluster_id", table_name="containerinstance")
    if "containertemplate" in _table_names() and "ix_containertemplate_cluster_id" in _index_names("containertemplate"):
        op.drop_index("ix_containertemplate_cluster_id", table_name="containertemplate")
    if "containerimage" in _table_names() and "ix_containerimage_cluster_id" in _index_names("containerimage"):
        op.drop_index("ix_containerimage_cluster_id", table_name="containerimage")
    if "instance" in _table_names() and "ix_instance_cluster_id" in _index_names("instance"):
        op.drop_index("ix_instance_cluster_id", table_name="instance")
    if "template" in _table_names() and "ix_template_cluster_id" in _index_names("template"):
        op.drop_index("ix_template_cluster_id", table_name="template")
    if "imageuploadtask" in _table_names() and "ix_imageuploadtask_cluster_id" in _index_names("imageuploadtask"):
        op.drop_index("ix_imageuploadtask_cluster_id", table_name="imageuploadtask")
    if "image" in _table_names() and "ix_image_cluster_id" in _index_names("image"):
        op.drop_index("ix_image_cluster_id", table_name="image")

    if "containerinstance" in _table_names() and "cluster_id" in _column_names("containerinstance"):
        with op.batch_alter_table("containerinstance") as batch_op:
            batch_op.drop_column("cluster_id")
    if "containertemplate" in _table_names() and "cluster_id" in _column_names("containertemplate"):
        with op.batch_alter_table("containertemplate") as batch_op:
            batch_op.drop_column("cluster_id")
    if "containerimage" in _table_names() and "cluster_id" in _column_names("containerimage"):
        with op.batch_alter_table("containerimage") as batch_op:
            batch_op.drop_column("cluster_id")
    if "instance" in _table_names() and "cluster_id" in _column_names("instance"):
        with op.batch_alter_table("instance") as batch_op:
            batch_op.drop_column("cluster_id")
    if "template" in _table_names() and "cluster_id" in _column_names("template"):
        with op.batch_alter_table("template") as batch_op:
            batch_op.drop_column("cluster_id")
    if "imageuploadtask" in _table_names() and "cluster_id" in _column_names("imageuploadtask"):
        with op.batch_alter_table("imageuploadtask") as batch_op:
            batch_op.drop_column("cluster_id")
    if "image" in _table_names() and "cluster_id" in _column_names("image"):
        with op.batch_alter_table("image") as batch_op:
            batch_op.drop_column("cluster_id")
