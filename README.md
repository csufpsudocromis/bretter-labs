# Bretter Labs

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-required-326CE5.svg)](https://kubernetes.io/)

Bretter Labs is a Kubernetes-native virtual lab platform.
It lets admins upload VM images and publish templates, then lets users launch browser-accessible Windows/Linux labs with staged runtime feedback.

For deeper docs, operational playbooks, and full configuration matrices, use the project wiki:

- https://github.com/csufpsudocromis/bretter-labs/wiki

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start (Kubernetes)](#quick-start-kubernetes)
- [Common Setup Options](#common-setup-options)
- [Usage](#usage)
- [Local Development](#local-development)
- [Operations and Troubleshooting](#operations-and-troubleshooting)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Features

### Admin capabilities

- Upload and normalize VM disk images
- Template management (CPU, RAM, idle timeout, pool tuning, network mode)
- User administration
- Resource/health pages for runtime and storage visibility
- Appearance settings (theme, typography, contrast targets)

### User capabilities

- One-click lab start from enabled templates
- Browser-based SPICE console access
- Runtime stage feedback (for example: Pending, Building, Starting, Running)
- Idle handling and auto-stop/cleanup behavior

### Platform capabilities

- FastAPI backend + React (Vite) frontend
- VM runner pods scheduled on Kubernetes nodes
- Clone-based VM disks via StorageClass/PVC workflows
- CDI-backed image upload/finalization flow
- Postgres-backed control-plane state
- Optional monitoring/alerting and proactive cleanup automation

## Architecture

Core components:

- `frontend-vite/`: React UI for user/admin workflows
- `backend/`: FastAPI API, auth, settings, template/image/instance orchestration
- `runner/`: QEMU + SPICE VM runtime container image
- `deploy/`: Kubernetes manifests rendered by setup
- `scripts/setup.sh`: cluster bootstrap and deployment automation

High-level flow:

1. Admin uploads image and creates a template.
2. User starts a lab from template.
3. Backend creates per-instance VM resources in Kubernetes.
4. User connects via browser console URL exposed from the runner service.

## Quick Start (Kubernetes)

### Prerequisites

- Access to a Kubernetes cluster
- `kubectl` configured for that cluster
- Linux host with Bash (setup script is designed for Ubuntu/Debian)

### Deploy

```bash
git clone https://github.com/csufpsudocromis/bretter-labs.git
cd bretter-labs
./scripts/setup.sh
```

The setup script handles:

- environment validation
- manifest rendering
- app deployment
- optional image build/push/import flows
- optional monitoring/cleanup integrations

### Access after setup

- UI: `https://<NODE_EXTERNAL_HOST>:30073`
- API: `https://<NODE_EXTERNAL_HOST>:30080`

Default admin bootstrap account:

- username: `admin`
- password: `admin`

Password change is required on first login.

## Common Setup Options

Use environment variables to tune installation behavior.
For the full list and recommended profiles, see the wiki.

| Variable | Default | Purpose |
| --- | --- | --- |
| `NAMESPACE` | `labs` | Target namespace |
| `CONTROL_NODE` | auto | Node for control-plane pinned workloads |
| `NODE_EXTERNAL_HOST` | auto | External host used in UI/API/console URLs |
| `PUBLIC_SCHEME` | `https` | Public URL scheme (`https` or `http`) |
| `TLS_ENABLED` | `1` | Enable TLS secret/bootstrap behavior |
| `VM_STORAGE_CLASS` | auto with Longhorn tune | StorageClass used for VM clone disks |
| `RUNNER_NODE_SELECTOR_VALUE` | empty | Pin runner pods to a specific node |
| `APPLY_GOLDEN_HOSTPATH` | `1` | Use hostPath golden-images PVC path |
| `APPLY_GOLDEN_PVC` | `0` | Use `deploy/golden-pvc.yaml` (shared/RWX scenarios) |
| `LOAD_LOCAL_IMAGES` | `1` | Build local images and import into cluster runtime |
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

## Usage

### Admin workflow

1. Log in as admin.
2. Upload image(s).
3. Create template(s) from uploaded images.
4. Enable templates for users.
5. Monitor runtime/storage/alerts from admin pages.

### User workflow

1. Log in as a standard user.
2. Start a lab from the available template list.
3. Watch stage status update until running.
4. Connect to the lab console.
5. Stop/delete when finished (or allow configured cleanup automation).

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

Set `VITE_API_BASE` if you need to target a non-default backend URL.

## Operations and Troubleshooting

### Quick health checks

```bash
kubectl -n labs get pods
kubectl -n labs get deploy bretter-backend bretter-frontend
kubectl -n labs logs deploy/bretter-backend --tail=200
```

### Common issues

- VM stays in Pending: cluster is waiting on schedulable CPU/memory/storage.
- Image upload problems: verify storage capacity and CDI/uploadproxy health.
- TLS/browser warnings: expected with self-signed certs unless custom certs are installed.

For runbooks and detailed diagnostics, use the wiki:

- https://github.com/csufpsudocromis/bretter-labs/wiki

## Project Structure

```text
backend/         FastAPI app, models, routes, migrations
frontend-vite/   React app (Vite)
runner/          VM runner image (QEMU/SPICE)
scripts/         Setup/bootstrap automation
deploy/          Kubernetes manifests/templates
docs/            Supplemental project docs
images/          README screenshots/assets
```

## Contributing

1. Create a feature branch.
2. Make focused changes with tests/validation.
3. Open a pull request with clear scope and rollout notes.

If your change affects setup variables or operational behavior, also update the wiki page for that area.

## License

MIT. See [LICENSE](LICENSE).
