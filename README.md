# Bretter Labs

## Description
Bretter Labs is a FastAPI + React (Vite) app for managing Windows/Linux lab VMs on Kubernetes. Admins upload images, define templates, enable/disable labs, manage users, and view cluster resources. Users start labs from templates and connect via SPICE directly in the browser.

## Installation

### Prerequisites
- Python 3.11+ with venv/pip (backend)
- Node.js 18+ with npm (frontend)
- kubectl (to talk to the target cluster)
- docker or podman (optional, for building/pushing images)

### Local development
Backend:
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000
```

Frontend:
```bash
cd frontend-vite
npm install
npm run dev -- --host --port 5173
```

### Kubernetes deployment (current setup)
Recommended: use the setup script (auto-installs prerequisites on Ubuntu/Debian).
```bash
./scripts/setup.sh
```

Optional flags:
- `LOAD_LOCAL_IMAGES=1` (default) to build backend/frontend/runner images locally and import them into local containerd.
- `PRELOAD_RUNNER_ON_ALL_NODES=1` (default) to preload the VM runner image onto every non-control node via `kubectl debug` (set `0` to disable).
- `PUSH_IMAGES=1` to build/push images (requires GHCR credentials).
- `CREATE_PULL_SECRET=1` to create/update `ghcr-creds` for private image pulls.
- `BACKEND_IMAGE` / `FRONTEND_IMAGE` to override image tags.
- `RUNNER_IMAGE` to override the VM runner image passed to backend env.
- `NAMESPACE` to override the namespace (default `labs`).
- `KUBECONFIG` to point at a specific kubeconfig.
- `CONTROL_NODE` to pin Postgres + hostPath PV affinity to a specific node.
- `RUNNER_NODE_SELECTOR_VALUE` to pin VM runner pods to one node; leave empty (default) to allow scheduling on any eligible node.
- `VM_STORAGE_CLASS` to enable clone-based per-VM disks from source image PVCs. If unset and Longhorn tuning is enabled, setup defaults this to `longhorn-r1`.
- `LONGHORN_TUNE=1` (default) to apply phase-2 Longhorn defaults when Longhorn is installed.
- `LONGHORN_VM_STORAGE_CLASS` to set the VM clone StorageClass name created/used by setup (default `longhorn-r1`).
- `LONGHORN_VM_REPLICA_COUNT` to set replica count for `LONGHORN_VM_STORAGE_CLASS` (default `1`).
- `LONGHORN_DEFAULT_REPLICA_COUNT` to set Longhorn's default replica count (default `2`).
- `LONGHORN_RESERVED_PERCENT` to reserve per-node Longhorn disk capacity (default `10`).
- `LONGHORN_MIN_AVAILABLE_PERCENT` to set Longhorn minimal available capacity threshold (default `5`).
- `LONGHORN_OVERPROVISION_PERCENT` to set Longhorn overprovisioning percentage (default `200`).
- `LONGHORN_DEFAULT_DATA_PATH` to move Longhorn data to a dedicated fast disk mount (for example `/mnt/longhorn-fast`).
- `ENABLE_AUTOCLEANUP=1` (default) to install/update a `bretter-cleanup` CronJob in the app namespace.
- `AUTOCLEANUP_SCHEDULE` to control cleanup CronJob cadence (default `*/15 * * * *`).
- `AUTOCLEANUP_HELPER_MAX_AGE_MINUTES` to cull stale `image-sync-*` helper pods (default `30`).
- `AUTOCLEANUP_FINISHED_MAX_AGE_MINUTES` to cull old `Failed`/`Succeeded` pods (default `60`).
- `AUTOCLEANUP_STALE_UPLOAD_MAX_MINUTES` to remove stale completed direct-upload DataVolumes/PVCs (default `180`).
- `AUTOCLEANUP_RESTART_ALERT_COUNT` to log restart alerts when a pod crosses this count (default `3`).
- `AUTOCLEANUP_NODEFS_WARN_PCT` / `AUTOCLEANUP_NODEFS_CRITICAL_PCT` / `AUTOCLEANUP_NODEFS_EMERGENCY_PCT` for nodefs alert levels (defaults `70/85/95`).
- `AUTOCLEANUP_PVC_WARN_PCT` / `AUTOCLEANUP_PVC_CRITICAL_PCT` / `AUTOCLEANUP_PVC_EMERGENCY_PCT` for PVC-path alert levels (defaults `70/85/95`).
- `NODE_EXTERNAL_HOST` to set backend's advertised NodePort host (defaults to control node ExternalIP/InternalIP).
- `PUBLIC_SCHEME` to set external URL scheme for API/console links (`https` default, set `http` only for non-TLS environments).
- `WINDOWS_MACHINE_TYPE` / `WINDOWS_EFI_ENABLED` / `WINDOWS_CPU_MODEL` to control Windows VM firmware/machine defaults (defaults: `q35`/`true`/`host`).
- `LINUX_MACHINE_TYPE` / `LINUX_EFI_ENABLED` / `LINUX_CPU_MODEL` to control Linux VM firmware/machine defaults (defaults: `pc`/`false`/`host`).
- `VM_NET_BACKEND` to choose VM networking backend (`tap-nat` default, `user` for legacy qemu slirp).
- `CPU_MANAGER_STATIC=1` to enable kubelet `cpuManagerPolicy: static` on all nodes via `kubectl debug` (optional, requires kubelet restart).
- `INSTALL_CDI=1` and `CDI_VERSION` to auto-install CDI when missing (defaults: enabled, `v1.61.0`).
- `CDI_NAMESPACE`, `CDI_UPLOAD_NODEPORT`, `CDI_UPLOAD_PROXY_URL` to control CDI uploadproxy exposure/URL used by browser direct uploads.
- `ENABLE_MONITORING=1` (default) to install/update `kube-prometheus-stack` (Prometheus + Alertmanager + Grafana).
- `MONITORING_NAMESPACE` / `MONITORING_RELEASE_NAME` to control where the monitoring stack is installed.
- `MONITORING_CHART_VERSION` to pin a specific kube-prometheus-stack chart version (default empty = latest).
- `MONITORING_RESTART_ALERT_COUNT` to alert on pod restart bursts in 15 minutes (default `3`).
- `MONITORING_DV_STALE_MINUTES` to alert when `img-upload-*` upload PVCs stay active too long (default `60`).
- `MONITORING_WARM_POOL_MIN_READY` to alert when ready warm-pool PVCs drop below this count (default `1`).
- `HELM_VERSION` to control helm install version if helm is missing (default `v3.15.4`).
- `BLABS_WARM_POOL_AUTOSCALE_ENABLED`, `BLABS_WARM_POOL_WINDOW_MINUTES`, `BLABS_WARM_POOL_REFILL_MINUTES`, `BLABS_WARM_POOL_SAFETY_FACTOR` to tune warm pool autoscaling behavior.
- `TLS_ENABLED=1` (default) to ensure a TLS secret exists for backend/frontend/runner.
- `TLS_SECRET_NAME` to set the TLS secret name (default `bretter-tls`).
- `TLS_CERT_FILE` and `TLS_KEY_FILE` to use your own certificate/key when creating the TLS secret.
- `BACKEND_DATA_HOSTPATH` to override backend app-data hostPath (sqlite fallback/cache; default `/var/lib/bretter-labs/backend-data`).
- `POSTGRES_DATA_HOSTPATH`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` to configure the in-cluster Postgres backing store.
- `GOLDEN_IMAGES_HOSTPATH` to override golden image hostPath (default `/var/lib/bretter-labs/golden-images`).
- `SETUP_WARN_FREE_GIB` to set setup-time low-space warning threshold (default `40` GiB free).
- `SETUP_MIN_FREE_GIB` to set setup-time low-space hard-fail threshold (default `25` GiB free).
- `APPLY_GOLDEN_HOSTPATH=1` (default) to create `golden-images` hostPath PV/PVC on control node.
- `APPLY_GOLDEN_PVC=1` to apply `deploy/golden-pvc.yaml` (use this for RWX storage classes).

