# Bretter Labs Wiki

This folder is the repository source for wiki pages.

GitHub wiki:

- https://github.com/csufpsudocromis/bretter-labs/wiki

Last reviewed: March 23, 2026.

## Audience paths

- Admin setup: [Setup and Configuration](Setup-and-Configuration)
- Operations/SRE: [Operations Runbook](Operations-Runbook)
- Restore/DR ops: [Restore Drill and Backup SOP](Restore-Drill-and-Backup-SOP)
- Secrets/Ops: [Secret Operations Runbook](Secret-Operations-Runbook)
- Alert routing: [Alert Routing and Receiver Defaults](Alert-Routing-and-Receiver-Defaults)
- Troubleshooting: [Error Catalog](Error-Catalog) + [Operations Runbook](Operations-Runbook)
- Developer: [Connect Flow Deep Dive](Connect-Flow-Deep-Dive) + [Template Best Practices](Template-Best-Practices)
- Console/RDP operations: [Console Providers and RDP Operations](Console-Providers-and-RDP-Operations)
- Security: [Security and Auth](Security-and-Auth) + [LDAP Authentication](LDAP-Authentication) + [Hardened Deployment Guide](Hardened-Deployment-Guide)
- GitHub/release ops: [GitHub Release and Packages Operations](GitHub-Release-and-Packages-Operations)
- Platform engineering: [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)
- Operator incidents: [Operator Incident Runbook](Operator-Incident-Runbook)
- CRD evolution: [Operator CRD Versioning Plan](Operator-CRD-Versioning-Plan)
- API contract checks: [API Contract and Drift Guardrails](API-Contract-and-Drift-Guardrails)
- Tenant isolation: [Tenant Isolation and Namespaces](Tenant-Isolation-and-Namespaces)
- Multi-cluster operations: [Multi-Cluster Operations](Multi-Cluster-Operations)

## Core pages

- [Architecture](../architecture.md)
- [Production Architecture](Production-Architecture)
- [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)
- [Operator Incident Runbook](Operator-Incident-Runbook)
- [Operator CRD Versioning Plan](Operator-CRD-Versioning-Plan)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Production Readiness Checklist](Production-Readiness-Checklist)
- [Upgrade and Rollback](Upgrade-and-Rollback)
- [Operations Runbook](Operations-Runbook)
- [Alert Routing and Receiver Defaults](Alert-Routing-and-Receiver-Defaults)
- [Restore Drill and Backup SOP](Restore-Drill-and-Backup-SOP)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Alert Routing and Receiver Defaults](Alert-Routing-and-Receiver-Defaults)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Tenant Isolation and Namespaces](Tenant-Isolation-and-Namespaces)
- [Multi-Cluster Operations](Multi-Cluster-Operations)
- [Error Catalog](Error-Catalog)
- [Network Modes Reference](Network-Modes-Reference)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
- [Connect Flow Deep Dive](Connect-Flow-Deep-Dive)
- [Console Providers and RDP Operations](Console-Providers-and-RDP-Operations)
- [Template Best Practices](Template-Best-Practices)
- [VM Image Formats](VM-Image-Formats)
- [Container Labs](Container-Labs)
- [Scaling and Quotas](Scaling-and-Quotas)
- [Security and Auth](Security-and-Auth)
- [LDAP Authentication](LDAP-Authentication)
- [Community and Roadmap](Community-and-Roadmap)
- [GitHub Release and Packages Operations](GitHub-Release-and-Packages-Operations)
- [Pentest Plan and Checklist](Pentest-Plan-and-Checklist)
- [API Contract and Drift Guardrails](API-Contract-and-Drift-Guardrails)
- [Setup and Configuration](Setup-and-Configuration)

## Production checks at a glance

Use this sequence for every production rollout:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
NAMESPACE=labs ./scripts/deploy_preflight.sh
./scripts/ci_guardrails.sh
PRODUCTION_PROFILE=1 SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

Reference pages:

- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)

## Current platform snapshot

- Kubernetes-native VM and container lab orchestration
- Cookie-based auth, short-lived connect grant/session cookies, and enforced session TTL
- RBAC roles and permissions for admin/API paths
- Optional OIDC SSO login flow (authorization code + PKCE)
- Optional LDAP auth fallback configured in `/admin/settings/ldap`
- One active lab per user enforced server-side (VM + container)
- Namespace-based scaling and quota controls in `/admin/scaling-quotas`
- Backend/frontend autoscaling controls via HPA (`*_HPA_MIN/MAX_REPLICAS`, CPU utilization targets) plus `UVICORN_WORKERS`
- Default ingress NetworkPolicies with explicit app allow rules
- Error log cap/rotation at 10MB with paging (50 entries/page)
- Reusable production baseline values with site-overlay template (`values-production-site.template.yaml`)
- Deploy preflight gate checks merged values, secret wiring, and per-node image pullability
- Automatic production go-live proof in `postdeploy` when `PRODUCTION_PROFILE=1`
- Post-deploy admin API smoke validation job (`bretter-post-deploy-admin-api-smoke`)
- Recurring GHCR access and user-flow SLO probe CronJobs with Prometheus alert rules
- Secret-backed postdeploy auth checks and RDP connect-latency probe credentials
- Optional encrypted off-cluster backup replication CronJob (`bretter-postgres-backup-replication`)
- Release-branch smoke gates for post-deploy synthetic, restore drill, and Playwright Guacamole RDP browser checks
- Main-branch userflow smoke gate (`deploy-userflow-smoke.yml`) on push/PR to `main`
- Merge-time image promotion and digest auto-pin workflow on `main` (`publish-and-pin-images.yml`)
- Dedicated LabImageImport controller with leader-election and metrics endpoints
- OpenAPI snapshot + frontend API type drift checks in CI guardrails
- Explicit PostgreSQL Alembic migration gate in CI (`scripts/check_alembic_postgres.sh`)
- Tenant isolation audit gate for values/RBAC/network policy posture (`scripts/audit_tenant_isolation.sh`)
- Tenant namespace bootstrap script for per-team quota/policy scaffolding
- Multi-cluster runtime dispatch with placement policy explain/telemetry and replication queue processing

## Architecture diagram

```mermaid
flowchart LR
  U["User Browser"]
  FE["Frontend React/Vite"]
  BE["Backend FastAPI"]
  DB["Postgres + Alembic"]
  K8S["Kubernetes API"]
  VM["VM Runner Pods"]
  CT["Container Lab Pods"]
  ST["PVC and StorageClass"]

  U --> FE
  FE --> BE
  BE --> DB
  BE --> K8S
  K8S --> VM
  K8S --> CT
  K8S --> ST
```
