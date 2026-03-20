# Operator/CRD Migration Plan

Last reviewed: March 20, 2026.

This page is the operator-facing summary for the Kubernetes-native control-plane migration.

Canonical design document:

- [../operator-crd-migration-plan.md](../operator-crd-migration-plan.md)

## Why this migration exists

- Reduce lifecycle drift and stuck states caused by imperative orchestration paths.
- Move runtime ownership to Kubernetes reconciliation and finalizers.
- Keep API/UI stable while improving reliability, rollback, and observability.

## Scope

- New CRDs:
  - `LabInstance` (`labs.bretter.io/v1alpha1`)
  - `LabImageImport` (`labs.bretter.io/v1alpha1`)
- New ownership model:
  - FastAPI validates/authenticates and writes desired state.
  - Operator reconciles runtime resources and writes status conditions.

## What is checked into the repo now

- CRD manifests:
  - `deploy/crds/labinstances.labs.bretter.io.yaml`
  - `deploy/crds/labimageimports.labs.bretter.io.yaml`
  - `deploy/crds/kustomization.yaml`
- Phase-by-phase migration/rollback strategy:
  - [../operator-crd-migration-plan.md](../operator-crd-migration-plan.md)

## Validate and inspect CRDs

```bash
# server-side schema check
kubectl apply --dry-run=server -k deploy/crds

# apply when ready
kubectl apply -k deploy/crds

# inspect
kubectl get crd labinstances.labs.bretter.io labimageimports.labs.bretter.io
kubectl explain labinstance.spec
kubectl explain labimageimport.status
```

## Rollout safety rules

- Keep operator cutover behind feature flags until parity SLOs are proven.
- Preserve backend fallback path for at least one full release after cutover.
- Do not store plaintext secret values in CRD `spec` or `status`.
- Use condition-based status (`type/reason/message`) as the source of truth for UI and alerts.
