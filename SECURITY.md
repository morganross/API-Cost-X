# Security Policy

APICostX is intended for local self-hosting by one user on a trusted machine.
It has no login system and should not be exposed directly to the public internet.

The default runtime bind address is `127.0.0.1`. Treat any non-localhost bind as unsafe unless you place APICostX behind your own trusted network controls. Anyone who can reach the app can use the local API service and provider-backed actions.

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
