# API Contract and Drift Guardrails

Last reviewed: March 20, 2026.

## Purpose

Keep backend OpenAPI and frontend API types in lockstep and fail CI when they drift.

## Canonical artifacts

- Backend OpenAPI snapshot: `backend/openapi/openapi.json`
- Frontend generated types: `frontend-vite/src/api/openapi-types.d.ts`

## Regenerate artifacts

```bash
python3 scripts/export_openapi_schema.py
npm --prefix frontend-vite run generate:api-types
```

## Drift checks

Backend snapshot drift:

```bash
python3 scripts/check_openapi_drift.py
```

Frontend type drift:

```bash
npm --prefix frontend-vite run generate:api-types
git diff -- frontend-vite/src/api/openapi-types.d.ts
```

## CI coverage

`CI Guardrails` includes:

- `python scripts/check_openapi_drift.py`
- `npm --prefix frontend-vite run generate:api-types`
- `git diff --exit-code -- frontend-vite/src/api/openapi-types.d.ts`

## Update workflow

When API endpoints/models change:

1. Update backend code + tests.
2. Regenerate OpenAPI + frontend types.
3. Commit both generated artifacts in the same PR.
4. Verify CI guardrails pass.
