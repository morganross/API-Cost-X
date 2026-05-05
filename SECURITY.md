# Security Policy

APICostX is intended for local self-hosting by one user on a trusted machine.
It has no login system and should not be exposed directly to the public internet.

## Secrets

All local secrets belong in the project root `.env` file. Do not commit `.env`, private keys, database files, logs, generated exports, or built assets.

Before publishing or opening a pull request, run:

```bash
scripts/validate.sh
```

## Reporting Issues

Do not include real provider keys, tokens, database files, logs containing secrets, or private documents in public issues.
