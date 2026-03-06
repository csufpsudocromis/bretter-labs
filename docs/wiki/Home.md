# Bretter Labs Wiki

This folder is the repository source for wiki pages.

If you maintain pages in the GitHub Wiki UI, copy/paste from:

- https://github.com/csufpsudocromis/bretter-labs/wiki

Last reviewed: March 6, 2026.

## Start here

- [Architecture](../architecture.md)
- [Operations Runbook](Operations-Runbook.md)
- [VM Image Formats](VM-Image-Formats.md)
- [Container Labs](Container-Labs.md)
- [Security and Auth](Security-and-Auth.md)
- [Setup and Configuration](Setup-and-Configuration.md)

## Current platform snapshot

- Kubernetes-native VM and container lab orchestration
- Cookie-based auth and short-lived connect token flow
- One active lab per user (VM or container) enforced server-side
- Unified user lab UX: staged statuses, connect gating, idle timeout prompts
- Admin pages for resources, pods, runtime/storage validation, and alerts/errors
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
