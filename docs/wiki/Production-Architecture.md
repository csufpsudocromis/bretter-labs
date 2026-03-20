# Production Architecture

Last reviewed: March 20, 2026.

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
- Keep `deploy/helm/values-production.yaml` non-secret (`SECRETS_ENCRYPTION_KEY` remains empty).
- Inject runtime encryption key via `RUNTIME_SECRETS_SECRET_NAME` / `RUNTIME_SECRETS_ENCRYPTION_KEY_KEY`.
- Inject cosign verification key via `CONTAINER_SIGNATURE_KEY_SECRET_NAME` mounted at `CONTAINER_SIGNATURE_KEY_REF`.

Production gates:

- Run strict production validation before rollout:
  - `python3 scripts/validate_production_profile.py --strict -f deploy/helm/values-production.yaml -f deploy/helm/values-prod-site.yaml`
- Run go-live proof after rollout:
  - `NAMESPACE=labs ./scripts/production_go_live_proof.sh`
- Or run deploy + postdeploy with automatic proof:
  - `PRODUCTION_PROFILE=1 SETUP_PHASES=deploy,postdeploy ./scripts/setup.sh`

Database:

- Postgres primary with durable storage.
- Alembic migration on startup/deploy.
- Scheduled backups + restore validation.

## Related production docs

- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Security and Auth](Security-and-Auth)
- [Operations Runbook](Operations-Runbook)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)
