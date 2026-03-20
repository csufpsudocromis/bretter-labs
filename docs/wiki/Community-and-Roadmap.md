# Community and Roadmap

This page tracks where to collaborate in public and what is planned next.

Last reviewed: March 20, 2026.

## Collaboration Channels

- Discussions (Q&A, design, roadmap):  
  https://github.com/csufpsudocromis/bretter-labs/discussions
- Issues (actionable bugs and feature work):  
  https://github.com/csufpsudocromis/bretter-labs/issues
- Pull requests:  
  https://github.com/csufpsudocromis/bretter-labs/pulls

## Suggested Discussion Seeds

- Roadmap: near-term milestones and version targets  
  https://github.com/csufpsudocromis/bretter-labs/discussions/2
- Operator Q&A: cluster setup and hardening troubleshooting  
  https://github.com/csufpsudocromis/bretter-labs/discussions/3
- Security/auth design thread (OIDC/SAML/LDAP)  
  https://github.com/csufpsudocromis/bretter-labs/discussions/4

## Project Board

If Projects is enabled, keep one public board with:

- Backlog
- Ready
- In Progress
- Review
- Done

Track release-critical work with labels + milestones.

## Release Rhythm

- Tag SemVer (`vX.Y.Z`) for each release candidate accepted to ship.
- GitHub release should include:
  - Changelog section
  - Upgrade notes
  - Rollback notes when relevant

## Platform Engineering Milestones

- Operator/CRD architecture migration plan (canonical):
  - [Operator CRD migration blueprint](../operator-crd-migration-plan.md)
- Initial migration artifacts in-repo:
  - `deploy/crds/labinstances.labs.bretter.io.yaml`
  - `deploy/crds/labimageimports.labs.bretter.io.yaml`
- Near-term implementation order:
  1. `LabInstance` shadow mode and status parity checks
  2. VM lifecycle cutover with backend fallback flag
  3. `LabImageImport` cutover for upload/finalize workflow
