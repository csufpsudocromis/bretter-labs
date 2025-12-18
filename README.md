# Bretter Labs

FastAPI + React app for managing Windows/Linux lab VMs on Kubernetes. Admins upload images, define templates, enable/disable labs, manage users, and view cluster resources. Users start labs from templates and connect via SPICE in the browser.

## Prerequisites
- Git
- Python 3.11+ with venv/pip (backend)
- Node.js 18+ with npm (frontend)
- kubectl (to talk to the target cluster)

Ubuntu/Debian install example:
```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip kubectl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

## Run the backend
```bash
cd /home/cbeis/bretter-labs
source .venv/bin/activate  # create if needed: python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
# adjust env as needed (examples below)
BLABS_RUNNER_IMAGE=ttl.sh/bretter-runner-1764617357:24h \
BLABS_KUBE_NAMESPACE=labs \
BLABS_KUBE_IMAGE_PVC=lab-images \
BLABS_KUBE_NODE_EXTERNAL_HOST=10.68.49.229 \
uvicorn backend.src.main:app --host 0.0.0.0 --port 8000
```
- Health: `GET /health`
- Default admin: `admin` / `admin`
- Config lives in `backend/src/config.py` and `BLABS_*` env vars (namespace, PVC, runner image, node selector, etc.).
- SQLite DB: `backend/data/app.db`; images live on the `lab-images` PVC mounted at `/mnt/lab-images` (the backend now writes/deletes directly on that PVC so runner pods can see updates).

## Run the frontend (React/Vite)
```bash
cd frontend-vite
cp .env.example .env  # set VITE_API_BASE if backend is remote
npm install
npm run dev -- --host --port 5173
```
Open `http://<host>:5173`, log in with your credentials.

### UI highlights
- **User**: tiles for templates (name/description/specs), start lab, view running labs with status, connect (opens SPICE embed), delete.
- **Admin**:
  - Templates: create with description/image/CPU/RAM, enable/disable, edit, delete.
  - Images: upload/manage VM images (.vhd/.qcow/.qcow2/.vdi; uploads and deletes sync to the PVC automatically).
  - Users: create, edit (username/password/role), delete.
  - Pods: list/stop/delete running pods.
  - Resources: view cluster capacity/allocatable vs requested CPU/memory.

## Golden image storage (RWX)
- Create a ReadWriteMany PVC for images (works on any node): `kubectl apply -f deploy/golden-pvc.yaml` and set `storageClassName` to your RWX class (NFS/CSI/Longhorn/etc.).
- Point the backend to that PVC via `BLABS_KUBE_IMAGE_PVC=golden-images`. All uploads go straight to the PVC; Admin → Images lists files from it; renames delete/rename on the PVC.
- When a user launches a VM, the image is copied into the pod’s emptyDir so the golden image stays unchanged.

## Kubernetes expectations
- Namespace defaults to `labs`; PVC `lab-images` must exist.
- KVM passthrough is supported (`/dev/kvm` hostPath, privileged runner) when `BLABS_KUBE_USE_KVM=true`.
- Runner image set via `BLABS_RUNNER_IMAGE`; update from temporary `ttl.sh` tag to a stable public image for long-term use.
