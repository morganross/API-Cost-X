# Contributing

APICostX is a local-first, single-user self-hosted app. Contributions should preserve these constraints:

- No account system.
- Plain SQLite only.
- One root `.env` for local secrets.
- No generated web build output, dependency folders, databases, logs, or private keys in Git.
- Web GUI source is committed; built assets are generated locally.

Run validation before submitting changes:

```bash
scripts/validate.sh
cd web-gui && npm run build
```
