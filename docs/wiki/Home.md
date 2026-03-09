# Bretter Labs Wiki

This folder is the repository source for wiki pages.

GitHub wiki:

- https://github.com/csufpsudocromis/bretter-labs/wiki

Last reviewed: March 9, 2026.

## Audience paths

- Admin setup: [Setup and Configuration](Setup-and-Configuration.md)
- Operations/SRE: [Operations Runbook](Operations-Runbook.md)
- Troubleshooting: [Operations Runbook](Operations-Runbook.md) + [VM Image Formats](VM-Image-Formats.md)
- Security: [Security and Auth](Security-and-Auth.md) + [Pentest Plan and Checklist](Pentest-Plan-and-Checklist.md)

## Core pages

- [Architecture](../architecture.md)
- [Operations Runbook](Operations-Runbook.md)
- [VM Image Formats](VM-Image-Formats.md)
- [Container Labs](Container-Labs.md)
- [Scaling and Quotas](Scaling-and-Quotas.md)
- [Security and Auth](Security-and-Auth.md)
- [Pentest Plan and Checklist](Pentest-Plan-and-Checklist.md)
- [Setup and Configuration](Setup-and-Configuration.md)

## Current platform snapshot

- Kubernetes-native VM and container lab orchestration
- Cookie-based auth, short-lived connect grant/session cookies, and enforced session TTL
- RBAC roles and permissions for admin/API paths
- Optional OIDC SSO login flow (authorization code + PKCE)
- One active lab per user enforced server-side (VM + container)
- Namespace-based scaling and quota controls in `/admin/scaling-quotas`
- Default ingress NetworkPolicies with explicit app allow rules
- Error log cap/rotation at 10MB with paging (50 entries/page)

## Architecture diagram

```mermaid
flowchart LR
  U[User Browser]
  FE[Frontend (React/Vite)]
  BE[Backend (FastAPI)]
  DB[(Postgres + Alembic)]
  K8S[Kubernetes API]
  VM[VM Runner Pods]
  CT[Container Lab Pods]
  ST[(PVC and StorageClass)]

  U --> FE
  FE --> BE
  BE --> DB
  BE --> K8S
  K8S --> VM
  K8S --> CT
  K8S --> ST
```
