# Bretter Labs – Architecture Draft

## Components
- API service (FastAPI, Python 3.12): admin/user management, image catalog, VM templates, VM lifecycle orchestration against Kubernetes; exposes REST endpoints (see `backend/src`).
- Persistence: SQLite via SQLModel (`backend/data/app.db`) for users, tokens, images, templates, instances, config.
- SPICE websocket console: user access to running VMs.
- Kubernetes cluster: pods run Windows VMs; images pulled from PVC-backed repository; network policies enforce pod isolation with internet egress only. RuntimeClass is set via config.
- PVC-backed image repository: stores uploaded `.vhd`/`.qcow` images; future migration to object storage optional.
- Auth: local username/password for testing; default admin `admin`/`admin` is forced to change password on first login; session tokens issued via `/auth/login` and sent as `Authorization: Bearer <token>`.
- Config row in DB stores `max_concurrent_vms`, `per_user_vm_limit`, and `idle_timeout_minutes`.
- Reaper loop: background task checks for idle instances older than `idle_timeout_minutes` and deletes their pods/records (interval configurable via `BLABS_REAPER_INTERVAL_SECONDS`).

## Networking
- Egress to internet allowed for lab pods.
- Inter-pod traffic blocked via Kubernetes NetworkPolicy.
- Admin/API endpoints exposed internally; user console served directly via NodePort + websockify.

## Data/Storage
- Image uploads land in `backend/data/images` (PVC mount point in production).
- VM disks are ephemeral; stopping or timing out destroys the pod and its disk.
- No persistence between sessions.

## Concurrency/Sessions
- Admin can set global `max_concurrent_vms` and `per_user_vm_limit`.
- Admin sets `idle_timeout_minutes`; idle pods are stopped/destroyed after inactivity (background worker still TODO).
- Ownership is enforced so users can only manage their own pods.

## Current backend skeleton
- SQLite-backed models via SQLModel (`backend/src/tables.py`, `backend/src/db.py`).
- Auth route (`backend/src/routes/auth.py`): issue tokens from username/password.
- Admin routes (`backend/src/routes/admin.py`, auth required): manage users, upload/list/delete images, create/enable/disable/delete templates, set concurrency/idle settings, list/stop/delete pods.
- User routes (`backend/src/routes/user.py`, auth required): list enabled templates, start/stop/delete own pods, list own pods.
- Health check at `/health`.
- Defaults loaded from `backend/src/config.py`; adjust via environment vars (`BLABS_*`).
- Frontend: React/Vite UI at `frontend-vite` (replace legacy static HTML) for login/admin/user flows.
- Kubernetes service (`backend/src/services/kubernetes.py`): uses Python client to create/stop/delete pods, apply egress-only NetworkPolicies, generate console URLs, and idle reaper hook.

## Running locally
```bash
cd /home/cbeis/bretter-labs
source .venv/bin/activate
uvicorn backend.src.main:app --reload
```
API served at `http://127.0.0.1:8000`.

## Next steps
- Implement real auth (password hashing, sessions/tokens, admin/user roles, first-login password change).
- Add persistence (database) for users/images/templates/instances; replace in-memory state.
- Wire Kubernetes client for pod lifecycle, image PVC handling, and network policies.
- Add idle-timeout worker to reap inactive pods.
- Integrate SPICE/websockify provisioning and per-pod connection URLs.
- Add frontend (admin + user consoles) consuming the API.
- Add CI/linting/tests and containerization for deployment.
