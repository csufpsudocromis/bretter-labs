# Operator Incident Runbook

Last reviewed: March 20, 2026.

Use this runbook when `LabInstance` reconciliation is degraded, stuck, or timing out.

## Scope

- `LabInstance` CRD controller health
- Reconcile failures and stuck pending/starting instances
- Finalizer backlog during delete
- Safe rollback to backend-imperative orchestration

## Fast triage checklist

```bash
kubectl -n labs get deploy bretter-labinstance-operator
kubectl -n labs get pods -l app.kubernetes.io/name=bretter-labinstance-operator
kubectl -n labs logs deploy/bretter-labinstance-operator --tail=300
kubectl -n labs get labinstances.labs.bretter.io
```

If the operator is crash-looping:

```bash
kubectl -n labs describe pod -l app.kubernetes.io/name=bretter-labinstance-operator
```

## Metrics and alerts

Expected metrics:

- `blabs_labinstance_reconcile_total{result=...}`
- `blabs_labinstance_finalizer_backlog`
- `blabs_labinstance_stuck_instances`

Primary alert rules:

- `BretterLabInstanceControllerDown`
- `BretterLabInstanceReconcileErrors`
- `BretterLabInstanceFinalizerBacklog`
- `BretterLabInstanceStuckPending`

## Common failure modes

### Reconcile errors increasing

Symptoms:

- Alert `BretterLabInstanceReconcileErrors`
- Controller logs show template/image lookup errors or Kubernetes API failures

Actions:

1. Verify referenced template/image still exists in DB.
2. Verify CRD RBAC grants include `labinstances/status` and runtime resources.
3. Verify runner image pullability on target nodes.

### Finalizer backlog

Symptoms:

- Alert `BretterLabInstanceFinalizerBacklog`
- `metadata.deletionTimestamp` set but object remains

Actions:

1. Inspect finalizer state and blocked runtime resources:
   ```bash
   kubectl -n labs get labinstance <id> -o yaml
   kubectl -n labs get pods,svc,pvc | rg "<id-short>"
   ```
2. Resolve underlying delete failures.
3. If teardown is complete, remove finalizer manually as last resort:
   ```bash
   kubectl -n labs patch labinstance <id> --type=merge -p '{"metadata":{"finalizers":[]}}'
   ```

### Instances stuck pending/starting

Symptoms:

- Alert `BretterLabInstanceStuckPending`
- CRD phase remains `Pending/Building/Starting`

Actions:

1. Check pod scheduling reasons:
   ```bash
   kubectl -n labs describe pod vm-<owner>-<id>
   ```
2. Validate clone source PVC and VM storage class.
3. Validate node capacity and image pull access.

## Emergency rollback

If operator path is unstable, move traffic back to legacy backend orchestration:

1. Set backend env:
   - `BLABS_ORCHESTRATION_BACKEND=db`
2. Roll out backend deployment:
   ```bash
   kubectl -n labs rollout restart deploy/bretter-backend
   kubectl -n labs rollout status deploy/bretter-backend --timeout=300s
   ```
3. Scale down operator:
   ```bash
   kubectl -n labs scale deploy/bretter-labinstance-operator --replicas=0
   ```
4. Run post-deploy checks:
   ```bash
   NAMESPACE=labs ./scripts/production_go_live_proof.sh
   ```

## Post-incident actions

- Capture a timeline with alert times and remediation commands.
- Backfill/repair CRD state if needed:
  ```bash
  .venv/bin/python scripts/backfill_labinstances_from_db.py --dry-run
  ```
- Add regression test coverage for the observed failure class.

## Related pages

- [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)
- [Operations Runbook](Operations-Runbook)
- [Error Catalog](Error-Catalog)
