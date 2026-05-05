"""
Schema Bootstrap — creates all normalized result tables in a user database.

Called during session initialization so every user DB has the correct schema.
All statements use CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
so this is fully idempotent: safe to call repeatedly.

NEVER add JSON/TEXT blob columns here. Every data point gets its own column.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# DDL — executed once per user DB
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- ── Schema version tracking ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- ── Core runs table indexes (backfill for existing DBs) ───────────────────
-- These mirror the index=True flags on the Run SQLAlchemy model.
-- CREATE INDEX IF NOT EXISTS is idempotent — safe to run on every open.
CREATE INDEX IF NOT EXISTS ix_runs_preset_id   ON runs(preset_id);
CREATE INDEX IF NOT EXISTS ix_runs_status      ON runs(status);
CREATE INDEX IF NOT EXISTS ix_runs_created_at  ON runs(created_at);

-- ── Generated documents ───────────────────────────────────────────────────
-- One row per document produced by any generator (fpf / gptr / dr / combine).
CREATE TABLE IF NOT EXISTS run_generated_docs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    doc_id           TEXT    NOT NULL,
    source_doc_id    TEXT    NOT NULL,
    generator        TEXT    NOT NULL,   -- 'fpf' | 'gptr' | 'dr' | 'combine'
    model            TEXT    NOT NULL,
    iteration        INTEGER NOT NULL DEFAULT 1,
    duration_seconds REAL,
    started_at       DATETIME,
    completed_at     DATETIME,
    file_path        TEXT,
    UNIQUE (run_id, doc_id),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rgd_run_id     ON run_generated_docs(run_id);
CREATE INDEX IF NOT EXISTS idx_rgd_run_source ON run_generated_docs(run_id, source_doc_id);

-- ── Evaluation scores ─────────────────────────────────────────────────────
-- One row per (run, doc, criterion, judge, trial).
-- This table is the SINGLE source of truth for all evaluation data.
-- Nothing downstream may pre-compute or cache scores outside this table.
CREATE TABLE IF NOT EXISTS run_eval_scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL,
    doc_id       TEXT    NOT NULL,
    source_doc_id TEXT   NOT NULL,
    criterion    TEXT    NOT NULL,
    judge_model  TEXT    NOT NULL,
    trial        INTEGER NOT NULL DEFAULT 1,
    score        INTEGER NOT NULL,   -- 1–5 integer only
    reason       TEXT,
    scored_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, doc_id, criterion, judge_model, trial),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY (run_id, doc_id) REFERENCES run_generated_docs(run_id, doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_res_run_id      ON run_eval_scores(run_id);
CREATE INDEX IF NOT EXISTS idx_res_run_doc     ON run_eval_scores(run_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_res_run_doc_crit ON run_eval_scores(run_id, doc_id, criterion);

-- ── Pairwise results ──────────────────────────────────────────────────────
-- One row per pairwise comparison (pre-combine or post-combine).
CREATE TABLE IF NOT EXISTS run_pairwise_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    source_doc_id    TEXT    NOT NULL,
    doc_id_a         TEXT    NOT NULL,
    doc_id_b         TEXT    NOT NULL,
    winner_doc_id    TEXT,             -- NULL if tie
    judge_model      TEXT    NOT NULL,
    trial            INTEGER NOT NULL DEFAULT 1,
    reason           TEXT,
    comparison_type  TEXT    NOT NULL DEFAULT 'pre_combine',  -- 'pre_combine' | 'post_combine'
    compared_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, source_doc_id, doc_id_a, doc_id_b, judge_model, trial, comparison_type),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rpr_run_id   ON run_pairwise_results(run_id);
CREATE INDEX IF NOT EXISTS idx_rpr_run_src  ON run_pairwise_results(run_id, source_doc_id);
CREATE INDEX IF NOT EXISTS idx_rpr_run_type ON run_pairwise_results(run_id, comparison_type);

-- ── Timeline events ───────────────────────────────────────────────────────
-- One row per discrete event in the execution timeline.
CREATE TABLE IF NOT EXISTS run_timeline_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    source_doc_id    TEXT,            -- NULL for run-level events
    doc_id           TEXT,            -- NULL for non-doc-specific events
    phase            TEXT    NOT NULL,
    event_type       TEXT    NOT NULL,
    description      TEXT,
    model            TEXT,
    success          BOOLEAN NOT NULL DEFAULT 1,
    duration_seconds REAL,
    details_json     TEXT,            -- small structured extras only (NOT a results blob)
    occurred_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rte_run_id     ON run_timeline_events(run_id);
CREATE INDEX IF NOT EXISTS idx_rte_run_source ON run_timeline_events(run_id, source_doc_id);

-- ── Combined documents ────────────────────────────────────────────────────
-- One row per document produced by the combine phase.
CREATE TABLE IF NOT EXISTS run_combined_docs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    doc_id           TEXT    NOT NULL,
    source_doc_id    TEXT    NOT NULL,
    combine_model    TEXT    NOT NULL,
    combine_strategy TEXT    NOT NULL,
    input_doc_ids    TEXT    NOT NULL,  -- comma-separated list of input doc_ids
    duration_seconds REAL,
    started_at       DATETIME,
    completed_at     DATETIME,
    file_path        TEXT,
    UNIQUE (run_id, doc_id),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rcd_run_id ON run_combined_docs(run_id);

-- ── Source document status ────────────────────────────────────────────────
-- One row per (run, source_doc) tracking pipeline status.
CREATE TABLE IF NOT EXISTS run_source_doc_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    source_doc_id   TEXT    NOT NULL,
    source_doc_name TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    winner_doc_id   TEXT,
    error_message   TEXT,
    started_at      DATETIME,
    completed_at    DATETIME,
    UNIQUE (run_id, source_doc_id),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rsds_run_id ON run_source_doc_status(run_id);

-- ── Run metadata (scalar) ─────────────────────────────────────────────────
-- One row per named scalar value per run (e.g. winner_doc_id, fpf_stats_json).
CREATE TABLE IF NOT EXISTS run_metadata (
    run_id  TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (run_id, key),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

-- ── Run metadata (list) ───────────────────────────────────────────────────
-- One row per list item (e.g. criteria_list, evaluator_list).
CREATE TABLE IF NOT EXISTS run_metadata_list (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT    NOT NULL,
    key      TEXT    NOT NULL,
    position INTEGER NOT NULL,
    value    TEXT    NOT NULL,
    UNIQUE (run_id, key, position),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rml_run_key ON run_metadata_list(run_id, key);
"""


