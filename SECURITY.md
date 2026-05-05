# Security Policy

APICostX is intended for local self-hosting by one user on a trusted machine.
It has no login system and should not be exposed directly to the public internet.

The default runtime bind address is `127.0.0.1`. Treat any non-localhost bind as unsafe unless you place APICostX behind your own trusted network controls. Anyone who can reach the app can use the local API service and provider-backed actions.

The public repository security page is:

https://github.com/morganross/API-Cost-X/security

## Secrets

All local secrets belong in the project root `.env` file. Do not commit `.env`, private keys, database files, logs, generated exports, or built assets.

Use least-privilege provider and GitHub tokens. GitHub features may read repository contents and can write small files such as `.gitkeep` when creating folders, depending on the action you run.

Before publishing or opening a pull request, run:

```bash
scripts/validate.sh
scripts/smoke.sh
```

The bundled seed database is intentionally committed, but it should be treated as a release artifact: scan it, dump/review it, and confirm it contains only sanitized sample data before publishing.

## Reporting Issues

Do not include real provider keys, tokens, database files, logs containing secrets, or private documents in public issues.

For a vulnerability that should not be public yet, use GitHub private vulnerability reporting:

https://github.com/morganross/API-Cost-X/security/advisories/new

If private reporting is unavailable, open a public issue with only a high-level description and no secrets or exploit-ready details. A maintainer will move the discussion to a safer channel before requesting logs, files, or reproduction details.

Expected response target:

- A maintainer acknowledges valid reports within 7 days.
- High-risk secret leakage, unauthenticated network exposure, or unsafe file-read/write behavior is prioritized before normal feature work.
- Public disclosure happens only after a fix or mitigation is available, unless the issue is already public.

## Supported Versions

Only the latest release and the current `main` branch receive security fixes. Users should update to the newest signed GitHub release when one is available.

## Release Verification

Release archives are published from the `Release` GitHub Actions workflow. Each release asset includes SHA256 checksums and Sigstore keyless signature bundles (`*.sigstore.json`) for archive verification.
