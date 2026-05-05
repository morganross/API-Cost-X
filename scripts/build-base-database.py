#!/usr/bin/env python3
"""Build the sanitized bundled base SQLite DB from a reviewed legacy seed DB.

The output database uses the current self-hosted schema, keeps saved presets and
sample run history, normalizes ownership to the single local user, and excludes
legacy cost-tracking tables/columns.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
DEFAULT_OUTPUT_DB = ROOT / "api" / "app" / "seed" / "api-cost-x.seed.db"
DEFAULT_OUTPUT_ARTIFACTS = ROOT / "api" / "app" / "seed" / "artifacts"
LOCAL_USER_UUID = "local"

COPY_TABLES = [
    "contents",
    "documents",
    "github_connections",
    "presets",
    "runs",
    "tasks",
    "artifacts",
    "run_generated_docs",
    "run_eval_scores",
    "run_pairwise_results",
    "run_timeline_events",
    "run_combined_docs",
    "run_source_doc_status",
    "run_metadata",
    "run_metadata_list",
    "user_settings",
]

FORBIDDEN_SOURCE_TABLES = {
    "provider_keys",
    "llm_call_events",
    "run_cost_entries",
    "run_cost_rollups",
    "usage_stats",
}

COST_OR_SECRET_KEY_RE = re.compile(
    r"(cost|price|pricing|billing|metering|api[_-]?key|secret|password|private[_-]?key|encrypted[_-]?key|token_encrypted)",
    re.IGNORECASE,
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")]


def sanitize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_json_value(item)
            for key, item in value.items()
            if not COST_OR_SECRET_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    return value


def sanitize_json_text(value: str) -> str:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(sanitize_json_value(parsed), separators=(",", ":"))


def safe_generated_relative_path(file_path: Any, doc_id: Any) -> str | None:
    if not file_path:
        if not doc_id:
            return None
        safe_doc_id = str(doc_id).replace(":", "_").replace("/", "_").replace("\\", "_")
        return f"generated/{safe_doc_id}.md"
    raw = Path(str(file_path))
    if raw.is_absolute() or ".." in raw.parts:
        safe_doc_id = str(doc_id or raw.stem).replace(":", "_").replace("/", "_").replace("\\", "_")
        return f"generated/{safe_doc_id}.md"
    return raw.as_posix()


def sanitize_cell(table: str, column: str, value: Any, row: sqlite3.Row) -> Any:
    if column == "user_uuid":
        return LOCAL_USER_UUID
    if column in {"config", "config_overrides", "variables", "tags", "documents", "scores", "details_json"} and isinstance(value, str):
        return sanitize_json_text(value)
    if table in {"run_generated_docs", "run_combined_docs"} and column == "file_path":
        return safe_generated_relative_path(value, row["doc_id"] if "doc_id" in row.keys() else None)
    return value


async def create_current_schema(output_db: Path) -> None:
    sys.path.insert(0, str(API_DIR))
    os.environ["API_COST_X_DATABASE_URL"] = f"sqlite+aiosqlite:///{output_db}"
    os.environ.setdefault("API_COST_X_DATA_DIR", str(output_db.parent))

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.infra.db.models import Base
    from app.infra.db.schema_bootstrap import bootstrap_user_schema

    engine = create_async_engine(f"sqlite+aiosqlite:///{output_db}", future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            await bootstrap_user_schema(session)
    finally:
        await engine.dispose()


def copy_compatible_rows(source_db: Path, output_db: Path) -> dict[str, int]:
    source = sqlite3.connect(source_db)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(output_db)
    target.row_factory = sqlite3.Row
    counts: dict[str, int] = {}

    try:
        source_tables = {
            row[0]
            for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        forbidden_present = sorted(source_tables & FORBIDDEN_SOURCE_TABLES)
        if forbidden_present:
            print("Skipping forbidden legacy tables: " + ", ".join(forbidden_present))

        target.execute("PRAGMA foreign_keys=OFF")
        for table in COPY_TABLES:
            if not table_exists(source, table) or not table_exists(target, table):
                continue
            source_cols = table_columns(source, table)
            target_cols = table_columns(target, table)
            common_cols = [col for col in target_cols if col in source_cols]
            if not common_cols:
                continue

            placeholders = ", ".join("?" for _ in common_cols)
            insert_sql = (
                f"INSERT OR IGNORE INTO {quote_identifier(table)} "
                f"({', '.join(quote_identifier(col) for col in common_cols)}) "
                f"VALUES ({placeholders})"
            )
            before = target.total_changes
            for row in source.execute(f"SELECT * FROM {quote_identifier(table)}"):
                if table == "run_metadata" and str(row["key"] or "").startswith("seed_source_"):
                    continue
                values = [sanitize_cell(table, col, row[col], row) for col in common_cols]
                target.execute(insert_sql, values)
            counts[table] = target.total_changes - before

        target.execute("DELETE FROM user_meta")
        target.execute(
            """
            INSERT INTO user_meta
                (id, uuid, seed_status, seed_version, seeded_at, created_at, updated_at)
            VALUES
                (?, ?, 'ready', 'base-2026-05-05', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
            """,
            ("local-seed-meta", LOCAL_USER_UUID),
        )
        counts["user_meta"] = 1

        target.execute("PRAGMA foreign_keys=ON")
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key violations in sanitized base DB: {violations[:10]}")
        target.commit()
        target.execute("VACUUM")
        return counts
    finally:
        source.close()
        target.close()


def copy_generated_artifacts(source_db: Path, source_artifacts: Path, output_artifacts: Path) -> int:
    if output_artifacts.exists():
        shutil.rmtree(output_artifacts)
    if not source_artifacts.exists():
        return 0

    conn = sqlite3.connect(source_db)
    conn.row_factory = sqlite3.Row
    copied = 0
    try:
        if not table_exists(conn, "run_generated_docs"):
            return 0
        for row in conn.execute("SELECT run_id, doc_id, file_path FROM run_generated_docs"):
            rel_path = safe_generated_relative_path(row["file_path"], row["doc_id"])
            if not rel_path:
                continue
            source = source_artifacts / "runs" / str(row["run_id"]) / rel_path
            if not source.exists():
                continue
            target = output_artifacts / "user_local" / "runs" / str(row["run_id"]) / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    finally:
        conn.close()
    return copied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_db", type=Path, help="Reviewed legacy seed.db to sanitize")
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--source-artifacts", type=Path, default=None)
    parser.add_argument("--output-artifacts", type=Path, default=DEFAULT_OUTPUT_ARTIFACTS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_db = args.source_db.resolve()
    output_db = args.output_db.resolve()
    source_artifacts = (args.source_artifacts or (source_db.parent / "seed_artifacts")).resolve()
    output_artifacts = args.output_artifacts.resolve()

    if not source_db.exists():
        print(f"Source DB does not exist: {source_db}", file=sys.stderr)
        return 1

    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()

    asyncio.run(create_current_schema(output_db))
    counts = copy_compatible_rows(source_db, output_db)
    artifact_count = copy_generated_artifacts(source_db, source_artifacts, output_artifacts)

    print(f"Wrote sanitized base DB: {output_db}")
    for table in sorted(counts):
        print(f"{table}: {counts[table]}")
    print(f"generated_artifacts: {artifact_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
