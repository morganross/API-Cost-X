# Self-Hosting Guide

APICostX runs as a single-user local application with one API service process and one web GUI process on the same machine.

This document describes the public install flow for running APICostX locally.

## Security Model

APICostX is local-only software. It has no login, no account system, and no multi-user authorization layer. That is intentional for a single-user self-hosted app, but it also means the app must stay bound to localhost unless you add your own trusted network controls.

Never expose APICostX directly to the public internet, a shared LAN, or an untrusted network. Anyone who can reach it can use the app and any provider keys loaded from your local `.env`.

## Requirements

- Linux, macOS, or WSL for the shell scripts
- Python 3
- Node.js 20+ and npm for the web GUI
- Local provider credentials supplied by the person running the app

On apt-based Linux systems, `install.sh` installs Node.js 20.x through NodeSource when the system Node.js is missing or too old. On macOS, WSL without apt, or other Linux distributions, install Node.js 20+ before running the installer.

## First Run

```bash
git clone https://github.com/morganross/API-Cost-X.git
cd API-Cost-X
./install.sh
./start.sh
```

`install.sh` creates `.env` from `.env.example` if needed, prepares local runtime folders, and initializes the main SQLite database from the bundled sanitized base DB when `data/api-cost-x.db` does not already exist. Existing runtime databases are never overwritten.

`start.sh` starts the API service and web GUI using the ports from `.env`. It also repeats the same first-run database initialization check so direct starts after a deleted DB recover cleanly.

By default, `start.sh` binds to `127.0.0.1`. Non-localhost binds are refused unless you explicitly set the unsafe override documented in `.env.example`.

## Main SQLite Database

The self-hosted app uses one runtime SQLite database, normally:

```text
data/api-cost-x.db
```

The bundled base database at `api/app/seed/api-cost-x.seed.db` is copied into that main runtime path only on first install/start. Saved presets and sample history then live in the same main SQLite database as all future local data. Sample generated Markdown files are copied under `data/user_local/` so the included history can open generated documents.

Provider/API secrets are not stored in SQLite. They belong only in the root `.env` file.

If you use GitHub import/export features, use the narrowest token permissions possible. GitHub folder creation can write `.gitkeep` files to connected repositories.

## Local Ports

Default API URL:

```text
http://127.0.0.1:8000/api
```

Default GUI URL:

```text
http://127.0.0.1:5173
```

## Design Constraints

- Single local user.
- Localhost-only by default.
- No external CMS runtime.
- No production server access.
- No account system in the local OSS mode.
- Plain SQLite only in the local OSS mode.
- One main runtime SQLite database; no separate runtime seed database.
- Runtime data stays local under `data/` and is ignored by Git.

## Verification

After `./install.sh`, run:

```bash
scripts/validate.sh
scripts/smoke.sh
```

The smoke script starts the local API briefly, checks health endpoints, then builds the GUI.


## Source And Build Artifacts

The public repository includes the web GUI source and the sanitized bundled base database. It does not include built web assets. Running `npm run build` or the installer creates `assets/react-build/` locally; that output is ignored by Git and should not be uploaded.