async def bootstrap_user_schema(session: AsyncSession) -> None:
    """
    Create all normalized result tables in the user's SQLite database.

    Safe to call on every session open — uses IF NOT EXISTS throughout.
    Bumps schema_version to CURRENT_SCHEMA_VERSION after first run.
    """
    try:
        # Execute all DDL statements
        for statement in _SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                await session.execute(text(stmt))

        # Record schema version if not already present
        await session.execute(
            text(
                "INSERT OR IGNORE INTO schema_version (version, description) "
                "VALUES (:v, :d)"
            ),
            {"v": 1, "d": "Initial normalized result tables"},
        )

        # ── Schema migration step 2: rebuild run_eval_scores with FK to run_generated_docs ──
        current_version = await get_schema_version(session)
        if current_version < 2:
            logger.info("schema_bootstrap: applying migration step 2 — adding FK run_eval_scores→run_generated_docs")
            await session.execute(text("PRAGMA foreign_keys = OFF"))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS run_eval_scores_v2 (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id       TEXT    NOT NULL,
                    doc_id       TEXT    NOT NULL,
                    source_doc_id TEXT   NOT NULL,
                    criterion    TEXT    NOT NULL,
                    judge_model  TEXT    NOT NULL,
                    trial        INTEGER NOT NULL DEFAULT 1,
                    score        INTEGER NOT NULL,
                    reason       TEXT,
                    scored_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (run_id, doc_id, criterion, judge_model, trial),
                    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY (run_id, doc_id) REFERENCES run_generated_docs(run_id, doc_id) ON DELETE CASCADE
                )
            """))
            await session.execute(text("""
                INSERT OR IGNORE INTO run_eval_scores_v2
                    SELECT * FROM run_eval_scores
            """))
            await session.execute(text("DROP TABLE run_eval_scores"))
            await session.execute(text("ALTER TABLE run_eval_scores_v2 RENAME TO run_eval_scores"))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_res_run_id       ON run_eval_scores(run_id);
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_res_run_doc      ON run_eval_scores(run_id, doc_id);
            """))
            await session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_res_run_doc_crit ON run_eval_scores(run_id, doc_id, criterion);
            """))
            await session.execute(text("PRAGMA foreign_keys = ON"))
            await session.execute(
                text("INSERT OR IGNORE INTO schema_version (version, description) VALUES (:v, :d)"),
                {"v": 2, "d": "Added FK run_eval_scores→run_generated_docs"},
            )
            logger.info("schema_bootstrap: migration step 2 complete")

        await session.commit()
        logger.debug("schema_bootstrap: user DB schema at version %d", CURRENT_SCHEMA_VERSION)

    except Exception as exc:
        logger.error("schema_bootstrap: failed — %s", exc, exc_info=True)
        await session.rollback()
        raise


async def get_schema_version(session: AsyncSession) -> int:
    """Return the current schema version recorded in the user DB, or 0 if not tracked."""
    try:
        result = await session.execute(
            text("SELECT MAX(version) FROM schema_version")
        )
        row = result.scalar()
        return row if row is not None else 0
    except Exception:
        return 0
