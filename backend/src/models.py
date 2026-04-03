from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    role: Optional[str] = Field(default=None, min_length=1, max_length=64)
    team: str = Field(default="default", min_length=1, max_length=64)
    namespace_scopes: list[str] = Field(default_factory=list)
    is_admin: bool = False


class UserPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    password: Optional[str] = None
    role: Optional[str] = Field(default=None, min_length=1, max_length=64)
    team: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace_scopes: Optional[list[str]] = None
    is_admin: Optional[bool] = None


class UserOut(BaseModel):
    username: str
    role: str
    team: str
    namespace_scopes: list[str] = Field(default_factory=list)
    is_admin: bool
    force_password_change: bool
    permissions: list[str] = Field(default_factory=list)
    can_access_admin: bool = False


class RoleCatalogOut(BaseModel):
    role: str
    label: str
    description: str
    permissions: list[str] = Field(default_factory=list)
    editable: bool = False
    deletable: bool = False


class RoleDefinitionCreate(BaseModel):
    role: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=256)
    permissions: list[str] = Field(default_factory=list)


class RoleDefinitionUpdate(BaseModel):
    label: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=256)
    permissions: Optional[list[str]] = None


class RoleManagementCatalogOut(BaseModel):
    roles: list[RoleCatalogOut] = Field(default_factory=list)
    permission_catalog: list[str] = Field(default_factory=list)


class TeamQuotaCreate(BaseModel):
    team: str = Field(default="default", min_length=1, max_length=64)
    namespace: str = Field(..., min_length=1, max_length=63)
    max_concurrent_labs: Optional[int] = Field(default=None, ge=1, le=5000)
    max_cpu_millicores: Optional[int] = Field(default=None, ge=100, le=1_000_000)
    max_memory_mb: Optional[int] = Field(default=None, ge=128, le=8_388_608)
    max_storage_gib: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    idle_timeout_minutes_cap: Optional[int] = Field(default=None, ge=1, le=1440)
    enabled: bool = True


class TeamQuotaUpdate(BaseModel):
    team: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    max_concurrent_labs: Optional[int] = Field(default=None, ge=1, le=5000)
    max_cpu_millicores: Optional[int] = Field(default=None, ge=100, le=1_000_000)
    max_memory_mb: Optional[int] = Field(default=None, ge=128, le=8_388_608)
    max_storage_gib: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    idle_timeout_minutes_cap: Optional[int] = Field(default=None, ge=1, le=1440)
    enabled: Optional[bool] = None
    clear_max_concurrent_labs: bool = False
    clear_max_cpu_millicores: bool = False
    clear_max_memory_mb: bool = False
    clear_max_storage_gib: bool = False
    clear_idle_timeout_minutes_cap: bool = False


class TeamQuotaOut(BaseModel):
    id: str
    team: str
    namespace: str
    max_concurrent_labs: Optional[int] = None
    max_cpu_millicores: Optional[int] = None
    max_memory_mb: Optional[int] = None
    max_storage_gib: Optional[int] = None
    idle_timeout_minutes_cap: Optional[int] = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class ManagedNamespaceCreate(BaseModel):
    namespace: str = Field(..., min_length=1, max_length=63)
    team_label: str = Field(default="default", min_length=1, max_length=64)
    security_profile: Literal["restricted", "baseline", "privileged"] = "baseline"
    enforce_network_policies: bool = True
    max_pods: str = Field(default="200", min_length=1, max_length=32)
    max_services: str = Field(default="100", min_length=1, max_length=32)
    max_persistent_volume_claims: str = Field(default="200", min_length=1, max_length=32)
    requests_cpu: str = Field(default="8", min_length=1, max_length=32)
    limits_cpu: str = Field(default="16", min_length=1, max_length=32)
    requests_memory: str = Field(default="16Gi", min_length=1, max_length=32)
    limits_memory: str = Field(default="32Gi", min_length=1, max_length=32)
    requests_storage: str = Field(default="2Ti", min_length=1, max_length=32)
    limit_min_cpu: str = Field(default="50m", min_length=1, max_length=32)
    limit_min_memory: str = Field(default="64Mi", min_length=1, max_length=32)
    limit_default_request_cpu: str = Field(default="250m", min_length=1, max_length=32)
    limit_default_request_memory: str = Field(default="256Mi", min_length=1, max_length=32)
    limit_default_cpu: str = Field(default="2", min_length=1, max_length=32)
    limit_default_memory: str = Field(default="2Gi", min_length=1, max_length=32)
    limit_max_cpu: str = Field(default="8", min_length=1, max_length=32)
    limit_max_memory: str = Field(default="16Gi", min_length=1, max_length=32)
    idle_timeout_minutes_default: int = Field(default=30, ge=1, le=1440)
    vm_auto_delete_minutes_default: int = Field(default=60, ge=1, le=10080)
    container_auto_delete_minutes_default: int = Field(default=60, ge=1, le=10080)
    queue_max_pending: int = Field(default=25, ge=1, le=5000)
    upload_max_bytes: int = Field(default=60 * 1024 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024 * 1024)
    enabled: bool = True


