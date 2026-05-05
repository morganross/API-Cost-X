"""
ResultsReader — read facade over RunResultsRepository for API response assembly
and export generation.

Does NOT read from runs.results_summary.  All data comes from normalized tables.
Returns typed dicts and lists ready for use in helpers.py and export_service.py.

No writes happen here.  Exceptions are logged and surfaced as empty results
rather than propagated to callers, so a missing table never 500s an API response.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.repositories.run_results import RunResultsRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed return types used by API helpers
# ---------------------------------------------------------------------------

@dataclass
class EvalAggregate:  # noqa: keep reason field for tooltip passthrough
    """Average score for a (doc, criterion, judge) combination."""
    doc_id: str
    source_doc_id: str
    criterion: str
    judge_model: str
    avg_score: float
    trial_count: int
    reason: Optional[str] = None


@dataclass
class RunResultsSnapshot:
    """
    All normalized result data for a single run, assembled through one
    coordinated read path.

    Used by the canonical detail/export read path to build API responses
    without touching results_summary.
    """
    generated_docs: list[dict[str, Any]] = field(default_factory=list)
    eval_aggregates: list[EvalAggregate] = field(default_factory=list)
    eval_scores_raw: list[dict[str, Any]] = field(default_factory=list)
    pairwise_results: list[dict[str, Any]] = field(default_factory=list)
    timeline_events: list[dict[str, Any]] = field(default_factory=list)
    combined_docs: list[dict[str, Any]] = field(default_factory=list)
    source_doc_statuses: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    criteria_list: list[str] = field(default_factory=list)
    evaluator_list: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ResultsReader
# ---------------------------------------------------------------------------

class ResultsReader:
    """
    Read facade for all normalized run-result data.

    Instantiate per-request or per-background-task, bound to a user AsyncSession.

    Example:
        reader = ResultsReader(session)
        snapshot = await reader.get_run_snapshot(run_id)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = RunResultsRepository(session)

    _ALL_SNAPSHOT_DATASETS = frozenset(
        {
            "generated_docs",
            "eval_aggregates",
            "eval_scores_raw",
            "pairwise_results",
            "timeline_events",
            "combined_docs",
            "source_doc_statuses",
            "metadata",
            "criteria_list",
            "evaluator_list",
        }
    )

    # -----------------------------------------------------------------------
    # Full snapshot (used by canonical detail assembly and export)
    # -----------------------------------------------------------------------

    async def get_run_snapshot(self, run_id: str) -> RunResultsSnapshot:
        return await self.get_run_snapshot_for_datasets(
            run_id,
            datasets=self._ALL_SNAPSHOT_DATASETS,
        )

    async def get_run_snapshot_for_datasets(
        self,
        run_id: str,
        *,
        datasets: set[str] | frozenset[str],
        source_doc_id: Optional[str] = None,
    ) -> RunResultsSnapshot:
        """
        Fetch only the requested result datasets for a run.

        On partial failure, affected fields are left empty and errors are logged.
        """
        snapshot = RunResultsSnapshot()

        async def _safe(coro, attr: str):
            try:
                return await coro
            except Exception as exc:
                logger.error(
                    "[ResultsReader] failed to fetch %s for run=%s: %s",
                    attr, run_id, exc, exc_info=True,
                )
                return None

        requests: list[tuple[str, Any]] = []
        dataset_names = set(datasets or set())

        if "generated_docs" in dataset_names:
            requests.append(
                (
                    "generated_docs",
                    self._repo.get_generated_docs(run_id, source_doc_id=source_doc_id),
                )
            )
        if "eval_aggregates" in dataset_names:
            requests.append(
                (
                    "eval_aggregates",
                    self._repo.get_eval_aggregates(run_id, source_doc_id=source_doc_id),
                )
            )
        if "eval_scores_raw" in dataset_names:
            requests.append(
                (
                    "eval_scores_raw",
                    self._repo.get_eval_scores(run_id, source_doc_id=source_doc_id),
                )
            )
        if "pairwise_results" in dataset_names:
            requests.append(
                (
                    "pairwise_results",
                    self._repo.get_pairwise_results(run_id, source_doc_id=source_doc_id),
                )
            )
        if "timeline_events" in dataset_names:
            requests.append(
                (
                    "timeline_events",
                    self._repo.get_timeline_events(run_id, source_doc_id=source_doc_id),
                )
            )
        if "combined_docs" in dataset_names:
            requests.append(
                (
                    "combined_docs",
                    self._repo.get_combined_docs(run_id, source_doc_id=source_doc_id),
                )
            )
        if "source_doc_statuses" in dataset_names:
            requests.append(("source_doc_statuses", self._repo.get_source_doc_statuses(run_id)))
        if "metadata" in dataset_names:
            requests.append(("metadata", self._repo.get_metadata(run_id)))
        if "criteria_list" in dataset_names:
            requests.append(("criteria_list", self._repo.get_metadata_list(run_id, "criteria_list")))
        if "evaluator_list" in dataset_names:
            requests.append(("evaluator_list", self._repo.get_metadata_list(run_id, "evaluator_list")))

        values: list[Any] = []
        for attr, coro in requests:
            # A single AsyncSession is not a safe fan-out boundary. Keep these
            # reads serialized until the repository exposes a coordinated
            # snapshot/export read contract.
            values.append(await _safe(coro, attr))

        fetched = {attr: value for (attr, _), value in zip(requests, values)}

        gen_docs = fetched.get("generated_docs")
        agg = fetched.get("eval_aggregates")
        raw_scores = fetched.get("eval_scores_raw")
        pairwise = fetched.get("pairwise_results")
        timeline = fetched.get("timeline_events")
        combined = fetched.get("combined_docs")
        statuses = fetched.get("source_doc_statuses")
        meta = fetched.get("metadata")
        criteria = fetched.get("criteria_list")
        evaluators = fetched.get("evaluator_list")

        if gen_docs is not None:
            snapshot.generated_docs = [_orm_to_dict(d) for d in gen_docs]

        if agg is not None:
            snapshot.eval_aggregates = [
                EvalAggregate(
                    doc_id=row["doc_id"],
                    source_doc_id=row["source_doc_id"],
                    criterion=row["criterion"],
                    judge_model=row["judge_model"],
                    avg_score=float(row["avg_score"] or 0),
                    trial_count=int(row["trial_count"] or 0),
                    reason=row.get("reason") or None,
                    )
                for row in agg
                if not source_doc_id or row["source_doc_id"] == source_doc_id
            ]

        if raw_scores is not None:
            snapshot.eval_scores_raw = [_orm_to_dict(s) for s in raw_scores]

        if pairwise is not None:
            snapshot.pairwise_results = [_orm_to_dict(p) for p in pairwise]

        if timeline is not None:
            snapshot.timeline_events = [_orm_to_dict(t) for t in timeline]

        if combined is not None:
            snapshot.combined_docs = [_orm_to_dict(c) for c in combined]


        if statuses is not None:
            snapshot.source_doc_statuses = [
                _orm_to_dict(s)
                for s in statuses
                if not source_doc_id or getattr(s, "source_doc_id", None) == source_doc_id
            ]

        if meta is not None:
            snapshot.metadata = meta

        if criteria is not None:
            snapshot.criteria_list = criteria

        if evaluators is not None:
            snapshot.evaluator_list = evaluators

        return snapshot

    async def get_eval_avg_by_doc(self, run_id: str) -> dict[str, float]:
        """
        Return {doc_id: overall_average_score} for all evaluated docs in this run.

        Used by the executor's get_all_eval_scores() callback to select top-N
        docs for pairwise comparison.  One float per doc — fits in kilobytes.
        """
        from collections import defaultdict

        try:
            snapshot = await self.get_run_snapshot_for_datasets(
                run_id,
                datasets={"eval_scores_raw"},
            )
        except Exception as exc:
            logger.error(
                "[ResultsReader] get_eval_avg_by_doc failed: run=%s %s",
                run_id, exc, exc_info=True,
            )
            return {}

        score_accum: dict[str, list[float]] = defaultdict(list)
        for row in snapshot.eval_scores_raw:
            doc_id = row.get("doc_id")
            score = row.get("score")
            if doc_id and score is not None:
                score_accum[doc_id].append(float(score))

        return {
            doc_id: sum(scores) / len(scores)
            for doc_id, scores in score_accum.items()
            if scores
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _orm_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict (non-recursive)."""
    # Works for mapped classes with __mapper__
    try:
        cols = {c.key for c in obj.__mapper__.column_attrs}
        return {k: getattr(obj, k) for k in cols}
    except AttributeError:
        # Fallback for Row / NamedTuple results
        if hasattr(obj, "_asdict"):
            return obj._asdict()
        return dict(obj)
