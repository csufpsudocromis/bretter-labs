# Operations Runbook

Last reviewed: March 16, 2026.

## Production pre-rollout gate

Validate production profile values before deployment:

```bash
python3 scripts/check_release_discipline.py
python3 scripts/validate_production_profile.py --strict -f deploy/helm/values-production.yaml
```

If you use environment overlays, include them in validation order:

```bash
python3 scripts/validate_production_profile.py --strict \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

## Baseline checks

Confirm context/namespace first:

```bash
kubectl config current-context
kubectl get ns labs
```

Core platform checks:

```bash
kubectl -n labs get deploy bretter-backend bretter-frontend bretter-postgres
kubectl -n labs get pods -o wide
kubectl -n labs logs deploy/bretter-backend --tail=200
kubectl -n labs logs deploy/bretter-frontend --tail=200
```

Bootstrap env pruning check (after first bootstrap rollout):

```bash
kubectl -n labs get deploy bretter-backend -o yaml | rg BLABS_ADMIN_DEFAULT_PASSWORD
```

Expected: no output.

Runtime secret wiring checks:

```bash
kubectl -n labs get secret bretter-runtime-secrets -o go-template='{{index .data "secrets_encryption_key"}}' | wc -c
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | wc -c
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | base64 -d | sha256sum
```

Expected:

- First two commands print a value greater than `0`.
- Third command prints the expected SHA256 fingerprint for your official cosign public key.

## VM and container workload visibility

List only user runtime pods:

```bash
kubectl -n labs get pods | rg '^vm-|^virt-launcher-|^ct-'
```

Separate quick filters:

```bash
kubectl -n labs get pods | rg '^ct-'
kubectl -n labs get pods | rg '^vm-|^virt-launcher-'
```

## Rollout verification

```bash
kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
kubectl -n labs rollout status deploy/bretter-frontend --timeout=300s
kubectl -n labs get pods -o wide
```

## Pre-deploy gate

Run this before rollout to catch config/secret blockers early:

```bash
NAMESPACE=labs ./scripts/deploy_preflight.sh
```

For CI/static-only usage (skip cluster calls):

```bash
SKIP_CLUSTER_CHECKS=1 ./scripts/deploy_preflight.sh
```

If image-based runner changes were deployed, verify both nodes can pull/start:

```bash
kubectl -n labs get pods -o wide | rg 'vm-|virt-launcher|ct-'
```

## Post-deploy synthetic validation (manual)

1. Login on a fresh browser profile.
2. Start one VM lab and verify states progress to `Running`.
3. Connect to VM and verify interactive session.
4. Delete VM lab.
5. Start one container lab and verify `Building -> Starting -> Running`.
6. Connect to container app and verify app response.
7. Confirm idle prompt appears on both user page and connect tab.
8. Confirm deleting the running lab clears single-lab-limit message.

## Go-live proof artifact

Generate and archive a single report covering rollout, production env checks, runtime/signature secret wiring, bootstrap pruning, and API health:

```bash
NAMESPACE=labs ./scripts/production_go_live_proof.sh
```

## Quotas and scaling checks

```bash
kubectl -n labs get resourcequota bretter-runtime-quota -o yaml
kubectl -n labs describe resourcequota bretter-runtime-quota
kubectl -n labs get limitrange bretter-default-container-limits -o yaml
```

Admin UI checks:

- `/admin/scaling-quotas` should load available namespaces.
- `/admin/scaling-quotas` should allow team+namespace quota rows (not only `default`).
- Quota changes should apply to both VM and container starts.
- When hit, users should receive quota detail (HTTP 429) or queued reason.
- `/admin/audit-events` should show recent admin mutations for templates/images/quotas/settings.

## Common incidents and triage

### CI guardrail run fails with missing `httpx`

Symptom:

- GitHub Actions guardrail job fails while loading `fastapi.testclient` with:
  - `RuntimeError: ... requires the httpx package to be installed`

Actions:

1. Confirm the run is for latest `main`, not a historical commit.
2. Re-trigger CI from current `main` head.
3. Validate install/import step in workflow:

```bash
.venv/bin/pip install -r backend/requirements.txt pytest httpx
.venv/bin/python -c "import httpx; from fastapi.testclient import TestClient"
```

### Labs stuck in pending/queued

Check scheduler pressure and events:

```bash
kubectl -n labs get pods | rg 'Pending|ContainerCreating'
kubectl -n labs describe pod <pod-name>
kubectl describe nodes | rg -n 'DiskPressure|MemoryPressure|PIDPressure|Ready'
```

Typical causes:

- Node resource pressure (CPU/memory/disk)
- PVC/storage class scheduling failure
- Namespace/team quota limits

### Token/session auth failures (`invalid token`, `session expired`, repeated login loops)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'invalid token|session expired|missing authorization token|auth'
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_AUTH_COOKIE_SECURE|BLABS_CONNECT_COOKIE_SECURE|BLABS_PUBLIC_SCHEME|BLABS_CORS_ALLOWED_ORIGINS'
```