class ManagedNamespaceUpdate(BaseModel):
    team_label: Optional[str] = Field(default=None, min_length=1, max_length=64)
    security_profile: Optional[Literal["restricted", "baseline", "privileged"]] = None
    enforce_network_policies: Optional[bool] = None
    max_pods: Optional[str] = Field(default=None, min_length=1, max_length=32)
    max_services: Optional[str] = Field(default=None, min_length=1, max_length=32)
    max_persistent_volume_claims: Optional[str] = Field(default=None, min_length=1, max_length=32)
    requests_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limits_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    requests_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limits_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    requests_storage: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_min_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_min_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_default_request_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_default_request_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_default_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_default_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_max_cpu: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit_max_memory: Optional[str] = Field(default=None, min_length=1, max_length=32)
    idle_timeout_minutes_default: Optional[int] = Field(default=None, ge=1, le=1440)
    vm_auto_delete_minutes_default: Optional[int] = Field(default=None, ge=1, le=10080)
    container_auto_delete_minutes_default: Optional[int] = Field(default=None, ge=1, le=10080)
    queue_max_pending: Optional[int] = Field(default=None, ge=1, le=5000)
    upload_max_bytes: Optional[int] = Field(default=None, ge=1, le=2 * 1024 * 1024 * 1024 * 1024)
    enabled: Optional[bool] = None


class ManagedNamespaceOut(BaseModel):
    id: str
    namespace: str
    team_label: str
    security_profile: Literal["restricted", "baseline", "privileged"]
    enforce_network_policies: bool
    max_pods: str
    max_services: str
    max_persistent_volume_claims: str
    requests_cpu: str
    limits_cpu: str
    requests_memory: str
    limits_memory: str
    requests_storage: str
    limit_min_cpu: str
    limit_min_memory: str
    limit_default_request_cpu: str
    limit_default_request_memory: str
    limit_default_cpu: str
    limit_default_memory: str
    limit_max_cpu: str
    limit_max_memory: str
    idle_timeout_minutes_default: int = 30
    vm_auto_delete_minutes_default: int = 60
    container_auto_delete_minutes_default: int = 60
    queue_max_pending: int = 25
    upload_max_bytes: int = 60 * 1024 * 1024 * 1024
    enabled: bool
    present_in_cluster: bool = False
    active_vm_instances: int = 0
    active_container_instances: int = 0
    active_total_instances: int = 0
    last_reconciled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ManagedNamespaceCleanupStepOut(BaseModel):
    step: str
    status: Literal["ok", "warning", "error", "skipped"]
    detail: str = ""
    affected: int = 0


class ManagedNamespaceDecommissionOut(BaseModel):
    namespace: str
    delete_cluster_namespace: bool = True
    force_cleanup: bool = False
    blocked: bool = False
    deleted_database_records: int = 0
    deleted_cluster_resources: int = 0
    steps: list[ManagedNamespaceCleanupStepOut] = Field(default_factory=list)
    finished_at: datetime


