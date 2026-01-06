from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    admin_default_username: str = "admin"
    admin_default_password: str = "admin"
    max_concurrent_vms: int = 50
    per_user_vm_limit: int = 2
    idle_timeout_minutes: int = 30
    storage_root: str = "/home/cbeis/golden-images"
    database_path: str = "backend/data/app.db"
    kube_namespace: str = "labs"
    kube_image_pvc: str = "golden-images"
    kube_runtime_class: str = ""  # set to your RuntimeClass name if needed
    reaper_interval_seconds: int = 60
    runner_image: str = "ghcr.io/bretter-labs/win-vm-runner:latest"
    image_pull_secret: str = ""  # optional: name of imagePullSecret in the namespace
    kube_node_selector_key: str = "kubernetes.io/hostname"
    kube_node_selector_value: str = ""  # set to pin pods to a node (e.g., kub1)
    kube_use_kvm: bool = True  # set False if /dev/kvm is unavailable
    kube_spice_embed_configmap: str = "spice-embed"  # ConfigMap with spice-embed.html to slim the console UI
    kube_node_external_host: str = "10.68.48.105"
    image_pull_secret: str = "ghcr-creds"  # optional: name of imagePullSecret in the namespace

    model_config = {"env_prefix": "BLABS_"}


settings = Settings()
