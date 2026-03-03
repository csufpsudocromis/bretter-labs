from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    is_admin: bool = False


class UserPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    password: Optional[str] = None
    is_admin: Optional[bool] = None


class UserOut(BaseModel):
    username: str
    is_admin: bool
    force_password_change: bool


class ImageMeta(BaseModel):
    id: str
    name: str
    checksum: str
    size_bytes: int
    created_at: datetime


class ImageCreateResponse(ImageMeta):
    filename: str


class ImageUploadTaskStatus(BaseModel):
    task_id: str
    status: str
    original_filename: str
    filename: str
    size_bytes: int
    detail: str = ""
    error: str | None = None
    image_id: str | None = None
    created_at: datetime
    updated_at: datetime


class ContainerImageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    image_ref: str = Field(..., min_length=1, max_length=255)


class ContainerImageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    image_ref: Optional[str] = Field(default=None, min_length=1, max_length=255)


class ContainerImageMeta(BaseModel):
    id: str
    name: str
    image_ref: str
    created_at: datetime


class VMTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    os_type: str = Field(default="windows", pattern="^(windows|linux)$")
    image_id: str
    cpu_cores: int = Field(..., ge=1, le=32)
    ram_mb: int = Field(..., ge=512, le=262144)
    auto_delete_minutes: int = Field(..., ge=1, le=30)
    idle_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    preclone_pool_size: int = Field(default=0, ge=0, le=50)
    preclone_pool_max: int = Field(default=0, ge=0, le=50)
    enabled: bool = False
    network_mode: str = Field(
        default="bridge", pattern="^(bridge|host|none|unrestricted|isolated)$"
    )


class VMTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    os_type: Optional[str] = Field(default=None, pattern="^(windows|linux)$")
    image_id: Optional[str] = None
    cpu_cores: Optional[int] = Field(default=None, ge=1, le=32)
    ram_mb: Optional[int] = Field(default=None, ge=512, le=262144)
    auto_delete_minutes: Optional[int] = Field(default=None, ge=1, le=30)
    idle_timeout_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    preclone_pool_size: Optional[int] = Field(default=None, ge=0, le=50)
    preclone_pool_max: Optional[int] = Field(default=None, ge=0, le=50)
    enabled: Optional[bool] = None
    network_mode: Optional[str] = Field(
        default=None, pattern="^(bridge|host|none|unrestricted|isolated)$"
    )


