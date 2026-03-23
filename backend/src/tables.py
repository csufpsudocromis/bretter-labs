from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from .time_utils import utc_now


class User(SQLModel, table=True):
    username: str = Field(primary_key=True, index=True)
    password_hash: str
    role: str = Field(default="user", index=True)
    team: str = Field(default="default", index=True)
    is_admin: bool = False
    force_password_change: bool = False


class Token(SQLModel, table=True):
    token: str = Field(primary_key=True, index=True)
    username: str = Field(foreign_key="user.username")
    issued_at: datetime = Field(default_factory=utc_now)


class ConnectToken(SQLModel, table=True):
    token: str = Field(primary_key=True, index=True)
    username: str = Field(foreign_key="user.username", index=True)
    instance_id: str = Field(index=True)
    resource_type: str = "container"
    token_type: str = "grant"
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=utc_now)
    used_at: Optional[datetime] = None


class OIDCLoginState(SQLModel, table=True):
    state: str = Field(primary_key=True, index=True)
    code_verifier: str
    nonce: str
    return_to: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=utc_now)


class Image(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    filename: str
    tenant: str = Field(default="global", index=True)
    cluster_id: str = Field(default="local", index=True)
    source_pvc: Optional[str] = None
    checksum: str
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    created_at: datetime = Field(default_factory=utc_now)


class ImageUploadTask(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    original_filename: str
    filename: str
    tenant: str = Field(default="global", index=True)
    cluster_id: str = Field(default="local", index=True)
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    status: str = "queued"
    stage: str = "queued"
    progress_percent: Optional[int] = None
    detail: str = ""
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 0
    next_retry_at: Optional[datetime] = None
    last_retry_error: Optional[str] = None
    finalize_started_at: Optional[datetime] = None
    image_id: Optional[str] = None
    checksum: Optional[str] = None
    source_pvc: Optional[str] = None
    upload_pvc: Optional[str] = None
    finalize_job: Optional[str] = None
    copy_job: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Template(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    tenant: str = Field(default="global", index=True)
    cluster_id: str = Field(default="local", index=True)
    description: str = ""
    os_type: str = "windows"
    image_id: str = Field(foreign_key="image.id")
    cpu_cores: int
    ram_mb: int
    auto_delete_minutes: int = 30
    idle_timeout_minutes: int = 30
    preclone_pool_size: int = 0
    preclone_pool_max: int = 0
    max_active_instances: int = 2
    enabled: bool = False
    network_mode: str = "bridge"
    console_provider: str = "spice"
    rdp_default_username: str = ""
    rdp_default_password: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class Instance(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    template_id: str = Field(foreign_key="template.id")
    owner: str = Field(foreign_key="user.username")
    tenant: str = Field(default="default", index=True)
    namespace: str = Field(default="labs", index=True)
    cluster_id: str = Field(default="local", index=True)
    status: str = "pending"
    disk_pvc: Optional[str] = None
    started_at: datetime = Field(default_factory=utc_now)
    last_active_at: datetime = Field(default_factory=utc_now)
    console_url: Optional[str] = None


class ContainerImage(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    image_ref: str
    tenant: str = Field(default="global", index=True)
    cluster_id: str = Field(default="local", index=True)
    last_scan_at: Optional[datetime] = None
    last_scan_status: str = "never"
    last_scan_summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ContainerTemplate(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("template_key", "version", name="uq_containertemplate_key_version"),)

    id: str = Field(primary_key=True, index=True)
    template_key: str = Field(index=True)
    version: int = 1
    is_default: bool = True
    name: str
    tenant: str = Field(default="global", index=True)
    cluster_id: str = Field(default="local", index=True)
    description: str = ""
    container_image_id: str = Field(foreign_key="containerimage.id")
    cpu_millicores: int = 500
    memory_mb: int = 512
    container_port: int = 80
    healthcheck_protocol: str = "tcp"
    healthcheck_path: str = "/"
    readiness_http_status: int = 200
    readiness_success_path: Optional[str] = None
    startup_timeout_seconds: int = 300
    dependency_checks_json: str = "[]"
    expose_strategy: str = "nodeport"
    network_mode: str = "bridge"
    run_as_non_root: bool = False
    read_only_root_filesystem: bool = False
    command: Optional[str] = None
    args_json: str = "[]"
    env_json: str = "{}"
    auto_delete_minutes: int = 60
    idle_timeout_minutes: int = 30
    max_active_instances: int = 2
    enabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ContainerInstance(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    template_id: str = Field(foreign_key="containertemplate.id")
    owner: str = Field(foreign_key="user.username")
    tenant: str = Field(default="default", index=True)
    namespace: str = Field(default="labs", index=True)
    cluster_id: str = Field(default="local", index=True)
    status: str = "pending"
    pod_name: Optional[str] = None
    queue_attempts: int = 0
    queue_not_before: Optional[datetime] = None
    queue_reason: Optional[str] = None
    started_at: datetime = Field(default_factory=utc_now)
    last_active_at: datetime = Field(default_factory=utc_now)


class Config(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    max_concurrent_vms: int = 50
    per_user_vm_limit: int = 2
    idle_timeout_minutes: int = 30
    storage_root_override: Optional[str] = None
    kube_image_pvc_override: Optional[str] = None
    kube_vm_storage_class_override: Optional[str] = None
    site_title: str = "Bretter Labs"
    site_tagline: str = "Run Virtual Labs and Software"
    theme_bg_color: str = "#f5f5f5"
    theme_text_color: str = "#111111"
    theme_button_color: str = "#2563eb"
    theme_button_text_color: str = "#ffffff"
    theme_bg_image: str = ""
    theme_bg_image_overlay_opacity: float = 0.0
    theme_contrast_body: float = 4.5
    theme_contrast_button: float = 4.5
    theme_contrast_tile: float = 4.5
    theme_contrast_tile_border: float = 1.5
    theme_font_family: str = "Inter, system-ui, -apple-system, sans-serif"
    theme_font_size_base: float = 16.0
    theme_font_size_h1: float = 32.0
    theme_font_size_h2: float = 24.0
    theme_tile_bg: str = "#f8fafc"
    theme_tile_border: str = "#e2e8f0"
    theme_tile_opacity: float = 1.0
    theme_tile_border_opacity: float = 1.0
    sso_enabled: bool = False
    sso_provider: str = ""
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_authorize_url: str = ""
    sso_token_url: str = ""
    sso_userinfo_url: str = ""
    sso_redirect_url: str = ""
    sso_role_claim: str = "groups"
    sso_default_role: str = "user"
    sso_role_mappings_json: str = "{}"
    sso_auto_create_users: bool = True
    sso_sync_roles_on_login: bool = True
    ldap_enabled: bool = False
    ldap_server_uri: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_base_dn: str = ""
    ldap_user_filter: str = "(uid={username})"
    ldap_start_tls: bool = False
    ldap_insecure_skip_verify: bool = False
    ldap_timeout_seconds: int = 10
    ldap_auto_create_users: bool = True


class TeamQuota(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("team", "namespace", name="uq_teamquota_team_namespace"),)

    id: str = Field(primary_key=True, index=True)
    team: str = Field(index=True)
    namespace: str = Field(index=True)
    max_concurrent_labs: Optional[int] = None
    max_cpu_millicores: Optional[int] = None
    max_memory_mb: Optional[int] = None
    max_storage_gib: Optional[int] = None
    idle_timeout_minutes_cap: Optional[int] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Cluster(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    region: str = Field(default="local", index=True)
    compliance_tags_csv: str = ""
    capacity_weight: int = 100
    enabled: bool = True
    schedule_enabled: bool = True
    runtime_enabled: bool = False
    is_local: bool = False
    runtime_namespace: str = ""
    kubeconfig_secret_name: str = ""
    kubeconfig_secret_namespace: str = ""
    kubeconfig_secret_key: str = "kubeconfig"
    kubeconfig: str = ""
    notes: str = ""
    health_status: str = "unknown"
    health_message: str = ""
    last_heartbeat_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamPlacementPolicy(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("team", name="uq_teamplacementpolicy_team"),)

    id: str = Field(primary_key=True, index=True)
    team: str = Field(index=True)
    preferred_cluster_id: Optional[str] = Field(default=None, foreign_key="cluster.id")
    hard_pin_cluster: bool = False
    required_regions_csv: str = ""
    required_compliance_tags_csv: str = ""
    allowed_cluster_ids_csv: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ArtifactReplication(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint(
            "artifact_type",
            "artifact_id",
            "target_cluster_id",
            name="uq_artifactreplication_artifact_target",
        ),
    )

    id: str = Field(primary_key=True, index=True)
    tenant: str = Field(default="global", index=True)
    artifact_type: str = Field(index=True)
    artifact_id: str = Field(index=True)
    source_cluster_id: str = Field(foreign_key="cluster.id")
    target_cluster_id: str = Field(foreign_key="cluster.id")
    status: str = Field(default="queued", index=True)
    detail: str = ""
    requested_by: str = ""
    last_attempt_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AdminAuditEvent(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    actor: str = Field(default="unknown", index=True)
    tenant: str = Field(default="global", index=True)
    action: str = Field(index=True)
    target_type: str = Field(index=True)
    target_id: str = Field(default="")
    detail: str = Field(default="")
    created_at: datetime = Field(default_factory=utc_now, index=True)
