# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.1] - 2026-04-14

### Added

- Namespace-scoped lab operations end-to-end, including namespace assignment/switching UX and namespace-aware template visibility.
- Golden-image and ISO administration workflows (create/update/copy image lifecycle with admin update VM path).
- Operator-facing runbook coverage for production checks, release/package operations, and image/ISO lifecycle management.

### Changed

- Production hardening defaults and validation flow (strict profile, explicit runtime secret wiring, stronger image/signature posture).
- VM/container launch and orchestration reliability across namespace-scoped runtime paths and clone-based VM storage flows.
- Documentation and release metadata refreshed to match current package topology and release baseline (`0.6.1`).

## [0.3.1] - 2026-03-09

### Added

- Added release/version discipline foundations:
  - Canonical `VERSION` file.
  - Version consistency checks in CI guardrails.
  - Version bump helper script for repeatable releases.
  - Backend API version now resolved from project version.
