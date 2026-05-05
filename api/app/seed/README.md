# Bundled Base Database

This directory contains the sanitized base SQLite database for first-time self-hosted installs. The installer copies `api-cost-x.seed.db` to the configured main runtime database, normally `data/api-cost-x.db`, only when that runtime database does not already exist.

Runtime data remains under `data/` and is ignored by Git. The committed DB is reviewed with `scripts/check-base-database.py` and must not contain credentials, provider keys, cost-tracking tables, metering logs, or production-only user data.
