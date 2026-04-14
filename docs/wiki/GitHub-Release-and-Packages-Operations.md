# GitHub Release and Packages Operations

Last reviewed: April 14, 2026.

This page is the operator runbook for repository metadata, releases, CI guardrails, and GHCR package publishing.

## Repository metadata baseline

Keep these GitHub repo settings populated:

- Description and homepage URL
- Topics (for discoverability)
- Discussions enabled
- Projects enabled (if used operationally)
- Security policy (`SECURITY.md`) and contribution docs visible in repo root

## Versioning and release flow

Source of truth:

- `VERSION` (semantic version)
- `CHANGELOG.md` (Keep a Changelog format)

Current release baseline in this repository:

- `0.6.1`

Release workflow:

1. Bump version with helper:
   - `python3 scripts/bump_version.py patch` (or `minor`/`major`)
2. Update changelog entry.
3. Validate release discipline:
   - `python3 scripts/check_release_discipline.py`
4. Create and push tag:

```bash
git tag v$(cat VERSION)
git push origin v$(cat VERSION)
```

Tag push triggers:

- `.github/workflows/release-on-tag.yml`

## Container image publishing (GHCR)

Workflow:

- `.github/workflows/publish-and-pin-images.yml`

Published package set:

- `ghcr.io/<namespace>/bretter-backend` (admin-tools image)
- `ghcr.io/<namespace>/bretter-backend-admin` (compatibility alias tag)
- `ghcr.io/<namespace>/bretter-backend-runtime` (runtime-only backend image)
- `ghcr.io/<namespace>/bretter-frontend`
- `ghcr.io/<namespace>/win-vm-runner`

Pipeline gates:

1. publish images with SBOM/provenance
2. scan published refs with Trivy (HIGH/CRITICAL)
3. keyless sign and verify with Cosign
4. promote digests into `deploy/helm/values-production.yaml` (only after gates pass)
5. deploy workflow proof gate validates authenticated synthetic VM launch + Guacamole RDP frame + admin image upload/finalize/delete

Inputs:

- `version`
- `image_namespace`
- `commit_digest_update` (`true`/`false`)

### Auth configuration

Recommended for reliable publishing to existing private packages:

- Repository Actions secret `GHCR_USERNAME`
- Repository Actions secret `GHCR_PAT` (scope: `write:packages`)

Fallback:

- If these are absent, workflow falls back to `GITHUB_TOKEN`.

### Runner image build requirements

Runner publish step must use:

- `context: runner`
- `file: runner/Dockerfile`

This ensures assets like `rdp.html` are present during build.

## Digest pin management

The publish workflow can optionally update:

- `deploy/helm/values-production.yaml`

When `commit_digest_update=true`, workflow writes release-tagged digest refs
(`ghcr.io/<ns>/<image>:vX.Y.Z@sha256:...`) and pushes a commit.

## CI guardrails expectations

Primary CI gate:

- `.github/workflows/ci-guardrails.yml`
- Release branch runtime gates:
  - `.github/workflows/post-deploy-synthetic.yml`
  - `.github/workflows/nightly-restore-drill.yml`
  - `.github/workflows/playwright-rdp-smoke.yml`

It validates:

- Release/version discipline
- Production profile checks
- Python tests/guardrails
- Frontend lint/format/build
- Security scans and smoke checks

Release branch (`release/**`) expectations:

- Post-deploy synthetic/API smoke runs on push + PR.
- Restore drill workflow runs on push + PR.
- Playwright Guacamole RDP browser smoke runs on push + PR.
- Nightly restore drill defaults to strict backup-CronJob enforcement (`require_backup_cronjob=true`).
- Configure these jobs as required branch checks in GitHub rulesets/branch protection.

## Common GitHub-side failures

### GHCR push `403 Forbidden`

Fix:

- Ensure `GHCR_USERNAME` + `GHCR_PAT` secrets exist and PAT has `write:packages`.
- Re-run publish workflow from latest `main`.

### Guardrails fail on old commit after fix merged

Cause:

- Re-run uses historical commit, not current `main`.

Fix:

- Trigger a new run against latest `main` head.

### Publish fails with `"/rdp.html": not found`

Fix:

- Confirm runner publish step uses `context: runner`.

## Verification checklist

After publish/release:

1. Release exists at GitHub Releases tab for current tag.
2. Latest publish run is green.
3. GHCR package timestamps updated for:
   - `bretter-backend`
   - `bretter-backend-admin` (compatibility alias)
   - `bretter-backend-runtime`
   - `bretter-frontend`
   - `win-vm-runner`
4. Package pages show source repo linkage.

## Related pages

- [Home](Home)
- [Production Helm Values Reference](Production-Helm-Values-Reference)
- [Operations Runbook](Operations-Runbook)
- [Upgrade and Rollback](Upgrade-and-Rollback)
