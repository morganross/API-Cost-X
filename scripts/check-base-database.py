#!/usr/bin/env python3
"""Validate the bundled base SQLite DB before public release."""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE_DB = ROOT / "api" / "app" / "seed" / "api-cost-x.seed.db"

FORBIDDEN_TABLES = {
    "provider_keys",
    "llm_call_events",
    "run_cost_entries",
    "run_cost_rollups",
    "usage_stats",
}

FORBIDDEN_COLUMN_RE = re.compile(
    r"(^|_)(cost|price|pricing|billing|metering)(_|$)|encrypted_key|token_encrypted",
    re.IGNORECASE,
)

SECRET_PATTERNS = {
    "openai_project_key": re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "github_pat": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "google_ai_key": re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"(A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}"),
    "npm_token": re.compile(r"npm_[A-Za-z0-9]{36}"),
    "pypi_token": re.compile(r"pypi-[A-Za-z0-9_-]{50,}"),
    "cloudflare_token": re.compile(
        r"(CF_API_TOKEN|CF_AUTH_KEY|CLOUDFLARE_API_TOKEN|CLOUDFLARE_AUTH_KEY)[^A-Za-z0-9]{0,20}[A-Za-z0-9_-]{20,}",
        re.IGNORECASE,
    ),
    "generic_secret_assignment": re.compile(
        r"(api[_-]?token|auth[_-]?token|secret[_-]?key|client[_-]?secret)[\"' ]*[:=][\"' ]*[A-Za-z0-9_./+=-]{24,}",
        re.IGNORECASE,
    ),
    "oci_tenancy": re.compile(r"tenancy\s*=\s*ocid1\.", re.IGNORECASE),
    "oci_user": re.compile(r"user\s*=\s*ocid1\.user", re.IGNORECASE),
    "oci_key_file": re.compile(r"(private_key_file|key_file)\s*=", re.IGNORECASE),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "oci_private_key": re.compile(r"-----BEGIN OCI API KEY-----"),
}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def main() -> int:
    if not BASE_DB.exists():
        print(f"Bundled base DB is missing: {BASE_DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(BASE_DB)
    conn.row_factory = sqlite3.Row
    failures: list[str] = []
    try:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        forbidden_tables = sorted(set(tables) & FORBIDDEN_TABLES)
        if forbidden_tables:
            failures.append("forbidden tables: " + ", ".join(forbidden_tables))

        for table in tables:
            if table.startswith("sqlite_"):
                continue
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")]
            forbidden_columns = [col for col in columns if FORBIDDEN_COLUMN_RE.search(col)]
            if forbidden_columns:
                failures.append(f"{table}: forbidden columns: {', '.join(forbidden_columns)}")

            for row in conn.execute(f"SELECT * FROM {quote_identifier(table)}"):
                for col in columns:
                    value = row[col]
                    if value is None:
                        continue
                    text = value.decode("utf-8", "ignore") if isinstance(value, bytes) else str(value)
                    for name, pattern in SECRET_PATTERNS.items():
                        if pattern.search(text):
                            failures.append(f"{table}.{col}: secret-like value matched {name}")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            failures.append(f"foreign key violations: {violations[:10]}")

        if failures:
            print("base database validation failed", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
            return 1

        print("base database validation passed")
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0]
            print(f"{table}: {count}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
