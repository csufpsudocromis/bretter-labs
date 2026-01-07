# Bretter Labs – Architecture

## Overview
Bretter Labs is a FastAPI + React (Vite) platform that provisions per-user lab VMs as Kubernetes pods and exposes them through a browser-based SPICE console.

## Core components
- **Frontend (`frontend-vite`)**: React UI for login, admin, and user workflows. Loads site appearance/SSO settings, starts labs, opens console URLs, and drives idle prompts in the main UI.
- **Backend (`backend/src`)**: FastAPI API for users, templates, images, and VM instances. Uses SQLModel + SQLite for persistence and runs a background reaper to clean up idle labs.
- **VM runner (`runner`)**: Debian-based container running QEMU + SPICE with websockify and the spice-html5 assets. Each VM pod runs this image.
- **Kubernetes control plane**: Backend uses the Kubernetes Python client to create pods, per-VM NodePort services, and NetworkPolicies. Helper pods + `kubectl` are used to copy/validate images on the PVC.
- **Storage**: `golden-images` PVC stores uploaded VM images; per-VM ephemeral disks live on `emptyDir`. Backend state uses a PVC-backed SQLite DB.

## Request flow
1. Admin uploads an image; backend writes it into the image PVC and stores metadata/checksum in SQLite.
2. Admin creates a template with CPU/RAM, network mode, auto-delete, and idle timeout.
3. User starts a template; backend creates a VM pod (init container copies the image to `emptyDir`), then creates a NodePort service to expose SPICE/websockify.
4. Backend returns a console URL built from `BLABS_KUBE_NODE_EXTERNAL_HOST` and the NodePort; the UI opens it in a new tab.

## Idle handling and cleanup
- User UI polls `/user/pods`, which refreshes `last_active_at` for running labs.
- Both the main UI and the console tab show idle prompts; timeouts trigger VM deletion via the API.
- A backend reaper loop runs on startup and deletes instances whose `last_active_at` exceeds the template or global timeout.
- Stopped/completed instances are auto-deleted after `auto_delete_minutes`.

## Networking and isolation
- Default `bridge` mode applies NetworkPolicies allowing DNS + 80/443 egress and SPICE ingress.
- `isolated`/`none` block egress; `host`/`unrestricted` skip NetworkPolicy and may use host networking.

## Auth and settings
- Local username/password auth with bcrypt hashing; bearer tokens are issued from `/auth/login`.
- Default admin is created on first startup and forced to change password.
- Runtime settings are configured via `BLABS_*` env vars (namespace, runtime class, KVM, node selector, image pull secret, node external host, etc.).
- Appearance and SSO settings are stored in the config table; SSO is currently config-only (no backend SSO flow yet).

## Deployment notes
- `scripts/setup.sh` installs prerequisites on Ubuntu/Debian, updates the SPICE embed ConfigMap, and applies `deploy/app.yaml`.
- The manifest creates NodePort services for backend/frontend and grants the backend RBAC to manage pods/services/network policies.
