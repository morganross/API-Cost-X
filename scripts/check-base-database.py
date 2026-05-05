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
    "google_ai_key": re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
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