The script now renders manifests dynamically for namespace/control-node/IP/image values, and applies control-plane tolerations for control-node scheduling.
It also creates a placeholder `ghcr-creds` secret by default so fresh clusters do not fail on a missing imagePullSecret.
It also runs storage preflight checks and can install a cleanup CronJob to remove stale helper pods/orphan VM services.

If you use prebuilt public images, you can skip local builds with `LOAD_LOCAL_IMAGES=0`. If your registry is private, use `CREATE_PULL_SECRET=1`.

Manual build/push (edit tags as needed):
```bash
podman build -t ghcr.io/csufpsudocromis/bretter-backend:latest -f backend/Dockerfile .
podman push ghcr.io/csufpsudocromis/bretter-backend:latest
podman build -t ghcr.io/csufpsudocromis/bretter-frontend:latest -f frontend-vite/Dockerfile .
podman push ghcr.io/csufpsudocromis/bretter-frontend:latest
podman build -t ghcr.io/csufpsudocromis/win-vm-runner:latest -f runner/Dockerfile runner
podman push ghcr.io/csufpsudocromis/win-vm-runner:latest
```

After build/push, deploy with setup script so placeholders are rendered:
```bash
PUSH_IMAGES=1 CREATE_PULL_SECRET=1 ./scripts/setup.sh
```