class ManagedNamespaceObservabilityOut(BaseModel):
    namespace: str
    enabled: bool = True
    present_in_cluster: bool = False
    active_vm_instances: int = 0
    active_container_instances: int = 0
    queued_container_instances: int = 0
    failed_total_instances: int = 0
    running_total_instances: int = 0
    image_upload_tasks_pending: int = 0
    image_upload_tasks_failed: int = 0
    resource_quota_present: bool = False
    limit_range_present: bool = False
    network_policy_count: int = 0
    required_network_policies_missing: list[str] = Field(default_factory=list)
    slo_window_minutes: int = 60
    vm_launches_total: int = 0
    vm_launches_failed: int = 0
    vm_launch_failure_rate_pct: float = 0.0
    upload_finalizes_total: int = 0
    upload_finalizes_failed: int = 0
    upload_finalize_failure_rate_pct: float = 0.0
    queue_oldest_pending_seconds: int = 0
    error_budget_target_pct: float = 99.0
    error_budget_remaining_pct: float = 100.0
    quota_current_concurrent_labs: int = 0
    quota_max_concurrent_labs: Optional[int] = None
    quota_concurrent_usage_pct: float = 0.0
    quota_current_cpu_millicores: int = 0
    quota_max_cpu_millicores: Optional[int] = None
    quota_cpu_usage_pct: float = 0.0
    quota_current_memory_mb: int = 0
    quota_max_memory_mb: Optional[int] = None
    quota_memory_usage_pct: float = 0.0
    pending_pvc_count: int = 0
    image_import_oldest_pending_seconds: int = 0
    recent_failures_60m: int = 0
    alert_route_key: str = ""
    drift_count: int = 0
    drift_items: list[str] = Field(default_factory=list)
    last_reconciled_at: Optional[datetime] = None


class ImageMeta(BaseModel):
    id: str
    name: str
    filename: str | None = None
    tenant: str = "global"
    namespace: str = "labs"
    shared_catalog: bool = False
    cluster_id: str = "local"
    source_kind: str = "uploaded"
    installer_iso_id: str | None = None
    installer_iso_filename: str | None = None
    installer_os_type: str | None = None
    installer_disk_size_gib: int | None = None
    update_cpu_cores_default: int = 2
    update_ram_mb_default: int = 4096
    checksum: str
    size_bytes: int
    created_at: datetime


class ImageCreateResponse(ImageMeta):
    filename: str


class IsoImageMeta(BaseModel):
    id: str
    name: str
    filename: str
    tenant: str = "global"
    namespace: str = "labs"
    shared_catalog: bool = False
    checksum: str
    size_bytes: int
    created_at: datetime


class ImageUploadTaskStatus(BaseModel):
    task_id: str
    status: str
    stage: str = ""
    progress_percent: int | None = None
    original_filename: str
    filename: str
    namespace: str = "labs"
    size_bytes: int
    detail: str = ""
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    next_retry_at: datetime | None = None
    last_retry_error: str | None = None
    image_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminLaunchTaskOut(BaseModel):
    task_id: str
    kind: Literal["vm", "container"]
    status: str
    owner: str
    namespace: str = "labs"
    cluster_id: str = "local"
    template_id: str
    detail: str = ""
    elapsed_seconds: int = 0
    started_at: datetime
    last_active_at: datetime


class AdminOperationActionResult(BaseModel):
    ok: bool = True
    detail: str


class AdminAuditEventOut(BaseModel):
    id: str
    actor: str
    tenant: str = "global"
    namespace: str = "labs"
    action: str
    target_type: str
    target_id: str
    detail: str = ""
    created_at: datetime


class ContainerImageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    image_ref: str = Field(..., min_length=1, max_length=255)
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ContainerImageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    image_ref: Optional[str] = Field(default=None, min_length=1, max_length=255)
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ContainerImageMeta(BaseModel):
    id: str
    name: str
    image_ref: str
    tenant: str = "global"
    namespace: str = "labs"
    shared_catalog: bool = False
    cluster_id: str = "local"
    signature_warning: Optional[str] = None
    last_scan_at: Optional[datetime] = None
    last_scan_status: str = "never"
    last_scan_summary: str = ""
    created_at: datetime


