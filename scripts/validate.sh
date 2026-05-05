#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== shell syntax =="
bash -n install.sh
bash -n start.sh
for script in scripts/*.sh; do
  bash -n "$script"
done

echo "== repository validation =="
scripts/scan-secrets.sh
python3 scripts/check-base-database.py

echo "validation passed"
