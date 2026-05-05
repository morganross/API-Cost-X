"""
Normalized run-result ORM models.

Each class maps to one table defined in schema_bootstrap.py.
Column names, types, and constraints intentionally match the DDL exactly —
do not change one without changing the other.

NO JSON/TEXT blob columns in this file. Every data point has its own column.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import NormalizedBase


# ---------------------------------------------------------------------------
# Generated documents
# ---------------------------------------------------------------------------

class RunGeneratedDoc(NormalizedBase):
    """
    One row per document produced by any generator (fpf / gptr / dr / combine / aiq).

    UNIQUE on (run_id, doc_id) — a doc_id is globally unique within a run.
    """
    __tablename__ = "run_generated_docs"
    __table_args__ = (
        UniqueConstraint("run_id", "doc_id", name="uq_rgd_run_doc"),
    )

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:           Mapped[str]            = mapped_column(String(36),  nullable=False, index=True)
    doc_id:           Mapped[str]            = mapped_column(String(255), nullable=False)
    source_doc_id:    Mapped[str]            = mapped_column(String(255), nullable=False)
    generator:        Mapped[str]            = mapped_column(String(32),  nullable=False)  # fpf | gptr | dr | combine | aiq
    model:            Mapped[str]            = mapped_column(String(128), nullable=False)
    iteration:        Mapped[int]            = mapped_column(Integer,     nullable=False, default=1)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float,      nullable=True)
    started_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    file_path:        Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)

    def __repr__(self) -> str:
        return f"<RunGeneratedDoc(run_id={self.run_id}, doc_id={self.doc_id}, generator={self.generator})>"


# ---------------------------------------------------------------------------
# Evaluation scores
# ---------------------------------------------------------------------------

class RunEvalScore(NormalizedBase):
    """
    One row per (run, doc, criterion, judge_model, trial).

    This is the SINGLE source of truth for all evaluation score data.
    Score must be an integer 1–5; enforcement is in ResultsWriter, not here.
    """
    __tablename__ = "run_eval_scores"
    __table_args__ = (
        UniqueConstraint("run_id", "doc_id", "criterion", "judge_model", "trial",
                         name="uq_res_run_doc_crit_judge_trial"),
    )

    id:            Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:        Mapped[str]            = mapped_column(String(36),  nullable=False, index=True)
    doc_id:        Mapped[str]            = mapped_column(String(255), nullable=False)
    source_doc_id: Mapped[str]            = mapped_column(String(255), nullable=False)
    criterion:     Mapped[str]            = mapped_column(String(255), nullable=False)
    judge_model:   Mapped[str]            = mapped_column(String(128), nullable=False)
    trial:         Mapped[int]            = mapped_column(Integer,     nullable=False, default=1)
    score:         Mapped[int]            = mapped_column(Integer,     nullable=False)   # 1–5
    reason:        Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)
    scored_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RunEvalScore(run_id={self.run_id}, doc_id={self.doc_id}, "
            f"criterion={self.criterion}, judge={self.judge_model}, score={self.score})>"
        )


# ---------------------------------------------------------------------------
# Pairwise results
# ---------------------------------------------------------------------------

class RunPairwiseResult(NormalizedBase):
    """
    One row per pairwise comparison between two generated documents.

    comparison_type: 'pre_combine' or 'post_combine'.
    winner_doc_id is NULL for a tie.
    """
    __tablename__ = "run_pairwise_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "source_doc_id", "doc_id_a", "doc_id_b",
            "judge_model", "trial", "comparison_type",
            name="uq_rpr_unique_comparison",
        ),
    )

    id:              Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:          Mapped[str]           = mapped_column(String(36),  nullable=False, index=True)
    source_doc_id:   Mapped[str]           = mapped_column(String(255), nullable=False)
    doc_id_a:        Mapped[str]           = mapped_column(String(255), nullable=False)
    doc_id_b:        Mapped[str]           = mapped_column(String(255), nullable=False)
    winner_doc_id:   Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    judge_model:     Mapped[str]           = mapped_column(String(128), nullable=False)
    trial:           Mapped[int]           = mapped_column(Integer,     nullable=False, default=1)
    reason:          Mapped[Optional[str]] = mapped_column(Text,        nullable=True)
    comparison_type: Mapped[str]           = mapped_column(String(32),  nullable=False, default="pre_combine")
    compared_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RunPairwiseResult(run_id={self.run_id}, "
            f"a={self.doc_id_a}, b={self.doc_id_b}, winner={self.winner_doc_id})>"
        )


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------

class RunTimelineEvent(NormalizedBase):
    """
    One row per discrete event in a run's execution timeline.

    source_doc_id and doc_id may be NULL for run-level events.
    details_json is permitted for small structured extras (NOT a results blob).
    """
    __tablename__ = "run_timeline_events"

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:           Mapped[str]            = mapped_column(String(36),  nullable=False, index=True)
    source_doc_id:    Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    doc_id:           Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    phase:            Mapped[str]            = mapped_column(String(64),  nullable=False)
    event_type:       Mapped[str]            = mapped_column(String(64),  nullable=False)
    description:      Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)
    model:            Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    success:          Mapped[bool]           = mapped_column(Boolean,     nullable=False, default=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float,     nullable=True)
    details_json:     Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)  # small extras only
    occurred_at:      Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RunTimelineEvent(run_id={self.run_id}, phase={self.phase}, "
            f"event_type={self.event_type}, success={self.success})>"
        )


# ---------------------------------------------------------------------------
# Combined documents
# ---------------------------------------------------------------------------

class RunCombinedDoc(NormalizedBase):
    """
    One row per document produced by the combine phase.

    input_doc_ids stores the comma-separated list of input doc_ids
    (matches the DDL; kept as TEXT since this is structural metadata, not result data).
    """
    __tablename__ = "run_combined_docs"
    __table_args__ = (
        UniqueConstraint("run_id", "doc_id", name="uq_rcd_run_doc"),
    )

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:           Mapped[str]            = mapped_column(String(36),  nullable=False, index=True)
    doc_id:           Mapped[str]            = mapped_column(String(255), nullable=False)
    source_doc_id:    Mapped[str]            = mapped_column(String(255), nullable=False)
    combine_model:    Mapped[str]            = mapped_column(String(128), nullable=False)
    combine_strategy: Mapped[str]            = mapped_column(String(64),  nullable=False)
    input_doc_ids:    Mapped[str]            = mapped_column(Text,        nullable=False)  # comma-separated
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float,     nullable=True)
    started_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    file_path:        Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)

    def __repr__(self) -> str:
        return f"<RunCombinedDoc(run_id={self.run_id}, doc_id={self.doc_id}, model={self.combine_model})>"


# ---------------------------------------------------------------------------
# Source document status
# ---------------------------------------------------------------------------

class RunSourceDocStatus(NormalizedBase):
    """
    One row per (run, source_doc) tracking pipeline progress.

    UNIQUE on (run_id, source_doc_id) — use INSERT OR REPLACE semantics via
    RunResultsRepository.upsert_source_doc_status().
    """
    __tablename__ = "run_source_doc_status"
    __table_args__ = (
        UniqueConstraint("run_id", "source_doc_id", name="uq_rsds_run_source"),
    )

    id:               Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:           Mapped[str]            = mapped_column(String(36),  nullable=False, index=True)
    source_doc_id:    Mapped[str]            = mapped_column(String(255), nullable=False)
    source_doc_name:  Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)
    status:           Mapped[str]            = mapped_column(String(32),  nullable=False, default="pending")
    winner_doc_id:    Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    error_message:    Mapped[Optional[str]]  = mapped_column(Text,        nullable=True)
    started_at:       Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at:     Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<RunSourceDocStatus(run_id={self.run_id}, "
            f"source_doc_id={self.source_doc_id}, status={self.status})>"
        )


# ---------------------------------------------------------------------------
# Run metadata — scalar key/value
# ---------------------------------------------------------------------------

class RunMetadata(NormalizedBase):
    """
    One row per named scalar value per run.

    Examples: winner_doc_id, fpf_version, combine_strategy.
    PRIMARY KEY is (run_id, key) — use INSERT OR REPLACE to update.
    """
    __tablename__ = "run_metadata"

    run_id: Mapped[str] = mapped_column(String(36),  nullable=False, primary_key=True)
    key:    Mapped[str] = mapped_column(String(255), nullable=False, primary_key=True)
    value:  Mapped[str] = mapped_column(Text,        nullable=False)

    def __repr__(self) -> str:
        return f"<RunMetadata(run_id={self.run_id}, key={self.key})>"


# ---------------------------------------------------------------------------
# Run metadata — list items
# ---------------------------------------------------------------------------

class RunMetadataList(NormalizedBase):
    """
    One row per list item per run.

    Examples: criteria_list, evaluator_list.
    UNIQUE on (run_id, key, position).
    """
    __tablename__ = "run_metadata_list"
    __table_args__ = (
        UniqueConstraint("run_id", "key", "position", name="uq_rml_run_key_pos"),
    )

    id:       Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id:   Mapped[str] = mapped_column(String(36),  nullable=False)
    key:      Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer,     nullable=False)
    value:    Mapped[str] = mapped_column(Text,        nullable=False)

    def __repr__(self) -> str:
        return f"<RunMetadataList(run_id={self.run_id}, key={self.key}, pos={self.position})>"