class ContainerDependencyCheck(BaseModel):
    name: str = Field(default="", max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    timeout_seconds: int = Field(default=90, ge=5, le=600)


class VMTemplateCreate(BaseModel):
    name: str
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    enabled_namespaces: list[str] = Field(default_factory=list)
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = ""
    os_type: str = Field(default="windows", pattern="^(windows|linux)$")
    image_id: str
    cpu_cores: int = Field(..., ge=1, le=32)
    ram_mb: int = Field(..., ge=512, le=262144)
    auto_delete_minutes: int = Field(..., ge=1, le=10080)
    idle_timeout_minutes: int = Field(default=30, ge=1, le=10080)
    preclone_pool_size: int = Field(default=0, ge=0, le=50)
    preclone_pool_max: int = Field(default=0, ge=0, le=50)
    max_active_instances: int = Field(default=2, ge=0, le=200)
    enabled: bool = False
    network_mode: str = Field(default="bridge", pattern="^(bridge|host|none|unrestricted|isolated)$")
    console_provider: str = Field(default="spice", pattern="^(spice|guacamole|guacamole_rdp)$")
    rdp_default_username: Optional[str] = Field(default=None, max_length=128)
    rdp_default_password: Optional[str] = Field(default=None, max_length=256)


class VMTemplateUpdate(BaseModel):
    name: Optional[str] = None
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    enabled_namespaces: Optional[list[str]] = None
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None
    os_type: Optional[str] = Field(default=None, pattern="^(windows|linux)$")
    image_id: Optional[str] = None
    cpu_cores: Optional[int] = Field(default=None, ge=1, le=32)
    ram_mb: Optional[int] = Field(default=None, ge=512, le=262144)
    auto_delete_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    idle_timeout_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    preclone_pool_size: Optional[int] = Field(default=None, ge=0, le=50)
    preclone_pool_max: Optional[int] = Field(default=None, ge=0, le=50)
    max_active_instances: Optional[int] = Field(default=None, ge=0, le=200)
    enabled: Optional[bool] = None
    network_mode: Optional[str] = Field(default=None, pattern="^(bridge|host|none|unrestricted|isolated)$")
    console_provider: Optional[str] = Field(default=None, pattern="^(spice|guacamole|guacamole_rdp)$")
    rdp_default_username: Optional[str] = Field(default=None, max_length=128)
    rdp_default_password: Optional[str] = Field(default=None, max_length=256)


class VMTemplate(BaseModel):
    id: str
    name: str
    tenant: str = "global"
    namespace: str = "labs"
    shared_catalog: bool = False
    enabled_namespaces: list[str] = Field(default_factory=list)
    cluster_id: str = "local"
    description: Optional[str] = None
    os_type: str
    image_id: str
    cpu_cores: int
    ram_mb: int
    auto_delete_minutes: int
    idle_timeout_minutes: int
    preclone_pool_size: int = 0
    preclone_pool_max: int = 0
    max_active_instances: int = 2
    enabled: bool
    network_mode: str = "bridge"
    console_provider: str = "spice"
    rdp_default_username: Optional[str] = None
    rdp_default_password_configured: bool = False
    created_at: datetime


class VMTemplateLaunchPreflightCheck(BaseModel):
    key: str
    status: Literal["ok", "warn", "error"]
    detail: str


class VMTemplateLaunchPreflight(BaseModel):
    template_id: str
    namespace: str
    cluster_id: str
    ready: bool
    blocking_reason: Optional[str] = None
    checks: list[VMTemplateLaunchPreflightCheck] = Field(default_factory=list)


class ContainerTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    enabled_namespaces: list[str] = Field(default_factory=list)
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = ""
    container_image_id: str
    cpu_millicores: int = Field(default=500, ge=50, le=16000)
    memory_mb: int = Field(default=512, ge=64, le=131072)
    container_port: int = Field(default=80, ge=1, le=65535)
    healthcheck_protocol: str = Field(default="tcp", pattern="^(tcp|http)$")
    healthcheck_path: str = Field(default="/", min_length=1, max_length=256)
    readiness_http_status: int = Field(default=200, ge=100, le=599)
    readiness_success_path: Optional[str] = Field(default=None, max_length=256)
    startup_timeout_seconds: int = Field(default=300, ge=10, le=1800)
    dependency_checks: list[ContainerDependencyCheck] = Field(default_factory=list)
    expose_strategy: str = Field(default="nodeport", pattern="^(nodeport|ingress)$")
    network_mode: str = Field(default="bridge", pattern="^(bridge|none|isolated|unrestricted)$")
    run_as_non_root: bool = False
    read_only_root_filesystem: bool = False
    command: Optional[str] = Field(default=None, max_length=2000)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auto_delete_minutes: int = Field(default=60, ge=1, le=1440)
    idle_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    max_active_instances: int = Field(default=2, ge=0, le=200)
    enabled: bool = False


class ContainerTemplateUpdate(BaseModel):
    is_default: Optional[bool] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    tenant: Optional[str] = Field(default=None, min_length=1, max_length=64)
    namespace: Optional[str] = Field(default=None, min_length=1, max_length=63)
    shared_catalog: Optional[bool] = None
    enabled_namespaces: Optional[list[str]] = None
    cluster_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None
    container_image_id: Optional[str] = None
    cpu_millicores: Optional[int] = Field(default=None, ge=50, le=16000)
    memory_mb: Optional[int] = Field(default=None, ge=64, le=131072)
    container_port: Optional[int] = Field(default=None, ge=1, le=65535)
    healthcheck_protocol: Optional[str] = Field(default=None, pattern="^(tcp|http)$")
    healthcheck_path: Optional[str] = Field(default=None, min_length=1, max_length=256)
    readiness_http_status: Optional[int] = Field(default=None, ge=100, le=599)
    readiness_success_path: Optional[str] = Field(default=None, max_length=256)
    startup_timeout_seconds: Optional[int] = Field(default=None, ge=10, le=1800)
    dependency_checks: Optional[list[ContainerDependencyCheck]] = None
    expose_strategy: Optional[str] = Field(default=None, pattern="^(nodeport|ingress)$")
    network_mode: Optional[str] = Field(default=None, pattern="^(bridge|none|isolated|unrestricted)$")
    run_as_non_root: Optional[bool] = None
    read_only_root_filesystem: Optional[bool] = None
    command: Optional[str] = Field(default=None, max_length=2000)
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    auto_delete_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    idle_timeout_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    max_active_instances: Optional[int] = Field(default=None, ge=0, le=200)
    enabled: Optional[bool] = None


class ContainerTemplate(BaseModel):
    id: str
    template_key: str
    version: int = 1
    is_default: bool = True
    name: str
    tenant: str = "global"
    namespace: str = "labs"
    shared_catalog: bool = False
    enabled_namespaces: list[str] = Field(default_factory=list)
    cluster_id: str = "local"
    description: Optional[str] = None
    container_image_id: str
    cpu_millicores: int
    memory_mb: int
    container_port: int = 80
    healthcheck_protocol: str = "tcp"
    healthcheck_path: str = "/"
    readiness_http_status: int = 200
    readiness_success_path: Optional[str] = None
    startup_timeout_seconds: int = 300
    dependency_checks: list[ContainerDependencyCheck] = Field(default_factory=list)
    expose_strategy: str = "nodeport"
    network_mode: str = "bridge"
    run_as_non_root: bool = False
    read_only_root_filesystem: bool = False
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auto_delete_minutes: int = 60
    idle_timeout_minutes: int = 30
    max_active_instances: int = 2
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


class OrchestrationParityItem(BaseModel):
    instance_id: str
    db_status: str = ""
    crd_phase: str = ""


class OrchestrationParityReport(BaseModel):
    available: bool = False
    detail: str = ""
    mode: str = "db"
    db_instances: int = 0
    crd_instances: int = 0
    missing_in_crd: int = 0
    missing_in_db: int = 0
    status_mismatch: int = 0
    missing_in_crd_samples: list[str] = Field(default_factory=list)
    missing_in_db_samples: list[str] = Field(default_factory=list)
    status_mismatch_samples: list[OrchestrationParityItem] = Field(default_factory=list)


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


class RdpReadinessTelemetry(BaseModel):
    status: Literal["ok", "warning", "critical", "unknown"] = "unknown"
    total_instances: int = 0
    pending_or_starting_instances: int = 0
    stuck_instances: int = 0
    stuck_minutes_threshold: int = 12
    warning_threshold: int = 0
    critical_threshold: int = 2
    sample_instances: list[str] = Field(default_factory=list)
    slo_alert_active: bool = False
    slo_alert_names: list[str] = Field(default_factory=list)


class AlertsAndErrorsView(BaseModel):
    fetched_at: datetime
    alertmanager_url: str
    alertmanager_error: str = ""
    alerts: list[AlertManagerAlert] = Field(default_factory=list)
    rdp_readiness: RdpReadinessTelemetry = Field(default_factory=RdpReadinessTelemetry)
    error_log: ErrorLogView
    error_log_clear_supported: bool = True
    error_log_clear_reason: str = ""


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


class SiteBackgroundAsset(BaseModel):
    theme_bg_image: str
    filename: str
    size_bytes: int


class SSOSettings(BaseModel):
    sso_enabled: bool
    sso_provider: str
    sso_client_id: str
    sso_client_secret_configured: bool = False
    sso_authorize_url: str
    sso_token_url: str
    sso_userinfo_url: str
    sso_redirect_url: str
    sso_role_claim: str = Field(default="groups", min_length=1, max_length=128)
    sso_default_role: str = Field(default="user", min_length=1, max_length=64)
    sso_role_mappings: dict[str, str] = Field(default_factory=dict)
    sso_auto_create_users: bool = True
    sso_sync_roles_on_login: bool = True


class LDAPSettings(BaseModel):
    ldap_enabled: bool
    ldap_server_uri: str
    ldap_bind_dn: str
    ldap_bind_password_configured: bool = False
    ldap_user_base_dn: str
    ldap_user_filter: str = Field(default="(uid={username})", min_length=1, max_length=512)
    ldap_start_tls: bool = False
    ldap_insecure_skip_verify: bool = False
    ldap_timeout_seconds: int = Field(default=10, ge=3, le=60)
    ldap_auto_create_users: bool = True


class SSOSettingsUpdate(BaseModel):
    sso_enabled: bool
    sso_provider: str
    sso_client_id: str
    sso_client_secret: str | None = None
    sso_authorize_url: str
    sso_token_url: str
    sso_userinfo_url: str
    sso_redirect_url: str
    sso_role_claim: str = Field(default="groups", min_length=1, max_length=128)
    sso_default_role: str = Field(default="user", min_length=1, max_length=64)
    sso_role_mappings: dict[str, str] = Field(default_factory=dict)
    sso_auto_create_users: bool = True
    sso_sync_roles_on_login: bool = True


class LDAPSettingsUpdate(BaseModel):
    ldap_enabled: bool
    ldap_server_uri: str
    ldap_bind_dn: str
    ldap_bind_password: str | None = None
    ldap_user_base_dn: str
    ldap_user_filter: str = Field(default="(uid={username})", min_length=1, max_length=512)
    ldap_start_tls: bool = False
    ldap_insecure_skip_verify: bool = False
    ldap_timeout_seconds: int = Field(default=10, ge=3, le=60)
    ldap_auto_create_users: bool = True


class ConcurrencySettings(BaseModel):
    max_concurrent_vms: int = Field(..., ge=1, le=5000)
    per_user_vm_limit: int = Field(..., ge=1, le=100)


class IdleTimeoutSettings(BaseModel):
    idle_timeout_minutes: int = Field(..., ge=1, le=1440)


class VMInstance(BaseModel):
    id: str
    template_id: str
    owner: str
    tenant: str = "default"
    namespace: str = "labs"
    cluster_id: str = "local"
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
    tenant: str = "default"
    namespace: str = "labs"
    cluster_id: str = "local"
    status: Literal["queued", "pending", "running", "stopped", "completed", "failed", "unknown"]
    status_stage: Optional[str] = None
    status_detail: Optional[str] = None
    pod_name: Optional[str] = None
    access_url: Optional[str] = None
    container_port: Optional[int] = None
    queue_attempts: int = 0
    queue_not_before: Optional[datetime] = None
    queue_reason: Optional[str] = None
    launch_diagnostics: list[str] = Field(default_factory=list)
    started_at: datetime
    last_active_at: datetime


class ContainerConnectReadiness(BaseModel):
    ready: bool
    detail: str = ""
    checked_at: datetime


class ClusterConfigCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    region: str = Field(default="local", min_length=1, max_length=64)
    compliance_tags: list[str] = Field(default_factory=list)
    capacity_weight: int = Field(default=100, ge=1, le=10000)
    enabled: bool = True
    schedule_enabled: bool = True
    runtime_enabled: bool = False
    runtime_namespace: str | None = Field(default=None, min_length=1, max_length=63)
    kubeconfig_secret_name: str | None = Field(default=None, min_length=1, max_length=253)
    kubeconfig_secret_namespace: str | None = Field(default=None, min_length=1, max_length=63)
    kubeconfig_secret_key: str | None = Field(default=None, min_length=1, max_length=253)
    kubeconfig: str | None = None
    notes: str = Field(default="", max_length=2000)


class ClusterConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    compliance_tags: list[str] | None = None
    capacity_weight: int | None = Field(default=None, ge=1, le=10000)
    enabled: bool | None = None
    schedule_enabled: bool | None = None
    runtime_enabled: bool | None = None
    runtime_namespace: str | None = Field(default=None, max_length=63)
    kubeconfig_secret_name: str | None = Field(default=None, max_length=253)
    kubeconfig_secret_namespace: str | None = Field(default=None, max_length=63)
    kubeconfig_secret_key: str | None = Field(default=None, max_length=253)
    kubeconfig: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ClusterConfigOut(BaseModel):
    id: str
    name: str
    region: str
    compliance_tags: list[str] = Field(default_factory=list)
    capacity_weight: int
    enabled: bool
    schedule_enabled: bool
    runtime_enabled: bool
    is_local: bool
    runtime_namespace: str = ""
    kubeconfig_configured: bool = False
    kubeconfig_source: str = "none"
    kubeconfig_secret_name: str | None = None
    kubeconfig_secret_namespace: str | None = None
    kubeconfig_secret_key: str | None = None
    notes: str = ""
    health_status: str = "unknown"
    health_message: str = ""
    last_heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ClusterTelemetryOut(BaseModel):
    cluster_id: str
    health_status: str = "unknown"
    enabled: bool = True
    schedule_enabled: bool = True
    runtime_enabled: bool = False
    active_vm_instances: int = 0
    active_container_instances: int = 0
    queued_replications: int = 0
    syncing_replications: int = 0
    error_replications: int = 0
    runtime_client_ready: bool = False
    runtime_client_message: str = ""


class PlacementCandidateOut(BaseModel):
    cluster_id: str
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


class PlacementExplainOut(BaseModel):
    team: str = "default"
    workload_kind: str = "vm"
    template_cluster_id: str | None = None
    selected_cluster_id: str | None = None
    selected_reason: str | None = None
    error: str | None = None
    candidates: list[PlacementCandidateOut] = Field(default_factory=list)


class TeamPlacementPolicyUpdate(BaseModel):
    preferred_cluster_id: str | None = Field(default=None, min_length=1, max_length=64)
    hard_pin_cluster: bool = False
    required_regions: list[str] = Field(default_factory=list)
    required_compliance_tags: list[str] = Field(default_factory=list)
    allowed_cluster_ids: list[str] = Field(default_factory=list)


class TeamPlacementPolicyOut(BaseModel):
    id: str
    team: str
    preferred_cluster_id: str | None = None
    hard_pin_cluster: bool = False
    required_regions: list[str] = Field(default_factory=list)
    required_compliance_tags: list[str] = Field(default_factory=list)
    allowed_cluster_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ArtifactReplicationCreate(BaseModel):
    artifact_type: Literal["vm_image", "vm_template", "container_image", "container_template"]
    artifact_id: str = Field(..., min_length=1, max_length=128)
    target_cluster_ids: list[str] = Field(..., min_length=1)
    source_cluster_id: str | None = Field(default=None, min_length=1, max_length=64)
    tenant: str | None = Field(default=None, min_length=1, max_length=64)


class ArtifactReplicationUpdate(BaseModel):
    status: Literal["queued", "syncing", "ready", "error"]
    detail: str = Field(default="", max_length=2000)


class ArtifactReplicationOut(BaseModel):
    id: str
    tenant: str = "global"
    artifact_type: str
    artifact_id: str
    source_cluster_id: str
    target_cluster_id: str
    status: str
    detail: str = ""
    requested_by: str = ""
    last_attempt_at: datetime | None = None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
