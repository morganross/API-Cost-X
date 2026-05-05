# Public Copy Policy

This repo is intended to become the public, self-hosted version of ACM/API-Cost-X.

The private web-gui and api repos under `/home/ubuntu/acm-oss/sources/` are reference material only. Do not copy them wholesale into this repo.

## Hard Rules

1. Do not SSH into the production web-gui or api servers for this work.
2. Do all development on `shdn` in `/home/ubuntu/acm-oss/work/API-Cost-X`.
3. Copy code by whitelist, not by whole repo or broad directory copy.
4. Do not preserve private repo Git history in this public repo.
5. Do not commit real credentials, tokens, keys, runtime databases, unsanitized run artifacts, generated bundles, logs, backups, or production docs. The only database exception is the reviewed bundled base DB under `api/app/seed/`.
6. If a file is ambiguous, leave it out until reviewed.

## Always Exclude

- `.git/` from source repos
- `.env`, `.env.*`, and any real local config file
- `.git-credentials`, `.netrc`, GitHub CLI auth files, SSH keys, PEM files, certificates
- SQLite runtime databases and runtime data
- unsanitized private SQLite databases
- logs, uploaded files, generated exports, metering artifacts
- web-gui build output and api generated output
- backup files and backup directories
- private operational docs, incident reports, infrastructure runbooks, and live deployment notes

## Initial Whitelist

Web GUI candidates:
- `ui/src/` after removing external-site bootstrap, account flows, production telemetry, and private endpoint assumptions
- `ui/package.json`, `ui/package-lock.json`, TypeScript/Vite/Tailwind/PostCSS config after review
- public-only static assets that are not generated snapshots

API candidates:
- selected FastAPI application code after removing account flows, multi-account tenancy, and production site contracts
- selected FilePromptForge source files after reviewing configs and provider handling
- dependency manifests after review

New repo native files:
- `.gitignore`
- `.env.example`
- installer/start scripts
- local-only docs
- secret scanning scripts and CI

## Required Rewrite Before Public Commit

- Replace external-site bootstrap with local web-gui configuration.
- Remove production-only header contracts from OSS mode.
- Use one local single-user plain SQLite database.
- Replace provider-key storage with local user-provided configuration.
- Sanitize any sample data into the bundled base DB and strip credentials, private user metadata, and cost-tracking data.
- Regenerate web-gui bundles from public source only.

## Commit Gate

Before every commit, run:

```bash
scripts/validate.sh
git status --short
```

Any scanner hit must be removed or intentionally documented as a false positive before commit.
