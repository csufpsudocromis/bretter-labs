from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    username: str = Field(primary_key=True, index=True)
    password_hash: str
    is_admin: bool = False
    force_password_change: bool = False


class Token(SQLModel, table=True):
    token: str = Field(primary_key=True, index=True)
    username: str = Field(foreign_key="user.username")
    issued_at: datetime = Field(default_factory=datetime.utcnow)


class Image(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    filename: str
    checksum: str
    size_bytes: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Template(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    name: str
    description: str = ""
    os_type: str = "windows"
    image_id: str = Field(foreign_key="image.id")
    cpu_cores: int
    ram_mb: int
    auto_delete_minutes: int = 30
    enabled: bool = False
    network_mode: str = "bridge"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Instance(SQLModel, table=True):
    id: str = Field(primary_key=True, index=True)
    template_id: str = Field(foreign_key="template.id")
    owner: str = Field(foreign_key="user.username")
    status: str = "pending"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    console_url: Optional[str] = None


class Config(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    max_concurrent_vms: int = 50
    per_user_vm_limit: int = 2
    idle_timeout_minutes: int = 30
    site_title: str = "Bretter Labs"
    site_tagline: str = "Run Virtual Labs and Software"
    theme_bg_color: str = "#f5f5f5"
    theme_text_color: str = "#111111"
    theme_button_color: str = "#2563eb"
    theme_button_text_color: str = "#ffffff"
    theme_bg_image: str = ""
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