class VMTemplate(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    os_type: str
    image_id: str
    cpu_cores: int
    ram_mb: int
    auto_delete_minutes: int
    idle_timeout_minutes: int
    preclone_pool_size: int = 0
    preclone_pool_max: int = 0
    enabled: bool
    network_mode: str = "bridge"
    created_at: datetime


class ContainerTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = ""
    container_image_id: str
    cpu_millicores: int = Field(default=500, ge=50, le=16000)
    memory_mb: int = Field(default=512, ge=64, le=131072)
    container_port: int = Field(default=80, ge=1, le=65535)
    command: Optional[str] = Field(default=None, max_length=2000)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auto_delete_minutes: int = Field(default=60, ge=1, le=1440)
    enabled: bool = False


class ContainerTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    container_image_id: Optional[str] = None
    cpu_millicores: Optional[int] = Field(default=None, ge=50, le=16000)
    memory_mb: Optional[int] = Field(default=None, ge=64, le=131072)
    container_port: Optional[int] = Field(default=None, ge=1, le=65535)
    command: Optional[str] = Field(default=None, max_length=2000)
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    auto_delete_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    enabled: Optional[bool] = None


class ContainerTemplate(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    container_image_id: str
    cpu_millicores: int
    memory_mb: int
    container_port: int = 80
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auto_delete_minutes: int = 60
    enabled: bool = False
    created_at: datetime


class TemplateToggle(BaseModel):
    enabled: bool


class RuntimeHealthCheck(BaseModel):
    key: str
    status: Literal["ok", "warn", "error", "info"]
    title: str
    detail: str


class RuntimeDriftItem(BaseModel):
    field_key: str
    env_var: str
    pod_name: str
    configured_value: str
    pod_value: str
    detail: str


class RuntimeSettingsRead(BaseModel):
    storage_root: str
    kube_namespace: str
    kube_image_pvc: str
    kube_runtime_class: str
    kube_vm_storage_class: str
    runner_image: str
    image_pull_secret: str
    kube_node_selector_key: str
    kube_node_selector_value: str
    kube_use_kvm: bool
    kube_spice_embed_configmap: str
    kube_node_external_host: str
    sources: dict[str, str] = Field(default_factory=dict)
    apply_behavior: dict[str, str] = Field(default_factory=dict)
    env_names: dict[str, str] = Field(default_factory=dict)
    health_status: Literal["healthy", "warning", "critical", "unknown"] = "unknown"
    health_checks: list[RuntimeHealthCheck] = Field(default_factory=list)
    drift: list[RuntimeDriftItem] = Field(default_factory=list)
    backend_pod_count: int = 0


class StorageValidationCheck(BaseModel):
    key: str
    status: Literal["ok", "warn", "error", "info"]
    title: str
    detail: str


class StorageSettingsRead(BaseModel):
    storage_root: str
    kube_namespace: str
    kube_image_pvc: str
    kube_vm_storage_class: str
    sources: dict[str, str] = Field(default_factory=dict)
    checks: list[StorageValidationCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class StorageSettingsUpdate(BaseModel):
    storage_root: str | None = None
    kube_image_pvc: str | None = None
    kube_vm_storage_class: str | None = None
    clear_overrides: bool = False


class AlertManagerAlert(BaseModel):
    name: str
    state: str
    severity: str = ""
    summary: str = ""
    description: str = ""
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class ErrorLogView(BaseModel):
    source: str
    bytes: int
    truncated: bool
    total_lines: int = 0
    page: int = 1
    per_page: int = 50
    total_pages: int = 1
    has_prev: bool = False
    has_next: bool = False
    lines: list[str] = Field(default_factory=list)
    content: str


class AlertsAndErrorsView(BaseModel):
    fetched_at: datetime
    alertmanager_url: str
    alertmanager_error: str = ""
    alerts: list[AlertManagerAlert] = Field(default_factory=list)
    error_log: ErrorLogView


class ErrorLogClearResult(BaseModel):
    source: str
    cleared_pods: int = 0
    total_pods: int = 0
    failed_pods: list[str] = Field(default_factory=list)
    detail: str = ""


class SiteSettings(BaseModel):
    site_title: str
    site_tagline: str
    theme_bg_color: str
    theme_text_color: str
    theme_button_color: str
    theme_button_text_color: str
    theme_bg_image: str
    theme_bg_image_overlay_opacity: float = Field(default=0.0, ge=0.0, le=0.85)
    theme_contrast_body: float = Field(default=4.5, ge=1.0, le=21.0)
    theme_contrast_button: float = Field(default=4.5, ge=1.0, le=21.0)
    theme_contrast_tile: float = Field(default=4.5, ge=1.0, le=21.0)
    theme_contrast_tile_border: float = Field(default=1.5, ge=1.0, le=21.0)
    theme_font_family: str = "Inter, system-ui, -apple-system, sans-serif"
    theme_font_size_base: float = Field(default=16.0, ge=12.0, le=24.0)
    theme_font_size_h1: float = Field(default=32.0, ge=20.0, le=64.0)
    theme_font_size_h2: float = Field(default=24.0, ge=16.0, le=48.0)
    theme_tile_bg: str
    theme_tile_border: str
    theme_tile_opacity: float
    theme_tile_border_opacity: float


class SSOSettings(BaseModel):
    sso_enabled: bool
    sso_provider: str
    sso_client_id: str
    sso_client_secret: str
    sso_authorize_url: str
    sso_token_url: str
    sso_userinfo_url: str
    sso_redirect_url: str


class ConcurrencySettings(BaseModel):
    max_concurrent_vms: int = Field(..., ge=1, le=5000)
    per_user_vm_limit: int = Field(..., ge=1, le=100)


class IdleTimeoutSettings(BaseModel):
    idle_timeout_minutes: int = Field(..., ge=1, le=1440)


class VMInstance(BaseModel):
    id: str
    template_id: str
    owner: str
    status: Literal["pending", "running", "stopped", "completed", "failed", "unknown"]
    status_stage: Optional[str] = None
    status_detail: Optional[str] = None
    started_at: datetime
    last_active_at: datetime
    console_url: Optional[str] = None


class ContainerInstance(BaseModel):
    id: str
    template_id: str
    owner: str
    status: Literal["pending", "running", "stopped", "completed", "failed", "unknown"]
    status_stage: Optional[str] = None
    status_detail: Optional[str] = None
    pod_name: Optional[str] = None
    access_url: Optional[str] = None
    container_port: Optional[int] = None
    started_at: datetime
    last_active_at: datetime