Storage and runtime notes:
- `golden-images` PVC stores VM images. By default setup uses `deploy/golden-hostpath.yaml`.
- For shared RWX storage, use `APPLY_GOLDEN_HOSTPATH=0 APPLY_GOLDEN_PVC=1` and set the storage class in `deploy/golden-pvc.yaml`.
- Multi-node runner scheduling requires shared storage for `golden-images` (RWX). A single-node hostPath PV keeps VM pods effectively tied to that node.
- Backend uses in-cluster Postgres (`bretter-postgres`) with hostPath persistence on `POSTGRES_DATA_HOSTPATH`.
- Backend schema migrations are versioned with Alembic and run automatically on startup (`upgrade head`), including legacy baseline stamping for pre-Alembic installs.
- Backend deployment runs with 2 replicas, hard pod anti-affinity, topology spread, and a PodDisruptionBudget (`minAvailable: 1`) so API remains available through a single-node worker failure.
- Frontend deployment runs with 2 replicas, hard pod anti-affinity, topology spread, and a PodDisruptionBudget (`minAvailable: 1`) so UI remains available through a single-node worker failure.
- Backend `/data` now uses `emptyDir` because Postgres is the source of truth; this avoids RWO PVC contention when scaling backend replicas.
- Runner image `ghcr.io/csufpsudocromis/win-vm-runner:latest` is preloaded to worker nodes by setup when `LOAD_LOCAL_IMAGES=1`.
- If `LOAD_LOCAL_IMAGES=0`, ensure the runner image is pullable from your registry or preloaded on each node.
- `VM_STORAGE_CLASS` is required for VM launch. Uploaded/imported images get a source PVC and all VM launches use per-instance cloned PVC disks (no init-container file copy path).
- Uploaded/imported images are normalized automatically (`.qcow`/`.qcow2` -> `.raw`, `.vhd`/`.vhdx`/`.vdi` -> `.qcow2`) for more reliable VM boot behavior.
- Setup installs CDI by default (if missing), wires uploadproxy NodePort, and image uploads use browser direct CDI upload (uploadproxy/DataVolume) with async finalize/import jobs.
- If CDI direct upload is not available, the UI falls back to legacy backend multipart upload.
- Templates include `preclone_pool_size` (min) and `preclone_pool_max` (max) so warm pre-cloned disks auto-scale with recent launch demand while staying within bounds.
- Runtime defaults use UEFI+q35 for Windows and BIOS+i440fx with `virtio` disk bus for Linux; override with `BLABS_WINDOWS_*` / `BLABS_LINUX_*` env vars if needed.
- Runner networking defaults to `tap-nat` with virtio-net multiqueue and optional `vhost-net` acceleration for higher throughput/lower latency.
- VM pods use Guaranteed QoS by default (memory requests = limits, with configurable overhead via `BLABS_VM_MEMORY_OVERHEAD_MB`).
- Runner pods now include startup/readiness/liveness probes, preferred anti-affinity, and topology-spread constraints across nodes.
- With Longhorn installed, setup can auto-apply phase-2 defaults and create a VM clone class (`longhorn-r1`) for fresh installs.
- Cleanup automation now adds nodefs/PVC pressure alerts (70/85/95) and tightens cleanup thresholds before DiskPressure hits.
- Monitoring stack installs kube-prometheus-stack and applies Bretter-specific alerts for stale upload DataVolumes, warm-pool depletion, pod restart bursts, PVC/nodefs usage (70/85/95), and DiskPressure.
- Admin Alerts and Errors uses a capped 10MB backend error log that continuously drops oldest entries (never blocks logging), paginates 50 errors per page, and supports one-click log clear.
- Admin Resources now includes risk/headroom scoring, pending-pod blockers, top resource consumers, and Longhorn health; live usage requires metrics-server.
- Admin Storage Options now supports persisted overrides (storage root/image PVC/VM clone storage class), env-default reset, and live readiness checks (path free space, namespace/PVC/StorageClass, clone compatibility, CDI).
- Setup installs metrics-server by default (`ENABLE_METRICS_SERVER=1`) so `/admin/resources` can show live node/pod usage.

## Usage
- UI: NodePort `30073` (e.g. `https://<node-external-host>:30073`).
- API: NodePort `30080` (e.g. `https://<node-external-host>:30080`).
- Default admin: `admin` / `admin` (forced change on first login).

TLS note:
- `PUBLIC_SCHEME=https` makes frontend/backend URLs and console links use HTTPS/WSS.
- Setup enables TLS on NodePorts by mounting `TLS_SECRET_NAME` into backend/frontend/runner pods.
- If `TLS_CERT_FILE` and `TLS_KEY_FILE` are not provided, setup generates a self-signed cert for `NODE_EXTERNAL_HOST`.

Admin workflow:
- Upload images and create templates (CPU/RAM, idle timeout, enable/disable).
- Manage users, pods, and cluster resources.

User workflow:
- Start a lab from a template.
- Connect via browser SPICE.
- Idle prompts appear in the UI and console; if ignored, labs are auto-stopped/removed.

## License
MIT License. See `LICENSE`.

## Screenshots
![Templates grid](images/thumbnail1.png)
![Template details](images/thumbnail2.png)
![Pod list](images/thumbnail3.png)
![Resources](images/thumbnail4.png)
![VM console](images/thumbnail5.png)
![Session timeout](images/thumbnail6.png)
