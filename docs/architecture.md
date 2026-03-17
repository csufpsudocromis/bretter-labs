# Bretter Labs - Architecture

## Overview

Bretter Labs is a FastAPI + React (Vite) platform that provisions per-user VM and container labs on Kubernetes and exposes browser-based connect flows.

## Core components

- **Frontend (`frontend-vite`)**  
  User/admin UI for login, template/image management, launch/connect flows, runtime status, and platform settings.

- **Backend (`backend/src`)**  
  FastAPI service that handles auth, RBAC-style admin/user API routes, template/image lifecycle, launch orchestration, idle reaping, and health/alert surfaces.

- **Runner (`runner`)**  
  VM runtime image (QEMU + websockify) used by VM lab pods with template-selected SPICE or VNC console mode.

- **Kubernetes orchestration layer**  
  Backend creates and manages workloads/services/network policies and uses storage-aware workflows for image uploads, conversions, cloning, and warm-pool behaviors.

- **Storage and database**  
  Golden image PVC + clone storage classes for lab disks. Database is SQLModel with Alembic migrations and supports Postgres-backed deployments.

## High-level request flow

1. Admin uploads VM image or registers container image.
2. Admin publishes VM/container template.
3. User starts a lab; backend enforces single active lab limit and acquires launch lock.
4. Backend provisions per-instance Kubernetes resources.
5. UI shows staged state (`queued/pending/building/starting/running`).
6. User connects through secure connect flow.

## VM pipeline

1. Uploaded VM disk is validated and normalized as needed.
2. Template defines resources, firmware/machine defaults, network mode, console provider, and idle timeout.
3. Start request creates instance resources and service endpoints.
4. User opens browser connect tab for interactive VM session.

## Container pipeline

1. Admin registers container image and creates container template.
2. Template includes port/connect checks, runtime/network options, and idle timeout.
3. Start request creates isolated per-instance container workload.
4. Connect flow returns browser URL only once readiness criteria are met.

## Auth/session model

- Username/password login issues secure HttpOnly session cookies.
- Connect flow uses short-lived one-time grant/session token cookies (not URL bearer tokens).
- Configurable cookie `secure` and `samesite` behavior via `BLABS_*` settings.

## Runtime cleanup and lifecycle controls

- Idle timers are enforced by backend reaper logic with UI prompt integration.
- User and connect-tab activity can refresh `last_active_at`.
- Stopped/failed/completed resources are eligible for cleanup automation.
- Launch queue/backoff behavior handles transient resource pressure.

## Networking model

- Runtime networking mode is template-driven (for VM and container templates).
- Policies and exposure behavior are applied per instance.
- Public access uses `PUBLIC_SCHEME` and configured external host.

## Observability and operations

- Admin pages expose runtime/storage/pod state and alert/error views.
- Error log storage is capped to prevent unbounded growth.
- Alertmanager integration is supported for surfaced active alerts.

## Deployment model

- `scripts/setup.sh` renders manifests and applies cluster/runtime/storage defaults.
- Deployments include backend/frontend and supporting Kubernetes resources.
- Cluster-facing endpoints:
  - UI: `https://<host>:30073`
  - API (frontend proxy): `https://<host>:30073/api`
  - Direct backend NodePort is disabled by default and can be enabled explicitly for dev workflows.