If users report sudden login failures after rollout:

1. Verify frontend origin is included in `BLABS_CORS_ALLOWED_ORIGINS`.
2. Verify requests are using the same scheme/host (`https` in production).
3. Confirm system clocks are sane on control plane and worker nodes.

### Runner scheduling failures (node selector / taints / insufficient resources)

Checks:

```bash
kubectl -n labs get pods -o wide | rg '^vm-|^virt-launcher-'
kubectl -n labs describe pod <vm-runner-or-virt-launcher-pod>
kubectl get nodes --show-labels | rg 'kubernetes.io/hostname|runner'
kubectl describe nodes | rg -n 'Taints|Allocatable|DiskPressure|MemoryPressure'
```

Look for:

- `0/X nodes are available` scheduler messages.
- Node selector mismatch with `RUNNER_NODE_SELECTOR_VALUE`.
- Resource shortage, taint rejection, or storage attach constraints.

### Signature verification failures (container image registration/start)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'signature|cosign|verification|public key'
kubectl -n labs get secret bretter-cosign-public-key -o go-template='{{index .data "cosign.pub"}}' | wc -c
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_CONTAINER_SIGNATURE_VERIFICATION_ENABLED|BLABS_CONTAINER_SIGNATURE_KEY_REF|container-signature-key'
```

If verification fails:

1. Confirm key secret contains the expected file (`cosign.pub` by default).
2. Confirm `BLABS_CONTAINER_SIGNATURE_KEY_REF` matches the mounted file path.
3. Re-check key fingerprint against your trusted source before retrying image registration.

### Runtime secret injection misconfiguration

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=300 | rg -n 'BLABS_SECRETS_ENCRYPTION_KEY|decrypt|Encrypted secret'
kubectl -n labs get secret bretter-runtime-secrets -o go-template='{{index .data "secrets_encryption_key"}}' | wc -c
kubectl -n labs get deploy bretter-backend -o yaml | rg 'BLABS_SECRETS_ENCRYPTION_KEY'
```

Common outcomes:

- Missing key -> startup fails in production profile.
- Wrong key -> decrypt errors for encrypted settings.
- Secret key-name drift -> deployment references a different key than the secret provides.

### Upload appears stuck at 100%

Usually browser upload is done and cluster finalization is still running.

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'upload|finaliz|convert|cdi|data volume|error'
kubectl -n labs get pvc
kubectl describe nodes | rg -n 'DiskPressure|imagefs|nodefs'
```

### Connect issues (blank page, proxy error, delayed connect)

Checks:

```bash
kubectl -n labs logs deploy/bretter-backend --tail=400 | rg -i 'connect|proxy|websocket|token|origin|error'
kubectl -n labs get svc | rg 'ct-|vm-|bretter'
kubectl -n labs get endpoints | rg 'ct-|vm-'
```

Common causes:

- App/VM not ready yet (`Starting`)
- Connect session cookie missing/expired
- Backend cannot reach container service/pod endpoint

### Storage pressure and disk alerts

Checks:

```bash
kubectl -n labs get pvc
kubectl describe nodes | rg -n 'DiskPressure|nodefs|imagefs'
kubectl -n longhorn-system get nodes.longhorn.io
kubectl -n longhorn-system get volumes.longhorn.io
```

If alerts indicate sustained high usage, clean stale labs/uploads and expand node storage.

## Alerts and error log behavior

`/admin/alerts-errors` behavior:

- Alertmanager alerts are pulled from configured API URL.
- Error log is capped at 10MB.
- Oldest log lines are dropped when cap is reached.
- UI shows 50 log entries per page with page navigation.
- `Clear Error Log` truncates backend error logs.

## Single active lab enforcement

The platform enforces one active workload per user across VM + container starts.

Expected user message:

`You already have a virtual lab running. Delete the current lab before starting a new one.`

If this persists unexpectedly, verify the user has no active VM/container instance records and no stuck runtime pods.

## Related pages

- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
- [Secret Operations Runbook](Secret-Operations-Runbook)
- [Error Catalog](Error-Catalog)
- [Storage Capacity Playbook](Storage-Capacity-Playbook)
- [Network Modes Reference](Network-Modes-Reference)
- [LDAP Authentication](LDAP-Authentication)
