# Production Architecture

Last reviewed: March 9, 2026.

Use this page as the production target state for Bretter Labs deployments.

## Scope and decisions this page must answer

- TLS termination points
- Ingress/gateway pattern
- Tenant isolation model
- Secrets storage model
- Database + backup strategy
- Security boundaries between UI/API/runner
- Kubernetes layout (namespaces, service accounts, network policies)

## Recommended topology

Reference architecture doc:

- [Production Architecture Reference](../production-architecture.md)

Use that document as the canonical design artifact and keep this page aligned to it.

## Security assumptions (pick one per environment)

1. Internal-only trusted network
   - Platform reachable only from campus/private networks
   - Strict source CIDR controls
2. Zero-trust/public exposure
   - Public ingress with WAF/rate-limiting
   - OIDC required
   - Optional mTLS on sensitive paths

Document the chosen model in your environment runbook.

## Recommended Kubernetes layout

Namespaces:

- `labs` (frontend/backend/postgres/runtime)
- `monitoring` (Prometheus/Grafana/Alertmanager)
- `external-secrets` (if enabled)
- `ingress-nginx` (or your ingress namespace)
- operator namespaces (`longhorn-system`, `cdi`, etc.)

Service accounts:

- `bretter-backend` for API/control-plane actions
- dedicated operator/job SAs where needed
- no default SA in production workloads

Network policy posture:

- namespace default deny (ingress + egress)
- explicit allow only for required app and control-plane flows

## Data and secret handling

Secrets:

- Use External Secrets + corporate store (Vault or equivalent).
- Keep credentials out of plaintext manifests.

Database:

- Postgres primary with durable storage.
- Alembic migration on startup/deploy.
- Scheduled backups + restore validation.

## Related production docs

- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Security and Auth](Security-and-Auth)
- [Operations Runbook](Operations-Runbook)
