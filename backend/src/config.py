from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    admin_default_username: str = "admin"
    admin_default_password: str = "admin"
    max_concurrent_vms: int = 50
    per_user_vm_limit: int = 2
    idle_timeout_minutes: int = 30
    storage_root: str = "/home/cbeis/golden-images"
    database_path: str = "backend/data/app.db"
    database_url: str = ""
    kube_namespace: str = "labs"
    kube_image_pvc: str = "golden-images"
    kube_runtime_class: str = ""  # set to your RuntimeClass name if needed
    reaper_interval_seconds: int = 60
    runner_image: str = "ghcr.io/csufpsudocromis/win-vm-runner:latest"
    image_pull_secret: str = "ghcr-creds"  # optional: name of imagePullSecret in the namespace
    kube_node_selector_key: str = "kubernetes.io/hostname"
    kube_node_selector_value: str = ""  # set to pin pods to a node (e.g., kub1)
    kube_vm_storage_class: str = ""  # set to enable per-VM cloned PVC disks (e.g., longhorn)
    kube_upload_use_cdi: bool = True
    kube_cdi_namespace: str = "cdi"
    kube_use_kvm: bool = True  # set False if /dev/kvm is unavailable
    kube_spice_embed_configmap: str = "spice-embed"  # ConfigMap with spice-embed.html to slim the console UI
    kube_node_external_host: str = "10.68.48.105"
    kube_tls_secret: str = "bretter-tls"
    # Default Windows to UEFI/q35; can be overridden per environment.
    windows_efi_enabled: bool = True
    windows_machine_type: str = "q35"
    windows_cpu_model: str = "host"
    # Linux lab images are often BIOS-era exports; default to BIOS + i440fx.
    linux_efi_enabled: bool = False
    linux_machine_type: str = "pc"
    linux_cpu_model: str = "host"
    vm_net_backend: str = "tap-nat"
    vm_vhost_net_enabled: bool = True
    vm_net_multiqueue_enabled: bool = True
    vm_qos_guaranteed: bool = True
    vm_memory_overhead_mb: int = 1024
    vm_runner_topology_spread_enabled: bool = True
    vm_runner_anti_affinity_enabled: bool = True
    warm_pool_autoscale_enabled: bool = True
    warm_pool_window_minutes: int = 15
    warm_pool_refill_minutes: int = 2
    warm_pool_safety_factor: float = 1.5
    cdi_direct_upload_enabled: bool = True
    cdi_upload_proxy_url: str = ""
    cdi_upload_source_filename: str = "disk.img"
    public_scheme: str = "https"
    cors_allow_http: bool = False
    alertmanager_api_url: str = "http://kube-prometheus-stack-alertmanager.monitoring.svc.cluster.local:9093/api/v2/alerts"
    alertmanager_timeout_seconds: int = 5
    error_log_file_path: str = "/data/error.log"
    error_log_max_bytes: int = 10 * 1024 * 1024

    model_config = {"env_prefix": "BLABS_"}


settings = Settings()
