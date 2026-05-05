# APICostX

APICostX is the public, self-hosted application for local AI model evaluation.

Current status: self-hosted application scaffold with migrated API service, web GUI source, and local installer scripts. It is single-user, local-first, and uses plain SQLite with no account system.

## Development Rules

1. Development happens on `shdn` in `/home/ubuntu/acm-oss/work/API-Cost-X`.
2. Production web-gui and api servers are off-limits.
3. Code from the private source repos is copied by whitelist only.
4. Run `scripts/scan-secrets.sh` before every commit.

## Included Source

- `.env.example` defines local placeholder configuration. Runtime secrets live only in the root `.env` copied from this file.
- `install.sh` prepares local folders, initializes the main SQLite database, installs API dependencies, and installs web GUI dependencies.
- `start.sh` starts the local API service and web GUI.
- `docs/public-copy-policy.md` defines what can and cannot be copied into this public repo.
- `.github/workflows/public-release-hygiene.yml` runs repository validation in CI.
- `scripts/validate.sh` is the local validation entrypoint.
- `scripts/smoke.sh` verifies api health and web-gui build after install.
- `docs/self-hosting.md` describes the intended local install flow.
- `api/app/seed/api-cost-x.seed.db` is the sanitized base database copied into the main runtime SQLite DB on first install.

## Repo Shape

```text
api/
web-gui/
packages/FilePromptForge/
scripts/
docs/
```


## Generated Files Policy

Commit the web GUI source under `web-gui/src/`, not generated build output. The local build creates `assets/react-build/`, and that directory is intentionally ignored by Git. Do not commit `node_modules/`, `.venv/`, local `.env`, runtime SQLite databases, logs, generated exports, or built assets. The only committed database is the sanitized bundled base DB at `api/app/seed/api-cost-x.seed.db`.
