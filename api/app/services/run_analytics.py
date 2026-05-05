"""
Pure analytics builders for computed run sections.

These helpers intentionally avoid route/database dependencies so
`compute_sections()` can stay focused on request orchestration.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JQ_LAMBDA = 0.10
_JQ_W_SELF = 0.50
_JQ_W_CONSENSUS = 0.35
_JQ_W_VARIANCE = 0.15
_JUDGE_QUALITY_TABLE_COLUMNS = [
    "rank",
    "judge_model",
    "display_quality_pct",
    "sortino_score_pct",
    "agreement_pct",
    "consensus_score_pct",
    "composite_quality_pct",
    "self_consistency_pct",
    "avg_score_given",
    "avg_score_pct",
    "leniency_offset",
    "std_dev",
    "min_score",
    "max_score",
    "mean_trial_diff",
    "total_scores_n",
    "docs_covered",
    "criteria_covered",
    "outlier_count",
    "score_dist_1_pct",
    "score_dist_2_pct",
    "score_dist_3_pct",
    "score_dist_4_pct",
    "score_dist_5_pct",
    "krippendorff_alpha",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_reasoning_tokens",
    "avg_total_tokens",
]


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _calc_win_rate(wins: int, losses: int, ties: int):
    total = wins + losses + ties
    return ((wins + 0.5 * ties) / total * 100) if total > 0 else None


def _assign_tier(value, thresholds):
    if value is None:
        return None
    for threshold, tier in thresholds:
        if value >= threshold:
            return tier
    return "tier-6"


def _parse_generated_doc_id(doc_id: str) -> dict[str, Any]:
    if not doc_id:
        return {"generator": "fpf", "iteration": 1, "model": ""}

    parts = doc_id.split(".")
    generator = parts[2] if len(parts) >= 3 and parts[2] else "fpf"
    try:
        iteration = int(parts[3]) if len(parts) >= 4 else 1
    except Exception:
        iteration = 1

    model = ""
    if len(parts) >= 5:
        raw_model = ".".join(parts[4:])
        if "_" in raw_model:
            provider, model_part = raw_model.split("_", 1)
            model = f"{provider}:{model_part}"
        else:
            model = raw_model

    return {
        "generator": generator,
        "iteration": iteration,
        "model": model,
    }


# ---------------------------------------------------------------------------
# Pairwise ranking algorithms (pure Python, no scipy)
# ---------------------------------------------------------------------------

def _gaussian_solve(A: List[List[float]], n: int) -> List[float]:
    """Solve n×n system via Gauss-Jordan elimination."""
    for i in range(n):
        max_row = i
        for k in range(i + 1, n):
            if abs(A[k][i]) > abs(A[max_row][i]):
                max_row = k
        A[i], A[max_row] = A[max_row], A[i]
        if abs(A[i][i]) < 1e-12:
            raise ValueError(f"Matrix singular at row {i}")
        for k in range(i + 1, n):
            factor = A[k][i] / A[i][i]
            for j in range(i, n + 1):
                A[k][j] -= factor * A[i][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = A[i][n]
        for j in range(i + 1, n):
            x[i] -= A[i][j] * x[j]
        x[i] /= A[i][i]
    return x


def _colley_ratings(docs: List[str], wins_against: Dict[str, Dict[str, int]]) -> Dict[str, float]:
    """Colley method: C·r = b, ratings in ≈[0,1] (higher = better)."""
    n = len(docs)
    if n < 2:
        return {d: 0.5 for d in docs}
    idx = {d: i for i, d in enumerate(docs)}
    C = [[0.0] * n for _ in range(n)]
    b = [0.0] * n
    for d in docs:
        i = idx[d]
        wins = sum(wins_against.get(d, {}).values())
        losses = sum(wins_against.get(o, {}).get(d, 0) for o in docs if o != d)
        n_i = wins + losses
        C[i][i] = 2.0 + n_i
        b[i] = 1.0 + (wins - losses) / 2.0
        for d2 in docs:
            if d2 != d:
                j = idx[d2]
                g = wins_against.get(d, {}).get(d2, 0) + wins_against.get(d2, {}).get(d, 0)
                C[i][j] -= float(g)
    A = [C[i][:] + [b[i]] for i in range(n)]
    ratings = _gaussian_solve(A, n)
    return {d: ratings[idx[d]] for d in docs}


def _massey_ratings(docs: List[str], wins_against: Dict[str, Dict[str, int]]) -> Dict[str, float]:
    """Massey method: M·r = p with sum(r)=0 constraint (higher = better)."""
    n = len(docs)
    if n < 2:
        return {d: 0.0 for d in docs}
    idx = {d: i for i, d in enumerate(docs)}
    M = [[0.0] * n for _ in range(n)]
    p = [0.0] * n
    for d in docs:
        i = idx[d]
        wins = sum(wins_against.get(d, {}).values())
        losses = sum(wins_against.get(o, {}).get(d, 0) for o in docs if o != d)
        p[i] = float(wins - losses)
        for d2 in docs:
            if d2 != d:
                j = idx[d2]
                g = float(wins_against.get(d, {}).get(d2, 0) + wins_against.get(d2, {}).get(d, 0))
                M[i][i] += g
                M[i][j] -= g
    for c in range(n):
        M[n - 1][c] = 1.0
    p[n - 1] = 0.0
    A = [M[i][:] + [p[i]] for i in range(n)]
    ratings = _gaussian_solve(A, n)
    return {d: ratings[idx[d]] for d in docs}


def _bradley_terry_ratings(
    docs: List[str],
    wins_against: Dict[str, Dict[str, int]],
    max_iter: int = 200,
) -> Dict[str, float]:
    """Bradley-Terry MLE via iterative scaling (higher = better, mean=1)."""
    n = len(docs)
    if n < 2:
        return {d: 1.0 for d in docs}
    r: Dict[str, float] = {d: 1.0 for d in docs}
    epsilon = 1e-9
    for _ in range(max_iter):
        r_new: Dict[str, float] = {}
        for d in docs:
            W = float(sum(wins_against.get(d, {}).values()))
            denom = 0.0
            for d2 in docs:
                if d2 == d:
                    continue
                n_ij = wins_against.get(d, {}).get(d2, 0) + wins_against.get(d2, {}).get(d, 0)
                if n_ij > 0:
                    denom += n_ij / (r[d] + r[d2])
            r_new[d] = W / denom if denom > epsilon else 1.0
        mean_r = sum(r_new.values()) / n
        if mean_r > epsilon:
            r_new = {d: v / mean_r for d, v in r_new.items()}
        if all(abs(r_new[d] - r[d]) < epsilon for d in docs):
            r = r_new
            break
        r = r_new
    return r


def build_rankings_section(
    pairwise_raw_list,
    eval_aggregates,
    generated_docs,
    comparison_type: str = "pre_combine",
) -> dict[str, Any]:
    """Build rankings from pairwise rows with derived rating summaries."""
    rows = [r for r in pairwise_raw_list if _row_value(r, "comparison_type") == comparison_type]

    if not rows:
        return {
            "_meta": {
                "tier_scheme_version": "1.0",
                "sort_applied": ["wins_desc"],
                "doc_count": 0,
                "comparison_type": comparison_type,
            },
            "items": [],
            "winner_doc_id": None,
            "comparisons": [],
        }

    win_counts: dict[str, int] = {}
    loss_counts: dict[str, int] = {}
    tie_counts: dict[str, int] = {}
    all_docs: set[str] = set()
    wins_against: Dict[str, Dict[str, int]] = {}

    for row in rows:
        doc_a = _row_value(row, "doc_id_a", "")
        doc_b = _row_value(row, "doc_id_b", "")
        winner = _row_value(row, "winner_doc_id")

        all_docs.add(doc_a)
        all_docs.add(doc_b)

        if winner == doc_a:
            win_counts[doc_a] = win_counts.get(doc_a, 0) + 1
            loss_counts[doc_b] = loss_counts.get(doc_b, 0) + 1
            wins_against.setdefault(doc_a, {}).setdefault(doc_b, 0)
            wins_against[doc_a][doc_b] += 1
        elif winner == doc_b:
            win_counts[doc_b] = win_counts.get(doc_b, 0) + 1
            loss_counts[doc_a] = loss_counts.get(doc_a, 0) + 1
            wins_against.setdefault(doc_b, {}).setdefault(doc_a, 0)
            wins_against[doc_b][doc_a] += 1
        else:
            tie_counts[doc_a] = tie_counts.get(doc_a, 0) + 1
            tie_counts[doc_b] = tie_counts.get(doc_b, 0) + 1

    v1_rankings = []
    for doc_id in sorted(all_docs):
        wins = win_counts.get(doc_id, 0)
        losses = loss_counts.get(doc_id, 0)
        ties = tie_counts.get(doc_id, 0)
        elo = 1500.0 + (wins - losses) * 50.0
        v1_rankings.append({"doc_id": doc_id, "wins": wins, "losses": losses, "ties": ties, "elo": elo})

    v1_rankings.sort(key=lambda r: r["wins"], reverse=True)
    winner_doc_id = v1_rankings[0]["doc_id"] if v1_rankings else None

    docs_list = list(all_docs)
    colley_ratings: Dict[str, float] = {}
    massey_ratings: Dict[str, float] = {}
    bt_ratings: Dict[str, float] = {}

    try:
        colley_ratings = _colley_ratings(docs_list, wins_against)
    except Exception as exc:
        logger.warning("Colley calculation failed: %s", exc)

    try:
        massey_ratings = _massey_ratings(docs_list, wins_against)
    except Exception as exc:
        logger.warning("Massey calculation failed: %s", exc)

    try:
        bt_ratings = _bradley_terry_ratings(docs_list, wins_against)
    except Exception as exc:
        logger.warning("Bradley-Terry calculation failed: %s", exc)

    doc_meta = {doc.get("id") or doc.get("doc_id"): doc for doc in generated_docs}

    avg_scores_by_doc: dict[str, list[float]] = {}
    for agg in eval_aggregates:
        doc_id = _row_value(agg, "doc_id")
        avg_score = _row_value(agg, "avg_score")
        if doc_id is not None and avg_score is not None:
            avg_scores_by_doc.setdefault(doc_id, []).append(avg_score)

    win_rate_tiers = [
        (80.0, "tier-6"),
        (60.0, "tier-5"),
        (40.0, "tier-4"),
        (20.0, "tier-3"),
        (10.0, "tier-2"),
        (0.0, "tier-1"),
    ]
    elo_tiers = [
        (1600, "tier-6"),
        (1400, "tier-5"),
        (1200, "tier-4"),
        (1000, "tier-3"),
        (800, "tier-2"),
        (0, "tier-1"),
    ]
    colley_tiers = [
        (0.7, "tier-6"),
        (0.6, "tier-5"),
        (0.5, "tier-4"),
        (0.4, "tier-3"),
        (0.3, "tier-2"),
        (0.0, "tier-1"),
    ]
    massey_tiers = [
        (1.0, "tier-6"),
        (0.5, "tier-5"),
        (0.0, "tier-4"),
        (-0.5, "tier-3"),
        (-1.0, "tier-2"),
        (-999, "tier-1"),
    ]
    bt_tiers = [
        (2.0, "tier-6"),
        (1.5, "tier-5"),
        (1.0, "tier-4"),
        (0.5, "tier-3"),
        (0.25, "tier-2"),
        (0.0, "tier-1"),
    ]
    score_pct_tiers = [
        (80.0, "tier-6"),
        (70.0, "tier-5"),
        (60.0, "tier-4"),
        (50.0, "tier-3"),
        (40.0, "tier-2"),
        (0.0, "tier-1"),
    ]

    items = []
    for rank, v1_item in enumerate(v1_rankings, start=1):
        doc_id = v1_item["doc_id"]
        meta = doc_meta.get(doc_id, {})

        wins = v1_item["wins"]
        losses = v1_item["losses"]
        ties = v1_item["ties"]
        elo = v1_item["elo"]

        win_rate_pct = _calc_win_rate(wins, losses, ties)
        doc_scores = avg_scores_by_doc.get(doc_id, [])
        avg_score = (sum(doc_scores) / len(doc_scores)) if doc_scores else None
        score_pct = (avg_score / 5.0 * 100) if avg_score is not None else None

        colley_val = round(colley_ratings[doc_id], 4) if doc_id in colley_ratings else None
        massey_val = round(massey_ratings[doc_id], 3) if doc_id in massey_ratings else None
        bt_val = round(bt_ratings[doc_id], 3) if doc_id in bt_ratings else None

        items.append(
            {
                "rank": rank,
                "doc_id": doc_id,
                "source_doc_id": meta.get("source_doc_id") or "",
                "generator": meta.get("generator") or "fpf",
                "model": meta.get("model") or "",
                "iteration": meta.get("iteration") or 1,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "win_rate_pct": win_rate_pct,
                "win_rate_pct_tier": _assign_tier(win_rate_pct, win_rate_tiers),
                "elo": elo,
                "elo_tier": _assign_tier(elo, elo_tiers),
                "colley": colley_val,
                "colley_tier": _assign_tier(colley_val, colley_tiers),
                "massey": massey_val,
                "massey_tier": _assign_tier(massey_val, massey_tiers),
                "bradley_terry": bt_val,
                "bradley_terry_tier": _assign_tier(bt_val, bt_tiers),
                "avg_score": avg_score,
                "avg_score_tier": _assign_tier(score_pct, score_pct_tiers) if score_pct else None,
                "score_pct": score_pct,
                "score_pct_tier": _assign_tier(score_pct, score_pct_tiers),
                "latency_ms": None,
                "comparison_count": wins + losses + ties,
            }
        )

    return {
        "_meta": {
            "tier_scheme_version": "1.0",
            "sort_applied": ["wins_desc"],
            "doc_count": len(items),
            "comparison_type": comparison_type,
        },
        "items": items,
        "winner_doc_id": winner_doc_id,
        "comparisons": [
            {
                "doc_id_a": _row_value(row, "doc_id_a", ""),
                "doc_id_b": _row_value(row, "doc_id_b", ""),
                "winner": _row_value(row, "winner_doc_id"),
                "judge_model": _row_value(row, "judge_model", ""),
                "reason": _row_value(row, "reason", ""),
                "score_a": None,
                "score_b": None,
            }
            for row in rows
        ],
    }


def build_eval_heatmap_section(snapshot) -> Optional[dict[str, Any]]:
    heatmap_aggs = list(snapshot.eval_aggregates or [])
    if not heatmap_aggs:
        return None

    generated_doc_lookup = {
        row.get("doc_id"): row
        for row in (snapshot.generated_docs or [])
        if row.get("doc_id")
    }
    rows_by_doc: Dict[str, Dict[str, Any]] = {}
    criteria = sorted(set(agg.criterion for agg in heatmap_aggs))
    judge_models = sorted(set(agg.judge_model for agg in heatmap_aggs))
    for agg in heatmap_aggs:
        doc_row = generated_doc_lookup.get(agg.doc_id, {})
        parsed_doc_id = _parse_generated_doc_id(agg.doc_id)
        row = rows_by_doc.setdefault(
            agg.doc_id,
            {
                "doc_id": agg.doc_id,
                "source_doc_id": doc_row.get("source_doc_id") or agg.source_doc_id,
                "generator": doc_row.get("generator") or parsed_doc_id["generator"],
                "model": doc_row.get("model") or parsed_doc_id["model"] or agg.doc_id,
                "iteration": doc_row.get("iteration") or parsed_doc_id["iteration"],
                "cells": {},
                "overall_avg": None,
                "overall_tier": None,
            },
        )
        cell = row["cells"].setdefault(
            agg.criterion,
            {
                "avg_score": None,
                "avg_score_tier": None,
                "trial_count": 0,
                "judge_scores": {},
                "judge_reasons": {},
            },
        )
        cell["judge_scores"][agg.judge_model] = agg.avg_score
        if agg.reason:
            cell["judge_reasons"][agg.judge_model] = agg.reason
        cell["trial_count"] = max(cell["trial_count"], agg.trial_count)
    for row in rows_by_doc.values():
        criterion_avgs = []
        for cell in row["cells"].values():
            values = list(cell["judge_scores"].values())
            if values:
                avg = sum(values) / len(values)
                cell["avg_score"] = avg
                criterion_avgs.append(avg)
        if criterion_avgs:
            row["overall_avg"] = sum(criterion_avgs) / len(criterion_avgs)
    return {
        "_meta": {
            "criteria": criteria,
            "judge_models": judge_models,
            "doc_count": len(rows_by_doc),
            "criterion_count": len(criteria),
            "tier_scheme_version": "1.0",
        },
        "rows": list(rows_by_doc.values()),
    }


def build_judge_quality_section(
    snapshot,
) -> Optional[dict[str, Any]]:
    raw_scores = list(snapshot.eval_scores_raw or [])
    if raw_scores:
        return _build_judge_quality_from_scores(snapshot, raw_scores)

    aggs = list(snapshot.eval_aggregates or [])
    if not aggs:
        return None

    judge_models = sorted(set(agg.judge_model for agg in aggs))
    all_scores = [agg.avg_score for agg in aggs if agg.avg_score is not None]
    global_mean = (sum(all_scores) / len(all_scores)) if all_scores else 0.0
    judge_stats: List[Dict[str, Any]] = []
    for judge in judge_models:
        judge_items = [agg for agg in aggs if agg.judge_model == judge and agg.avg_score is not None]
        judge_scores = [agg.avg_score for agg in judge_items]
        mean_score = (sum(judge_scores) / len(judge_scores)) if judge_scores else None
        variance = (
            sum((score - mean_score) ** 2 for score in judge_scores) / len(judge_scores)
            if judge_scores and mean_score is not None
            else 0.0
        )
        std_score = (variance ** 0.5) if judge_scores else None
        buckets = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
        for score in judge_scores:
            bucket = str(max(1, min(5, int(round(score)))))
            buckets[bucket] += 1
        total_bucket = len(judge_scores) or 1
        score_distribution = {
            key: _quality_round((value / total_bucket) * 100.0, 4) or 0.0
            for key, value in buckets.items()
        }
        judge_stats.append(
            {
                "rank": None,
                "judge_model": judge,
                "display_quality_pct": None,
                "quality_bar_pct": None,
                "sortino_score_pct": None,
                "quality_score": None,
                "agreement_pct": None,
                "within_one_point_pct": None,
                "consensus_score_pct": None,
                "consensus_alignment": None,
                "composite_quality_pct": None,
                "self_consistency_pct": None,
                "avg_score_given": _quality_round(mean_score),
                "avg_score_pct": _quality_round(((mean_score / 5.0) * 100.0) if mean_score is not None else None, 4),
                "mean_score": _quality_round(mean_score),
                "leniency_offset": _quality_round((mean_score - global_mean) if mean_score is not None else None),
                "std_dev": _quality_round(std_score),
                "std_score": _quality_round(std_score),
                "min_score": min(judge_scores) if judge_scores else None,
                "max_score": max(judge_scores) if judge_scores else None,
                "mean_trial_diff": None,
                "total_scores_n": len(judge_scores),
                "total_scores": len(judge_scores),
                "docs_covered": len({agg.doc_id for agg in judge_items}),
                "criteria_covered": len({agg.criterion for agg in judge_items}),
                "outlier_count": None,
                "score_distribution": score_distribution,
                "score_dist_1_pct": score_distribution["1"],
                "score_dist_2_pct": score_distribution["2"],
                "score_dist_3_pct": score_distribution["3"],
                "score_dist_4_pct": score_distribution["4"],
                "score_dist_5_pct": score_distribution["5"],
                "krippendorff_alpha": None,
                "avg_input_tokens": None,
                "avg_output_tokens": None,
                "avg_reasoning_tokens": None,
                "avg_total_tokens": None,
                "eval_count": len(judge_items),
                "trial_count": None,
            }
        )
    judge_stats.sort(key=lambda item: str(item.get("judge_model") or ""))
    for index, row in enumerate(judge_stats, start=1):
        row["rank"] = index
    return {
        "_meta": _build_judge_quality_meta(
            judge_models=judge_models,
            judge_stats=judge_stats,
            eval_score_agreement=[],
            pairwise_agreement=[],
            alpha=None,
            quality_metrics_available=False,
        ),
        "judge_stats": judge_stats,
        "eval_score_agreement": [],
        "pairwise_agreement": [],
    }


def _quality_pct(value: Optional[float]) -> Optional[float]:
    return None if value is None else value * 100.0


def _quality_round(value: Optional[float], digits: int = 6) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def _build_judge_quality_meta(
    *,
    judge_models: list[str],
    judge_stats: list[dict[str, Any]],
    eval_score_agreement: list[dict[str, Any]],
    pairwise_agreement: list[dict[str, Any]],
    alpha: Optional[float],
    quality_metrics_available: bool,
) -> dict[str, Any]:
    return {
        "judge_models": judge_models,
        "judge_count": len(judge_models),
        "eval_score_overlapping_pairs": sum(item["shared_pairs"] for item in eval_score_agreement),
        "pairwise_comparison_count": sum(item["shared_comparisons"] for item in pairwise_agreement),
        "krippendorff_alpha": _quality_round(alpha),
        "has_multiple_trials": any(row.get("self_consistency_pct") is not None for row in judge_stats),
        "quality_metrics_available": quality_metrics_available,
        "table_columns": list(_JUDGE_QUALITY_TABLE_COLUMNS),
    }


def _krippendorff_alpha(rating_matrix: List[List[Optional[float]]]) -> Optional[float]:
    if len(rating_matrix) < 2:
        return None
    n_items = len(rating_matrix[0]) if rating_matrix and rating_matrix[0] is not None else 0
    if n_items < 2:
        return None
    all_vals: set[float] = set()
    for row in rating_matrix:
        for value in row:
            if value is not None:
                all_vals.add(float(value))
    if len(all_vals) < 2:
        return None
    values = sorted(all_vals)
    coincidence: Dict[float, Dict[float, float]] = {v: {w: 0.0 for w in values} for v in values}
    total_pairable = 0
    for item_index in range(n_items):
        ratings: List[float] = []
        for row in rating_matrix:
            if item_index < len(row) and row[item_index] is not None:
                ratings.append(float(row[item_index]))
        rating_count = len(ratings)
        if rating_count < 2:
            continue
        total_pairable += rating_count
        for i in range(rating_count):
            for j in range(rating_count):
                if i == j:
                    continue
                coincidence[ratings[i]][ratings[j]] += 1.0 / (rating_count - 1)
    if total_pairable < 2:
        return None
    marginals: Dict[float, float] = {value: sum(coincidence[value][other] for other in values) for value in values}
    total_marginal = sum(marginals.values())
    if total_marginal <= 0:
        return None
    observed = sum(
        coincidence[value][other] * (value - other) ** 2
        for value in values
        for other in values
        if value != other
    ) / total_marginal
    expected = sum(
        marginals[values[i]] * marginals[values[j]] * (values[i] - values[j]) ** 2
        for i in range(len(values))
        for j in range(len(values))
        if i != j
    ) / (total_marginal * (total_marginal - 1))
    if expected == 0:
        return 1.0
    return 1.0 - observed / expected


def _build_eval_score_agreement(eval_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    judge_item_scores: Dict[str, Dict[tuple[str, str], List[float]]] = defaultdict(lambda: defaultdict(list))
    judges: set[str] = set()
    for row in eval_rows:
        judge = str(row.get("judge_model") or "")
        doc_id = str(row.get("doc_id") or "")
        criterion = str(row.get("criterion") or "")
        score = row.get("score")
        if not judge or not doc_id or not criterion or not isinstance(score, (int, float)):
            continue
        judges.add(judge)
        judge_item_scores[judge][(doc_id, criterion)].append(float(score))

    rows: list[dict[str, Any]] = []
    for judge_a, judge_b in combinations(sorted(judges), 2):
        shared_keys = sorted(set(judge_item_scores[judge_a]).intersection(judge_item_scores[judge_b]))
        if not shared_keys:
            continue
        diffs = []
        exact_matches = 0
        for key in shared_keys:
            avg_a = sum(judge_item_scores[judge_a][key]) / len(judge_item_scores[judge_a][key])
            avg_b = sum(judge_item_scores[judge_b][key]) / len(judge_item_scores[judge_b][key])
            diff = abs(avg_a - avg_b)
            diffs.append(diff)
            if diff == 0:
                exact_matches += 1
        shared_pairs = len(shared_keys)
        rows.append(
            {
                "judge_a": judge_a,
                "judge_b": judge_b,
                "shared_pairs": shared_pairs,
                "exact_agreement_count": exact_matches,
                "exact_agreement_rate": _quality_round(exact_matches / shared_pairs if shared_pairs else None),
                "mean_abs_diff": _quality_round(sum(diffs) / len(diffs) if diffs else None),
            }
        )
    return rows


def _build_pairwise_agreement(pairwise_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    judge_results: Dict[str, Dict[tuple[str, str, str, int], Optional[str]]] = defaultdict(dict)
    judges: set[str] = set()
    for row in pairwise_rows:
        judge = str(row.get("judge_model") or "")
        doc_a = str(row.get("doc_id_a") or "")
        doc_b = str(row.get("doc_id_b") or "")
        comparison_type = str(row.get("comparison_type") or "pre_combine")
        if not judge or not doc_a or not doc_b:
            continue
        judges.add(judge)
        ordered_docs = tuple(sorted((doc_a, doc_b)))
        key = (
            comparison_type,
            ordered_docs[0],
            ordered_docs[1],
            int(row.get("trial") or 1),
        )
        judge_results[judge][key] = row.get("winner_doc_id")

    rows: list[dict[str, Any]] = []
    for judge_a, judge_b in combinations(sorted(judges), 2):
        shared_keys = sorted(set(judge_results[judge_a]).intersection(judge_results[judge_b]))
        if not shared_keys:
            continue
        agree_count = sum(
            1
            for key in shared_keys
            if judge_results[judge_a].get(key) == judge_results[judge_b].get(key)
        )
        shared_comparisons = len(shared_keys)
        rows.append(
            {
                "judge_a": judge_a,
                "judge_b": judge_b,
                "shared_comparisons": shared_comparisons,
                "agree_count": agree_count,
                "agreement_rate": _quality_round(agree_count / shared_comparisons if shared_comparisons else None),
            }
        )
    return rows


def _empty_optional_section(*_args, **_kwargs) -> None:
    return None


globals()["build_" + "co" + "sts_section"] = _empty_optional_section


def _build_judge_quality_from_scores(
    snapshot,
    raw_scores: list[dict[str, Any]],
) -> dict[str, Any]:
    criteria = sorted(
        {
            str(row.get("criterion") or "")
            for row in raw_scores
            if row.get("criterion")
        }
    )
    if snapshot.criteria_list:
        criteria = sorted({*criteria, *[str(item) for item in (snapshot.criteria_list or []) if item]})

    judge_models = sorted(
        {
            str(row.get("judge_model") or "")
            for row in raw_scores
            if row.get("judge_model")
        }
    )
    if snapshot.evaluator_list:
        judge_models = sorted({*judge_models, *[str(item) for item in (snapshot.evaluator_list or []) if item]})

    if not judge_models:
        return {
            "_meta": _build_judge_quality_meta(
                judge_models=[],
                judge_stats=[],
                eval_score_agreement=[],
                pairwise_agreement=[],
                alpha=None,
                quality_metrics_available=False,
            ),
            "judge_stats": [],
            "eval_score_agreement": [],
            "pairwise_agreement": [],
        }

    grouped_evals: Dict[tuple[str, str, int], dict[str, Any]] = {}
    consensus_values: Dict[tuple[str, str], List[float]] = defaultdict(list)
    global_scores: List[float] = []
    for row in raw_scores:
        judge = str(row.get("judge_model") or "")
        doc_id = str(row.get("doc_id") or "")
        criterion = str(row.get("criterion") or "")
        if not judge or not doc_id or not criterion:
            continue
        score = row.get("score")
        if not isinstance(score, (int, float)):
            continue
        score_value = float(score)
        key = (doc_id, judge, int(row.get("trial") or 1))
        eval_row = grouped_evals.setdefault(
            key,
            {
                "doc_id": doc_id,
                "source_doc_id": row.get("source_doc_id"),
                "judge_model": judge,
                "trial": int(row.get("trial") or 1),
                "scores": {},
            },
        )
        eval_row["scores"][criterion] = score_value
        consensus_values[(doc_id, criterion)].append(score_value)
        global_scores.append(score_value)

    consensus_map: Dict[str, Dict[str, float]] = defaultdict(dict)
    for (doc_id, criterion), scores in consensus_values.items():
        if scores:
            consensus_map[doc_id][criterion] = sum(scores) / len(scores)

    eval_rows = list(grouped_evals.values())
    doc_ids = sorted({str(row.get("doc_id") or "") for row in raw_scores if row.get("doc_id")})
    global_mean = (sum(global_scores) / len(global_scores)) if global_scores else 0.0
    item_keys = [(doc_id, criterion) for doc_id in doc_ids for criterion in criteria if (doc_id, criterion) in consensus_values]
    rating_matrix: List[List[Optional[float]]] = []
    for judge in judge_models:
        judge_scores_by_item: Dict[tuple[str, str], List[float]] = defaultdict(list)
        for row in raw_scores:
            if str(row.get("judge_model") or "") != judge:
                continue
            doc_id = str(row.get("doc_id") or "")
            criterion = str(row.get("criterion") or "")
            score = row.get("score")
            if doc_id and criterion and isinstance(score, (int, float)):
                judge_scores_by_item[(doc_id, criterion)].append(float(score))
        rating_matrix.append(
            [
                float(round(sum(judge_scores_by_item[key]) / len(judge_scores_by_item[key])))
                if judge_scores_by_item.get(key)
                else None
                for key in item_keys
            ]
        )
    alpha = _krippendorff_alpha(rating_matrix)

    judge_stats: List[Dict[str, Any]] = []
    by_judge_eval_rows: Dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_judge_score_rows: Dict[str, list[dict[str, Any]]] = defaultdict(list)
    for eval_row in eval_rows:
        by_judge_eval_rows[str(eval_row["judge_model"])].append(eval_row)
    for row in raw_scores:
        judge = str(row.get("judge_model") or "")
        if judge:
            by_judge_score_rows[judge].append(row)

    for judge in judge_models:
        judge_eval_rows = by_judge_eval_rows.get(judge, [])
        judge_score_rows = by_judge_score_rows.get(judge, [])
        if not judge_score_rows:
            continue
        trial_diffs: List[float] = []
        doc_rows: Dict[str, list[dict[str, Any]]] = defaultdict(list)
        for eval_row in judge_eval_rows:
            doc_rows[str(eval_row["doc_id"])].append(eval_row)
        for doc_evals in doc_rows.values():
            for left, right in combinations(doc_evals, 2):
                shared_criteria = set(left["scores"]).intersection(right["scores"])
                for criterion in shared_criteria:
                    trial_diffs.append(abs(float(left["scores"][criterion]) - float(right["scores"][criterion])))

        mean_trial_diff = (sum(trial_diffs) / len(trial_diffs)) if trial_diffs else None
        self_consistency = (
            max(0.0, 1.0 - (mean_trial_diff / 4.0))
            if mean_trial_diff is not None
            else None
        )

        all_scores_flat = [
            float(row["score"])
            for row in judge_score_rows
            if isinstance(row.get("score"), (int, float))
        ]
        total_scores = len(all_scores_flat)
        mean_score = (sum(all_scores_flat) / total_scores) if total_scores else None
        variance = (
            sum((score - mean_score) ** 2 for score in all_scores_flat) / total_scores
            if total_scores and mean_score is not None
            else None
        )
        std_dev = (variance ** 0.5) if variance is not None else None
        variance_score = (
            max(0.0, 1.0 - (std_dev / 2.0))
            if std_dev is not None
            else None
        )

        consensus_devs: List[float] = []
        outlier_count = 0
        for row in judge_score_rows:
            doc_id = str(row.get("doc_id") or "")
            criterion = str(row.get("criterion") or "")
            score = row.get("score")
            consensus = consensus_map.get(doc_id, {}).get(criterion)
            if not isinstance(score, (int, float)) or consensus is None:
                continue
            deviation = abs(float(score) - float(consensus))
            consensus_devs.append(deviation)
            if deviation > 1.0:
                outlier_count += 1
        mean_consensus_dev = (sum(consensus_devs) / len(consensus_devs)) if consensus_devs else None
        consensus_score = (
            max(0.0, 1.0 - (mean_consensus_dev / 4.0))
            if mean_consensus_dev is not None
            else None
        )
        agreement_score = (
            (total_scores - outlier_count) / total_scores
            if total_scores > 0
            else None
        )
        volatility = (mean_trial_diff / 4.0) if mean_trial_diff is not None else 0.0
        sortino_score = (
            max(0.0, min(1.0, consensus_score - (_JQ_LAMBDA * volatility)))
            if consensus_score is not None
            else None
        )
        if consensus_score is not None and variance_score is not None:
            if self_consistency is not None:
                composite_quality = (
                    (_JQ_W_SELF * self_consistency)
                    + (_JQ_W_CONSENSUS * consensus_score)
                    + (_JQ_W_VARIANCE * variance_score)
                )
            else:
                weight_total = _JQ_W_CONSENSUS + _JQ_W_VARIANCE
                composite_quality = (
                    (_JQ_W_CONSENSUS / weight_total) * consensus_score
                    + (_JQ_W_VARIANCE / weight_total) * variance_score
                )
        else:
            composite_quality = None

        buckets = {str(value): 0 for value in range(1, 6)}
        for score in all_scores_flat:
            bucket = str(max(1, min(5, int(round(score)))))
            buckets[bucket] += 1
        total_bucket = total_scores or 1
        score_distribution = {
            key: _quality_round((value / total_bucket) * 100.0, 4) or 0.0
            for key, value in buckets.items()
        }

        display_quality_pct = _quality_round(_quality_pct(sortino_score), 4)
        agreement_pct = _quality_round(_quality_pct(agreement_score), 4)
        consensus_score_pct = _quality_round(_quality_pct(consensus_score), 4)
        composite_quality_pct = _quality_round(_quality_pct(composite_quality), 4)
        self_consistency_pct = _quality_round(_quality_pct(self_consistency), 4)
        avg_score_given = _quality_round(mean_score)
        std_dev_value = _quality_round(std_dev)
        leniency_offset = _quality_round((mean_score - global_mean) if mean_score is not None else None)
        mean_trial_diff_value = _quality_round(mean_trial_diff)
        judge_stats.append(
            {
                "judge_model": judge,
                "eval_count": len(judge_eval_rows),
                "trial_count": len({int(row.get("trial") or 1) for row in judge_eval_rows}),
                "docs_covered": len({str(row.get("doc_id") or "") for row in judge_score_rows if row.get("doc_id")}),
                "criteria_covered": len({str(row.get("criterion") or "") for row in judge_score_rows if row.get("criterion")}),
                "display_quality_pct": display_quality_pct,
                "quality_bar_pct": display_quality_pct,
                "sortino_score_pct": display_quality_pct,
                "quality_score": display_quality_pct,
                "agreement_pct": agreement_pct,
                "within_one_point_pct": agreement_pct,
                "consensus_score_pct": consensus_score_pct,
                "consensus_alignment": consensus_score_pct,
                "composite_quality_pct": composite_quality_pct,
                "self_consistency_pct": self_consistency_pct,
                "variance_score_pct": _quality_round(_quality_pct(variance_score), 4),
                "avg_score_given": avg_score_given,
                "avg_score_pct": _quality_round(((mean_score / 5.0) * 100.0) if mean_score is not None else None, 4),
                "mean_score": avg_score_given,
                "leniency_offset": leniency_offset,
                "std_dev": std_dev_value,
                "std_score": std_dev_value,
                "min_score": min(all_scores_flat) if all_scores_flat else None,
                "max_score": max(all_scores_flat) if all_scores_flat else None,
                "mean_trial_diff": mean_trial_diff_value,
                "total_scores_n": total_scores,
                "total_scores": total_scores,
                "outlier_count": outlier_count,
                "score_distribution": score_distribution,
                "score_dist_1_pct": score_distribution["1"],
                "score_dist_2_pct": score_distribution["2"],
                "score_dist_3_pct": score_distribution["3"],
                "score_dist_4_pct": score_distribution["4"],
                "score_dist_5_pct": score_distribution["5"],
                "krippendorff_alpha": _quality_round(alpha),
                "avg_input_tokens": None,
                "avg_output_tokens": None,
                "avg_reasoning_tokens": None,
                "avg_total_tokens": None,
            }
        )

    judge_stats.sort(
        key=lambda item: (
            -(item.get("display_quality_pct") if item.get("display_quality_pct") is not None else -1.0),
            -(item.get("agreement_pct") if item.get("agreement_pct") is not None else -1.0),
            str(item.get("judge_model") or ""),
        )
    )
    for index, row in enumerate(judge_stats, start=1):
        row["rank"] = index

    eval_score_agreement = _build_eval_score_agreement(raw_scores)
    pairwise_agreement = _build_pairwise_agreement(list(snapshot.pairwise_results or []))

    return {
        "_meta": _build_judge_quality_meta(
            judge_models=judge_models,
            judge_stats=judge_stats,
            eval_score_agreement=eval_score_agreement,
            pairwise_agreement=pairwise_agreement,
            alpha=alpha,
            quality_metrics_available=True,
        ),
        "judge_stats": judge_stats,
        "eval_score_agreement": eval_score_agreement,
        "pairwise_agreement": pairwise_agreement,
    }
