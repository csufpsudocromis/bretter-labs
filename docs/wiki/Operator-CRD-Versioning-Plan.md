# Operator CRD Versioning Plan

Last reviewed: March 20, 2026.

This page defines the version strategy for `LabInstance` and `LabImageImport` CRDs.

## Current state

- Storage version: `v1alpha1`
- Served version: `v1alpha1`
- Conversion webhook: not enabled yet

## Version policy

1. `v1alpha1`
   - Rapid iteration allowed.
   - Breaking schema changes allowed between minor releases with explicit migration notes.
2. `v1beta1`
   - Backward-compatible by default.
   - Any incompatible field change requires conversion strategy + deprecation window.
3. `v1`
   - Stable contract.
   - No breaking changes without multi-release migration and conversion support.

## Planned path to `v1beta1`

1. Freeze core status vocabulary:
   - `phase`
   - condition types/reasons
2. Freeze required `spec` fields used by controller reconcile.
3. Add compatibility fields for renamed keys as optional aliases.
4. Add conversion strategy before introducing storage-version changes.

## Conversion strategy options

### Option A: Structural compatibility only (short term)

- Keep fields compatible across `v1alpha1` and `v1beta1`.
- Use defaulting and nullable aliases.
- No webhook required initially.

### Option B: Conversion webhook (target for long-lived multi-version support)

- Add conversion webhook service.
- Store canonical internal shape.
- Convert served versions bidirectionally.

Recommended: start with Option A, adopt Option B before deprecating `v1alpha1`.

## Deprecation policy

- Mark deprecated fields in docs one release before removal.
- Keep deprecated fields readable for at least one minor release after deprecation.
- Emit warning-level condition/event when deprecated fields are used.

## CI/versioning gates

- CRD schema lint must pass:
  - `python3 scripts/lint_crd_schema.py`
- Server-side apply check must pass:
  - `kubectl apply --dry-run=server -k deploy/crds`
- Migration docs must be updated when CRD fields change:
  - [Operator/CRD Migration Plan](Operator-CRD-Migration-Plan)

## Release checklist for CRD changes

1. Update CRD schema.
2. Update controller reconcile logic and tests.
3. Update backfill script if field mapping changed.
4. Update canary smoke expectations.
5. Update this versioning page and upgrade notes.
