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


class VMTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    os_type: str = Field(default="windows", pattern="^(windows|linux)$")
    image_id: str
    cpu_cores: int = Field(..., ge=1, le=32)
    ram_mb: int = Field(..., ge=512, le=262144)
    auto_delete_minutes: int = Field(..., ge=1, le=30)
    idle_timeout_minutes: int = Field(default=30, ge=1, le=1440)
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
    enabled: bool
    network_mode: str = "bridge"
    created_at: datetime


class TemplateToggle(BaseModel):
    enabled: bool


class RuntimeSettingsRead(BaseModel):
    storage_root: str
    kube_namespace: str
    kube_image_pvc: str
    kube_runtime_class: str
    runner_image: str
    image_pull_secret: str
    kube_node_selector_key: str
    kube_node_selector_value: str
    kube_use_kvm: bool
    kube_spice_embed_configmap: str
    kube_node_external_host: str


class SiteSettings(BaseModel):
    site_title: str
    site_tagline: str
    theme_bg_color: str
    theme_text_color: str
    theme_button_color: str
    theme_button_text_color: str
    theme_bg_image: str
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
    started_at: datetime
    last_active_at: datetime
    console_url: Optional[str] = None
