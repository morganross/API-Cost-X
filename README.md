# APICostX

<p align="center">
  <a href="https://github.com/morganross/API-Cost-X/actions/workflows/public-release-hygiene.yml"><img alt="Public Release Hygiene" src="https://github.com/morganross/API-Cost-X/actions/workflows/public-release-hygiene.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Self-hosted" src="https://img.shields.io/badge/self--hosted-local--first-0f766e.svg">
  <img alt="SQLite" src="https://img.shields.io/badge/database-SQLite-003B57.svg">
  <img alt="Node 20+" src="https://img.shields.io/badge/node-20%2B-339933.svg">
</p>

APICostX is a single-user, self-hosted application for running and comparing AI generation/evaluation workflows on your own machine.

It ships with a local web GUI, a local API service, FilePromptForge, an installer, and a sanitized sample SQLite database that includes starter presets and sample run history. It does not use WordPress, accounts, multi-user tenancy, external auth, or hosted services for its own runtime state.
<img src="/Screenshot.png" alt="Screenshot" width="800">
## What You Get

- A browser-based web GUI for content, presets, model selection, execution, history, and quality review.
- A local API service on `127.0.0.1`.
- One root `.env` file for all provider keys and local settings.
- One main SQLite database at `data/api-cost-x.db`.
- A bundled sanitized base database with starter content, presets, and sample history.
- Local generated artifacts under `data/user_local/`.

## Requirements

- Git.
- Bash-compatible shell.
- Python 3 with `venv` support.
- Node.js 20+ and npm.
- `curl`.
- Provider API keys for any model providers you want to use.

On apt-based Linux systems, `./install.sh` can install `python3-venv` and Node.js 20.x. On Windows, use WSL for the current shell-based installer. On macOS or non-apt Linux distributions, install Python 3 and Node.js 20+ before running the installer.

## Install

```bash
git clone https://github.com/morganross/API-Cost-X.git
cd API-Cost-X
./install.sh
```

The installer:

- Creates `.env` from `.env.example` if `.env` does not exist.
- Creates local `data/` and `logs/` folders.
- Initializes `data/api-cost-x.db` from the bundled sanitized base database.
- Copies bundled sample artifacts into `data/user_local/`.
- Creates the Python virtual environment and installs the local API package.
- Installs web GUI dependencies with npm.

Existing `.env` files and existing runtime databases are not overwritten.

If you already installed system dependencies yourself, run:

```bash
API_COST_X_SKIP_SYSTEM_DEPS=1 ./install.sh
```

## Configure Provider Keys

All secrets belong in the root `.env` file next to `start.sh`. The public repo includes only `.env.example` with blank/commented examples.

Open `.env`, uncomment only the providers you use, and fill in your keys:

```bash
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# GOOGLE_API_KEY=
# OPENROUTER_API_KEY=
# GROQ_API_KEY=
# PERPLEXITY_API_KEY=
# TAVILY_API_KEY=
# GITHUB_TOKEN=
```

Restart APICostX after changing `.env`.

## Start

```bash
./start.sh
```

Then open:

```text
http://127.0.0.1:5173
```

Default local endpoints:

- Web GUI: `http://127.0.0.1:5173`
- API service: `http://127.0.0.1:8000/api`
- API health check: `http://127.0.0.1:8000/api/health`

Stop the app with `Ctrl+C` in the terminal running `./start.sh`.

## How To Use

1. Open the web GUI at `http://127.0.0.1:5173`.
2. Go to `Settings` and confirm which provider keys APICostX expects in `.env`.
3. Go to `Content Library` to add or review source content.
4. Go to `Presets` to choose saved model/evaluation presets or edit your own.
5. Go to `Execute` to start a run.
6. Use `Execution History` to inspect previous runs, generated documents, logs, and run status.
7. Use `Quality` to review evaluation results and model comparisons.

The bundled starter database gives you sample content, a saved preset, and sample run history immediately after first install.

## Data Locations

```text
.env                         local secrets and runtime settings, ignored by Git
data/api-cost-x.db           main runtime SQLite database, ignored by Git
data/user_local/             local generated/sample artifacts, ignored by Git
logs/                        runtime logs, ignored by Git
api/app/seed/api-cost-x.seed.db  sanitized bundled base database, committed
```

If you want to reset to the bundled starter database, stop APICostX and move `data/api-cost-x.db` out of the way before running `./start.sh` again. Keep a backup if you need your previous local history.

## Verify The Install

After `./install.sh`, run:

```bash
scripts/validate.sh
scripts/smoke.sh
```

`scripts/validate.sh` checks shell syntax, repository hygiene, secret patterns, and the bundled base database. `scripts/smoke.sh` starts the API service briefly, checks health endpoints, and builds the web GUI.

## Common Problems

- `node is required` or `Node.js 20+ is required`: install Node.js 20+ or rerun `./install.sh` without `API_COST_X_SKIP_SYSTEM_DEPS=1` on an apt-based Linux system.
- `Missing .env`: run `./install.sh`, or copy `.env.example` to `.env`.
- Port already in use: stop the conflicting process, or change `API_COST_X_API_PORT` / `API_COST_X_WEB_PORT` in `.env`.
- Provider calls fail: confirm the matching provider key is present in `.env`, then restart APICostX.
- Browser cannot reach the app: confirm `./start.sh` is still running and open `http://127.0.0.1:5173`.

## Repository Hygiene

Do not commit local secrets, local runtime databases, generated exports, logs, `.venv/`, `node_modules/`, or built web assets. The only committed database is the sanitized bundled base database at `api/app/seed/api-cost-x.seed.db`.

Before publishing changes, run:

```bash
scripts/validate.sh
scripts/smoke.sh
```

The GitHub workflow `.github/workflows/public-release-hygiene.yml` runs the same repository hygiene checks and builds the web GUI on pushes and pull requests.
