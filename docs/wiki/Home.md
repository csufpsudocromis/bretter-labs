# Bretter Labs Wiki

This folder is the repository source for wiki pages.

GitHub wiki:

- https://github.com/csufpsudocromis/bretter-labs/wiki

Last reviewed: March 9, 2026.

## Audience paths

- Admin setup: [Setup and Configuration](Setup-and-Configuration)
- Operations/SRE: [Operations Runbook](Operations-Runbook)
- Troubleshooting: [Error Catalog](Error-Catalog) + [Operations Runbook](Operations-Runbook)
- Developer: [Connect Flow Deep Dive](Connect-Flow-Deep-Dive) + [Template Best Practices](Template-Best-Practices)
- Security: [Security and Auth](Security-and-Auth) + [LDAP Authentication](LDAP-Authentication) + [Hardened Deployment Guide](Hardened-Deployment-Guide)

## Core pages

- [Architecture](../architecture.md)
- [Production Architecture](Production-Architecture)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Operations Runbook](Operations-Runbook)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Error Catalog](Error-Catalog)
- [Network Modes Reference](Network-Modes-Reference)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Template Best Practices](Template-Best-Practices)
- [VM Image Formats](VM-Image-Formats)
- [Container Labs](Container-Labs)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Security and Auth](Security-and-Auth)
- [LDAP Authentication](LDAP-Authentication)
- [Pentest Plan and Checklist](Pentest-Plan-and-Checklist)
- [Setup and Configuration](Setup-and-Configuration)

## Current platform snapshot

- Kubernetes-native VM and container lab orchestration
- Cookie-based auth, short-lived connect grant/session cookies, and enforced session TTL
- RBAC roles and permissions for admin/API paths
- Optional OIDC SSO login flow (authorization code + PKCE)
- Optional LDAP auth fallback configured in `/admin/settings/ldap`
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
