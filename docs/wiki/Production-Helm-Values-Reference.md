# Production Helm Values Reference

Last reviewed: March 9, 2026.

Canonical file:

- [`deploy/helm/values-production.yaml`](../../deploy/helm/values-production.yaml)

Use that file as the baseline and override per environment.

## Required overrides before production use

- `appTemplateValues.CONTROL_NODE`
- `appTemplateValues.NODE_EXTERNAL_HOST`
- `appTemplateValues.VM_STORAGE_CLASS`
- `appTemplateValues.BACKEND_IMAGE` (must be digest-pinned)
- `appTemplateValues.FRONTEND_IMAGE` (must be digest-pinned)
- `appTemplateValues.RUNNER_IMAGE` (must be digest-pinned)
- `appTemplateValues.PUBLIC_SCHEME`
- `appTemplateValues.TLS_SECRET_NAME`
- `ingress.host`
- `ingress.tls.secretName`
- `cors.allowedOrigins`
- `database.postgres.storageClass`
- `secrets.externalSecrets.clusterSecretStore`
- `secrets.externalSecrets.postgresSecretName`
- `runner.nodeSelector` and `runner.tolerations`
- `networkPolicy.vmConsoleSourceCidrs` (if restricting console sources)

## Key sections and intent

- `global`:
  - Namespace, app version, public scheme.
- `ingress`:
  - TLS, host, websocket timeout, optional mTLS controls.
- `cors`:
  - Allowed origins and constrained regex policy.
- `auth`:
  - Session cookie and connect-token TTL controls.
- `frontend` and `backend`:
  - Replicas/resources/probes/security contexts.
- `runner`:
  - Runtime pod placement and VM network backend posture.
- `networkPolicy`:
  - Default-deny posture and explicit flow allow toggles.
- `database`:
  - Postgres size/resources and migration behavior.
- `secrets`:
  - External Secrets provider wiring.
- `quotas`:
  - Namespace CPU/RAM/storage guardrails.
- `backup`:
  - Backup schedule and restore validation cadence.
- `observability`:
  - Monitoring + post-deploy synthetic checks.

## Usage pattern

1. Copy production values into an environment overlay.
2. Commit environment-specific override files.
3. Deploy with explicit values files in order.
4. Run rollout status + post-deploy synthetic check.

Example:

```bash
helm upgrade --install bretter-labs ./deploy/helm \
  -n labs \
  -f deploy/helm/values-production.yaml \
  -f deploy/helm/values-prod-site.yaml
```

## Related pages

- [Production Architecture](Production-Architecture)
- [Hardened Deployment Guide](Hardened-Deployment-Guide)
- [Post-Deploy Validation SOP](Post-Deploy-Validation-SOP)
