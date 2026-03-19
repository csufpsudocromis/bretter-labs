# Security Policy

## Supported Versions

Security fixes are prioritized for the current release line.

| Version | Supported |
| --- | --- |
| `0.3.x` | Yes |
| `< 0.3.0` | No |

## Reporting a Vulnerability

Please do not open public issues for security vulnerabilities.

Use one of these paths:

1. Preferred: GitHub private vulnerability report  
   `https://github.com/csufpsudocromis/bretter-labs/security/advisories/new`
2. If that path is unavailable, open a minimal issue requesting a private contact path without publishing exploit details.

Include:

- Affected version/commit
- Reproduction steps
- Impact assessment
- Suggested mitigation (if available)

## Response Targets

- Initial triage acknowledgement: within 3 business days
- Status update cadence: at least weekly until resolved
- Fix coordination: patch + release note + upgrade guidance when applicable

## Scope Notes

High-priority areas in this project include:

- Authentication/session handling
- OIDC/LDAP integration
- Secret handling/encryption key flow
- Kubernetes privilege boundaries and runtime controls
- Image supply-chain verification paths
