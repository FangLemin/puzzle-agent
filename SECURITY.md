# Security Policy

PuzzleOps Agent is a public engineering portfolio project. The repository intentionally excludes real business images, row-level private datasets, Feishu URLs, API keys, tokens and production credentials.

## Reporting a Vulnerability

Do not open a public issue containing credentials, private URLs, personal data or exploitable details. Use GitHub's private security advisory flow for this repository when available. If private reporting is unavailable, open a minimal issue that contains no sensitive detail and asks the maintainer for a private contact channel.

## Supported Version

Security fixes target the latest commit on `main`. Historical tags and local demo configurations are not maintained as separate security release lines.

## Deployment Boundary

- The default local UI binds to localhost and is intended for local demonstration.
- Team deployment requires HTTPS, firewall rules, PostgreSQL backups, Redis authentication, OSS access control and secret injection from the deployment environment.
- API tokens must be rotated and stored as hashes; role and country scope must be checked on every protected request.
- Feishu sync and other external writes require human confirmation and audit logging.
- Provider failures must remain observable as failed/skipped states and must not be converted into apparent success.

## Release Checks

Before every public release, run:

```bash
python scripts/release_preflight.py
PYTHONPATH=. pytest tests -q
```

The GitHub Actions workflow runs the same checks with remote model and vector calls disabled.
