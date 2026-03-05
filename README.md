# Bretter Labs

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-required-326CE5.svg)](https://kubernetes.io/)

Bretter Labs is a Kubernetes-native virtual lab platform for browser-based VM and container labs.

Admins manage images, templates, users, runtime/storage settings, and platform health.  
Users launch labs with staged status feedback and connect in the browser.

## Table of Contents

- [What You Get](#what-you-get)
- [Supported VM Image Types](#supported-vm-image-types)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Key Setup Variables](#key-setup-variables)
- [Admin and User Workflows](#admin-and-user-workflows)
- [Local Development](#local-development)
- [Operations](#operations)
- [Documentation and Wiki](#documentation-and-wiki)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## What You Get

### Admin features

- VM image upload and normalization
- VM templates and container templates
- Container image registry management
- Runtime, storage, appearance, and alert/error settings
- Resource, pod, and health visibility in admin pages

### User features

- Launch labs from enabled templates
- Staged runtime feedback (for example: Pending, Building, Starting, Running)
- Browser connect for VM and container labs
- Idle timeout behavior and cleanup automation
- Single active lab enforcement per user

### Platform features

- FastAPI backend + React (Vite) frontend
- Kubernetes-native VM/container lifecycle orchestration
- Clone-based VM storage workflows
- CDI direct upload/finalization support
- Postgres-ready DB stack with Alembic migrations
- Monitoring hooks, alert ingestion, and capped error logs

## Supported VM Image Types

Allowed upload formats:

- `.vhd`
- `.vhdx`
- `.qcow`
- `.qcow2`
- `.vdi`

QCOW uploads are normalized to raw during finalization.

## Architecture

Core components:

- `frontend-vite/`: UI for admin and user workflows
- `backend/`: API, auth/session, orchestration, migrations
- `runner/`: VM runtime image (QEMU/SPICE)
- `deploy/`: Kubernetes manifests used by setup
- `scripts/setup.sh`: bootstrap, deploy, and tuning automation

High-level flow:

1. Admin uploads image(s) and publishes template(s).
2. User starts a VM or container lab.
3. Backend provisions per-instance Kubernetes resources.
4. User connects from browser through the platform connect flow.

## Quick Start

### Prerequisites

- Kubernetes cluster access
- `kubectl` configured for target cluster
- Linux host with Bash (Ubuntu/Debian recommended)

### Deploy

```bash
git clone https://github.com/csufpsudocromis/bretter-labs.git
cd bretter-labs
./scripts/setup.sh
```

### Access

- UI: `https://<NODE_EXTERNAL_HOST>:30073`
- API: `https://<NODE_EXTERNAL_HOST>:30080`

Default admin account:

- username: `admin`
- password: `admin`

Password change is required on first login.

## Key Setup Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `NAMESPACE` | `labs` | Target namespace |
| `CONTROL_NODE` | auto | Preferred control node for pinned workloads |
| `NODE_EXTERNAL_HOST` | auto | Public host/IP used in generated URLs |
| `PUBLIC_SCHEME` | `https` | Public URL scheme |
| `TLS_ENABLED` | `1` | Enable TLS secret/bootstrap behavior |
| `VM_STORAGE_CLASS` | auto | StorageClass for VM clone disks |
| `APPLY_GOLDEN_HOSTPATH` | `1` | HostPath-backed golden image PVC |
| `APPLY_GOLDEN_PVC` | `0` | Use `deploy/golden-pvc.yaml` instead |
| `LOAD_LOCAL_IMAGES` | `1` | Build/import local images into cluster runtime |
| `PUSH_IMAGES` | `0` | Build and push images to registry |
| `CREATE_PULL_SECRET` | `0` | Create/update `ghcr-creds` pull secret |

Example:

```bash
NAMESPACE=labs \
NODE_EXTERNAL_HOST=10.68.49.250 \
CONTROL_NODE=cbekube1 \
VM_STORAGE_CLASS=longhorn-r1 \
./scripts/setup.sh
```

## Admin and User Workflows

### Admin

1. Log in as admin.
2. Upload VM images and register container images.
3. Create and enable VM/container templates.
4. Configure runtime/storage/appearance as needed.
5. Monitor resources, alerts, and logs from admin pages.

### User

1. Log in.
2. Start a lab from available templates.
3. Wait for staged status to reach running.
4. Connect in browser.
5. Delete lab when done.

## Local Development

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.src.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend-vite
npm install
npm run dev -- --host --port 5173
```

Set `VITE_API_BASE` to target a non-default API endpoint.

## Operations

Quick health checks:

```bash
kubectl -n labs get pods
kubectl -n labs get deploy bretter-backend bretter-frontend
kubectl -n labs logs deploy/bretter-backend --tail=200
```

Common issues:

- Pending labs: cluster waiting on available CPU/memory/storage.
- Upload finalize failures: check PVC/node disk usage and CDI/upload path health.
- TLS warnings: expected with self-signed certificates unless custom certs are installed.

## Documentation and Wiki

- GitHub wiki: https://github.com/csufpsudocromis/bretter-labs/wiki
- Repository wiki source pages: `docs/wiki/`
- Architecture deep dive: `docs/architecture.md`

## Project Structure

```text
backend/         FastAPI app, models, routes, migrations, services
frontend-vite/   React app (Vite)
runner/          VM runner image (QEMU/SPICE)
scripts/         Setup/bootstrap automation
deploy/          Kubernetes manifests/templates
docs/            Architecture and wiki source docs
images/          README/wiki assets
```

## Contributing

1. Create a focused branch.
2. Add/update tests for behavior changes.
3. Validate rollout/health for runtime-impacting changes.
4. Update README and `docs/wiki/` when behavior or config changes.

## License

MIT. See [LICENSE](LICENSE).
