# Hardened Deployment Guide

Last reviewed: March 16, 2026.

Use this as a production hardening checklist for Bretter Labs.

## 1) Restrict CORS

Use enterprise CORS mode with an explicit allowlist.

```bash
BLABS_CORS_ENTERPRISE_PROFILE=1
BLABS_CORS_ALLOWED_ORIGINS="https://labs.example.edu,https://<UI_HOST>:30073"
BLABS_CORS_ALLOWED_METHODS="GET,POST,PUT,PATCH,DELETE,OPTIONS"
BLABS_CORS_ALLOWED_HEADERS="Accept,Content-Type,Authorization"
```

Rules:

- Include the exact frontend origin(s).
- Do not set `BLABS_CORS_ALLOWED_ORIGIN_REGEX` in enterprise mode.
- Do not use wildcard methods/headers in enterprise mode.
- Keep CORS changes versioned in deployment config.

## 2) Enforce secure cookie/session settings

```bash
BLABS_AUTH_COOKIE_SECURE=1
BLABS_CONNECT_COOKIE_SECURE=1
BLABS_AUTH_COOKIE_TTL_SECONDS=86400
BLABS_CONNECT_GRANT_TTL_SECONDS=120
BLABS_CONNECT_SESSION_TTL_SECONDS=3600
```

Rules:

- Always use HTTPS in production.
- Keep connect grant TTL short.
- Set session TTL according to policy and re-login requirements.

## 3) Service account RBAC (least privilege)

Recommended:

- Backend uses dedicated SA (`bretter-backend`).
- Avoid default service account for app pods.
- Remove permissions that mutate secrets unless required.
- Scope Role/RoleBinding to namespace-level operations.

Quick check:

```bash
kubectl -n labs get sa
kubectl -n labs get role,rolebinding
kubectl -n labs auth can-i --as=system:serviceaccount:labs:bretter-backend create secrets
```

## 4) Pod Security posture and securityContext

Apply baseline defaults to frontend/backend pods:

```yaml
podSecurityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
```

Runner pods may have different requirements; keep that exception explicit and documented.

## 5) NetworkPolicy defaults and allow rules

Recommended:

- Namespace default deny ingress
- Explicit egress restrictions for core services:
  - frontend proxy -> backend (`/api`, `/auth`, `/user`)
  - backend -> Postgres + namespace-local runtime paths
  - DNS egress
  - backend outbound TLS (`tcp/443`) for API/registry integrations
- Keep backend direct NodePort disabled (`BACKEND_NODEPORT_ENABLED=0`) unless explicitly needed for dev break-glass.

Quick check:

```bash
kubectl -n labs get networkpolicy
kubectl -n labs describe networkpolicy
```

Expected egress policies:

- `bretter-frontend-restrict-egress`
- `bretter-backend-restrict-egress`

## 6) Ingress hardening (optional mTLS)

Recommended:

- TLS enabled with managed cert secret.
- Websocket timeouts set for connect routes.
- Optional mTLS for admin/API endpoints in high-security environments.
- Restrict source CIDRs for VM console when policy requires it.

## 7) Node pool isolation for VM runners

Recommended:

- Use dedicated runner node pool (labels + taints).
- Pin runner workloads with `nodeSelector`/`tolerations`.
- Keep control-plane API/UI workloads separate from heavy runtime pods.

## 8) Backup + restore procedures

Minimum baseline:

- Daily Postgres backup job.
- Backup retention policy (for example, 14 days).
- Weekly restore validation into a non-prod target.
- Keep backup run logs and restore evidence.

Quick checks:

```bash
kubectl -n labs get cronjob
kubectl -n labs get jobs --sort-by=.metadata.creationTimestamp | tail -n 10
```

## 9) Upgrade and migration steps

1. Backup Postgres and export critical config.
2. Apply new image tags/manifests/Helm values.
3. Run `alembic upgrade head` (or startup migration path).
4. Rollout backend/frontend and wait for readiness.
5. Run post-deploy synthetic validation job.
6. Confirm alerts/errors are clean before change close.

## 10) Post-hardening verification

- Login works from approved origins.
- VM launch/connect/delete path passes.
- Container launch/connect/delete path passes.
- Idle timeout prompt appears on user page and connect page.
- `kubectl get events` shows no new recurring failure patterns.

## 11) Kubelet serving cert and metrics TLS posture

Keep kubelet metrics scraping in strict TLS mode:

- Run with `METRICS_SERVER_INSECURE_TLS=0` in production.
- Enable kubelet serving cert bootstrap (`serverTLSBootstrap: true`) so certificates include valid node SANs.
- Keep kubelet-serving CSR approval automated (`ENABLE_KUBELET_SERVING_CSR_AUTOAPPROVAL=1`) or enforce an equivalent signed approval process.
- Verify metrics-server has no `--kubelet-insecure-tls` arg and `kubectl top nodes` returns data.

## 12) Runtime proxy TLS posture

- Keep `BLABS_VM_CONNECT_INSECURE_TLS=0` and `BLABS_CONTAINER_CONNECT_INSECURE_TLS=0` in production.
- Enable the insecure toggles only for local/dev clusters that cannot provide valid upstream certificates.
- Frontend reverse proxy should validate backend TLS (`proxy_ssl_verify on`) using the mounted trust bundle; avoid `proxy_ssl_verify off`.

## 13) Admission policy enforcement (Kyverno)

- Enable `ENABLE_ADMISSION_POLICIES=1` (default) in hardened environments.
- Keep `INSTALL_KYVERNO=1` so setup can install/upgrade Kyverno before policy apply.
- Enforced Bretter core workloads are validated for:
  - immutable image tags (no `:latest` / `:edge`)
  - non-root + RuntimeDefault seccomp
  - `allowPrivilegeEscalation=false` + dropped capabilities
  - explicit CPU/memory requests and limits

## 14) Runtime and signature secrets

- Keep `SECRETS_ENCRYPTION_KEY` empty in committed production values.
- Ensure runtime encryption key is injected through `bretter-runtime-secrets` (`secrets_encryption_key` key by default).
- Ensure `bretter-cosign-public-key` contains your official `cosign.pub`.
- Keep `CONTAINER_SIGNATURE_VERIFICATION_ENABLED=1` with `CONTAINER_SIGNATURE_KEY_REF=/etc/bretter-signing/cosign.pub`.
- After rollout, run:
  - `NAMESPACE=labs ./scripts/production_go_live_proof.sh`

## Related pages

- [Production Architecture](Production-Architecture)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Security and Auth](Security-and-Auth)
