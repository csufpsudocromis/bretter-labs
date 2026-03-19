# Contributing to Bretter Labs

Thanks for contributing.

## Before You Start

- Read [README.md](README.md) and the wiki pages in `docs/wiki/`.
- For behavior/security changes, review:
  - [SECURITY.md](SECURITY.md)
  - [docs/wiki/Hardened-Deployment-Guide.md](docs/wiki/Hardened-Deployment-Guide.md)
  - [docs/wiki/Secret-Operations-Runbook.md](docs/wiki/Secret-Operations-Runbook.md)

## Local Setup

1. Clone the repository.
2. Create backend virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
```

3. Install frontend dependencies:

```bash
cd frontend-vite
npm ci
cd ..
```

## Development Workflow

1. Create a feature branch from `main`.
2. Make focused changes with tests.
3. Run local checks before opening a PR.

Recommended checks:

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend-vite run format:check
./scripts/ci_guardrails.sh
```

## Pull Request Guidelines

- Keep PRs scoped to one goal.
- Include:
  - What changed
  - Why it changed
  - Risk/rollback notes (if operational impact)
- Update docs when changing:
  - Setup/deploy behavior
  - Security/auth behavior
  - Production values/validation expectations
- For UI changes, include screenshots.

## Commit Guidance

- Use clear commit messages with a short imperative subject.
- Conventional style is recommended (`feat:`, `fix:`, `docs:`, `chore:`), but not required.

## Release Expectations

- `VERSION`, `CHANGELOG.md`, and frontend package versions must remain consistent.
- Production image refs in `deploy/helm/values-production.yaml` must stay digest-pinned.

## Where to Ask Questions

- Use GitHub Discussions for design/Q&A.
- Use Issues for reproducible bugs and feature requests.
