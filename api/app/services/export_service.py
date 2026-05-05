"""
Export service: builds a ZIP archive of all run data after completion.

The ZIP contains:
  - run_summary.json          Raw results_summary data
  - README.md                 Human-readable format guide
  - spreadsheets/             9 CSV files covering all data facets
  - reports/                  HTML/MD report files (if present)
  - evaluations/              Generated document markdown files
"""
import asyncio
import csv
import io
import json
import logging
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.results_reader import ResultsReader, RunResultsSnapshot

logger = logging.getLogger(__name__)


def _write_export_zip_sync(
    *,
    export_path: Path,
    run_root: Path,
    run_id: str,
    run_name: str,
    export_rs: Dict[str, Any],
) -> Path:
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=export_path.parent,
        prefix=f".{run_id[:8]}-",
        suffix=".zip.tmp",
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            _add_readme(zf)
            _add_run_summary_json(zf, run_id, run_name, export_rs)
            _add_spreadsheets(zf, run_id, export_rs)
            _add_reports(zf, run_root)
            _add_generated_docs(zf, run_root)

        tmp_path.replace(export_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return export_path


# ---------------------------------------------------------------------------
# Normalized → results_summary dict builder
# ---------------------------------------------------------------------------

async def _build_results_dict_from_normalized(
    session: AsyncSession,
    run_id: str,
) -> Dict[str, Any]:
    """
    Reconstruct a results_summary-shaped dict from normalized tables.

    Returns a dict with exactly the same keys and nesting that the 9 CSV
    builder functions expect, so they need ZERO changes.

    Keys built:
      winner, eval_scores, pre_combine_evals, pre_combine_evals_detailed,
      evaluator_list, criteria_list, pairwise, post_combine_pairwise,
      post_combine_evals, eval_deviations, timeline_events,
      source_doc_results  (nested per SDR)
    """
    reader = ResultsReader(session)
    snapshot = await reader.get_run_snapshot(run_id)
    return _build_results_dict_from_snapshot(snapshot)


def _build_results_dict_from_snapshot(snapshot: RunResultsSnapshot) -> Dict[str, Any]:
    gen_docs_raw = snapshot.generated_docs or []
    eval_scores_raw = snapshot.eval_scores_raw or []
    pairwise_raw = snapshot.pairwise_results or []
    timeline_raw = snapshot.timeline_events or []
    combined_raw = snapshot.combined_docs or []
    source_statuses = snapshot.source_doc_statuses or []
    metadata = snapshot.metadata or {}
    criteria_list = snapshot.criteria_list or []
    evaluator_list = snapshot.evaluator_list or []

    rs: Dict[str, Any] = {}

    # --- winner ---
    rs["winner"] = metadata.get("winner", "")

    # --- evaluator_list / criteria_list ---
    rs["evaluator_list"] = evaluator_list
    rs["criteria_list"] = criteria_list

    # --- Group generated docs by source_doc_id ---
    sdr_gen_docs: Dict[str, list] = defaultdict(list)
    all_gen_doc_meta: Dict[str, Dict] = {}
    for gd in gen_docs_raw:
        info = {
            "id": gd.get("doc_id"),
            "doc_id": gd.get("doc_id"),
            "model": gd.get("model"),
            "generator": gd.get("generator"),
            "iteration": gd.get("iteration"),
            "source_doc_id": gd.get("source_doc_id"),
        }
        if info["source_doc_id"]:
            sdr_gen_docs[info["source_doc_id"]].append(info)
        if info["doc_id"]:
            all_gen_doc_meta[info["doc_id"]] = info

    # --- Build pre_combine_evals_detailed from eval_scores ---
    pce_detailed: Dict[str, Dict] = {}
    for es in eval_scores_raw:
        doc_id = es.get("doc_id")
        if not doc_id:
            continue
        if doc_id not in pce_detailed:
            pce_detailed[doc_id] = {"evaluations": []}
        detail = pce_detailed[doc_id]
        # Find or create the eval entry for this (judge, trial)
        target_eval = None
        for ev in detail["evaluations"]:
            if ev.get("judge_model") == es.get("judge_model") and ev.get("trial") == es.get("trial"):
                target_eval = ev
                break
        if target_eval is None:
            target_eval = {
                "judge_model": es.get("judge_model"),
                "trial": es.get("trial"),
                "scores": [],
            }
            detail["evaluations"].append(target_eval)
        target_eval["scores"].append({
            "criterion": es.get("criterion"),
            "score": es.get("score"),
            "reason": es.get("reason"),
        })
    rs["pre_combine_evals_detailed"] = pce_detailed

    # --- Build pre_combine_evals (per-doc per-criterion avg) ---
    # {doc_id: {criterion: avg_score_1_5}}
    pce: Dict[str, Dict[str, float]] = {}
    score_accum: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for es in eval_scores_raw:
        doc_id = es.get("doc_id")
        criterion = es.get("criterion")
        score = es.get("score")
        if doc_id and criterion and score is not None:
            score_accum[doc_id][criterion].append(score)
    for doc_id, crit_map in score_accum.items():
        pce[doc_id] = {
            crit: sum(vals) / len(vals) for crit, vals in crit_map.items()
        }
    rs["pre_combine_evals"] = pce

    # --- Build eval_scores (per-doc single avg for run_summary sheet) ---
    # {doc_id: avg_score_0_1}
    eval_scores_summary: Dict[str, float] = {}
    for doc_id, crit_map in pce.items():
        vals = list(crit_map.values())
        if vals:
            avg_1_5 = sum(vals) / len(vals)
            eval_scores_summary[doc_id] = avg_1_5 / 5.0
    rs["eval_scores"] = eval_scores_summary

    # --- Build eval_deviations (per-judge deviation from group mean per criterion) ---
    # {judge_model: {criterion: deviation}}
    # Group mean per (doc, criterion): across all judges
    group_mean: Dict[str, Dict[str, float]] = defaultdict(dict)
    for doc_id, crit_map in score_accum.items():
        for crit, vals in crit_map.items():
            group_mean[doc_id][crit] = sum(vals) / len(vals)

    # Per-judge per-criterion scores
    judge_crit_scores: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for es in eval_scores_raw:
        judge_model = es.get("judge_model")
        criterion = es.get("criterion")
        doc_id = es.get("doc_id")
        score = es.get("score")
        if judge_model and criterion and doc_id and score is not None:
            judge_crit_scores[judge_model][criterion].append((doc_id, score))

    eval_deviations: Dict[str, Dict[str, float]] = {}
    for judge, crit_map in judge_crit_scores.items():
        eval_deviations[judge] = {}
        for crit, doc_scores in crit_map.items():
            devs = []
            for doc_id, score in doc_scores:
                gm = group_mean.get(doc_id, {}).get(crit)
                if gm is not None:
                    devs.append(score - gm)
            eval_deviations[judge][crit] = sum(devs) / len(devs) if devs else 0.0
    rs["eval_deviations"] = eval_deviations

    # --- Group pairwise results by source_doc_id and comparison_type ---
    pre_pairwise_by_sdr: Dict[str, list] = defaultdict(list)
    post_pairwise_by_sdr: Dict[str, list] = defaultdict(list)
    for pr in pairwise_raw:
        comparison_type = pr.get("comparison_type")
        source_doc_id = pr.get("source_doc_id") or ""
        target = post_pairwise_by_sdr if comparison_type == "post_combine" else pre_pairwise_by_sdr
        target[source_doc_id].append(pr)

    def _compute_rankings(comparisons) -> list:
        """Aggregate pairwise comparisons into win/loss rankings."""
        wins: Dict[str, int] = defaultdict(int)
        losses: Dict[str, int] = defaultdict(int)
        doc_ids: set = set()
        for c in comparisons:
            doc_id_a = c.get("doc_id_a")
            doc_id_b = c.get("doc_id_b")
            winner_doc_id = c.get("winner_doc_id")
            if not doc_id_a or not doc_id_b:
                continue
            doc_ids.add(doc_id_a)
            doc_ids.add(doc_id_b)
            if winner_doc_id:
                wins[winner_doc_id] += 1
                loser = doc_id_b if winner_doc_id == doc_id_a else doc_id_a
                losses[loser] += 1
        rankings = []
        for did in doc_ids:
            w, l = wins.get(did, 0), losses.get(did, 0)
            total = w + l
            rankings.append({
                "doc_id": did,
                "wins": w,
                "losses": l,
                "score": w / total if total > 0 else 0,
            })
        rankings.sort(key=lambda r: r["score"], reverse=True)
        return rankings

    def _compute_judge_deviations(comparisons) -> dict:
        """Compute per-judge deviation from consensus in pairwise comparisons."""
        # Consensus = most-picked winner per pair
        pair_winners: Dict[tuple, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for c in comparisons:
            doc_id_a = c.get("doc_id_a")
            doc_id_b = c.get("doc_id_b")
            if not doc_id_a or not doc_id_b:
                continue
            pair_key = tuple(sorted([doc_id_a, doc_id_b]))
            winner = c.get("winner_doc_id") or "__TIE__"
            pair_winners[pair_key][winner] += 1

        consensus: Dict[tuple, str] = {}
        for pair_key, votes in pair_winners.items():
            consensus[pair_key] = max(votes, key=votes.get)

        judge_agree: Dict[str, list] = defaultdict(list)
        for c in comparisons:
            doc_id_a = c.get("doc_id_a")
            doc_id_b = c.get("doc_id_b")
            judge_model = c.get("judge_model")
            if not doc_id_a or not doc_id_b or not judge_model:
                continue
            pair_key = tuple(sorted([doc_id_a, doc_id_b]))
            winner = c.get("winner_doc_id") or "__TIE__"
            judge_agree[judge_model].append(1 if winner == consensus.get(pair_key) else 0)

        deviations = {}
        for judge, agrees in judge_agree.items():
            agreement_rate = sum(agrees) / len(agrees) if agrees else 1.0
            deviations[judge] = 1.0 - agreement_rate
        return deviations

    # --- Build run-level pairwise (pre-combine) ---
    all_pre_comparisons = [c for comps in pre_pairwise_by_sdr.values() for c in comps]
    if all_pre_comparisons:
        rs["pairwise"] = {"rankings": _compute_rankings(all_pre_comparisons)}
    else:
        rs["pairwise"] = {}

    # --- Build run-level post_combine_pairwise ---
    all_post_comparisons = [c for comps in post_pairwise_by_sdr.values() for c in comps]
    if all_post_comparisons:
        rs["post_combine_pairwise"] = {"rankings": _compute_rankings(all_post_comparisons)}
    else:
        rs["post_combine_pairwise"] = {}

    # --- Group combined docs by source_doc_id ---
    sdr_combined: Dict[str, list] = defaultdict(list)
    for cd in combined_raw:
        source_doc_id = cd.get("source_doc_id")
        if not source_doc_id:
            continue
        sdr_combined[source_doc_id].append({
            "id": cd.get("doc_id"),
            "doc_id": cd.get("doc_id"),
            "model": cd.get("combine_model"),
            "generator": "combine",
            "iteration": 1,
        })

    # --- Build post_combine_evals ---
    # For combined docs, eval scores may exist in run_eval_scores too.
    combined_doc_ids = {cd.get("doc_id") for cd in combined_raw if cd.get("doc_id")}
    post_combine_evals: Dict[str, Dict[str, float]] = {}
    for es in eval_scores_raw:
        doc_id = es.get("doc_id")
        judge_model = es.get("judge_model")
        score = es.get("score")
        if doc_id in combined_doc_ids:
            post_combine_evals.setdefault(doc_id, {})
            # Accumulate per-judge avg
            if judge_model:
                post_combine_evals[doc_id].setdefault(judge_model, [])
                if isinstance(post_combine_evals[doc_id][judge_model], list) and score is not None:
                    post_combine_evals[doc_id][judge_model].append(score)
    # Convert lists to averages
    for doc_id, judge_map in post_combine_evals.items():
        for judge, scores in judge_map.items():
            if isinstance(scores, list) and scores:
                post_combine_evals[doc_id][judge] = sum(scores) / len(scores) / 5.0
    rs["post_combine_evals"] = post_combine_evals

    # --- Group timeline events by source_doc_id ---
    sdr_timeline: Dict[str, list] = defaultdict(list)
    run_level_timeline: list = []
    for te in timeline_raw:
        te_dict = {
            "phase": te.get("phase"),
            "event_type": te.get("event_type"),
            "description": te.get("description"),
            "model": te.get("model"),
            "timestamp": te.get("occurred_at").isoformat() if te.get("occurred_at") else "",
            "duration_seconds": te.get("duration_seconds"),
            "success": te.get("success"),
            "details": {"source_doc_id": te.get("source_doc_id") or ""},
        }
        if te.get("source_doc_id"):
            sdr_timeline[te["source_doc_id"]].append(te_dict)
        else:
            run_level_timeline.append(te_dict)
    rs["timeline_events"] = run_level_timeline

    # --- Build source_doc_name lookup ---
    sdr_name_map: Dict[str, str] = {}
    for st in source_statuses:
        source_doc_id = st.get("source_doc_id")
        if source_doc_id:
            sdr_name_map[source_doc_id] = st.get("source_doc_name") or source_doc_id

    # --- Build source_doc_results ---
    all_sdr_ids: set = set()
    all_sdr_ids.update(sdr_gen_docs.keys())
    all_sdr_ids.update(sdr_combined.keys())
    all_sdr_ids.update(sdr_timeline.keys())
    all_sdr_ids.update(sdr_name_map.keys())

    source_doc_results: Dict[str, Dict] = {}
    for sdr_id in all_sdr_ids:
        sdr: Dict[str, Any] = {
            "source_doc_name": sdr_name_map.get(sdr_id, sdr_id),
            "generated_docs": sdr_gen_docs.get(sdr_id, []),
            "combined_docs": sdr_combined.get(sdr_id, []),
            "timeline_events": sdr_timeline.get(sdr_id, []),
        }
        # Per-SDR single_eval_results: {doc_id: {avg_score: X}}
        single_eval_results: Dict[str, Dict] = {}
        for gdoc in sdr["generated_docs"]:
            doc_id = gdoc["doc_id"]
            if doc_id in pce:
                crit_scores = list(pce[doc_id].values())
                avg = sum(crit_scores) / len(crit_scores) if crit_scores else None
                single_eval_results[doc_id] = {"avg_score": avg, **pce[doc_id]}
        sdr["single_eval_results"] = single_eval_results

        # Per-SDR pairwise_results with rankings and judge_deviations
        sdr_pre_comps = pre_pairwise_by_sdr.get(sdr_id, [])
        if sdr_pre_comps:
            sdr["pairwise_results"] = {
                "rankings": _compute_rankings(sdr_pre_comps),
                "judge_deviations": _compute_judge_deviations(sdr_pre_comps),
            }
        else:
            sdr["pairwise_results"] = {}

        # Per-SDR post_combine_eval_scores
        sdr_post_scores: Dict[str, Any] = {}
        for cd in sdr["combined_docs"]:
            cd_id = cd["doc_id"]
            if cd_id in post_combine_evals:
                sdr_post_scores[cd_id] = post_combine_evals[cd_id]
        if sdr_post_scores:
            sdr["post_combine_eval_scores"] = sdr_post_scores

        source_doc_results[sdr_id] = sdr

    rs["source_doc_results"] = source_doc_results

    # --- generated_docs flat list (all docs across all SDRs) ---
    rs["generated_docs"] = list(all_gen_doc_meta.values())

    return rs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def build_run_export(
    run_id: str,
    user_uuid: str,
    run_name: str = "",
) -> Path:
    """
    Build a ZIP export for *run_id* and save it next to generated docs.

    Returns the path to the created ZIP file.
    Raises on unrecoverable errors; logs warnings on partial failures so that
    the overall run completion is never blocked.
    """
    from app.infra.db.session import get_user_session_by_uuid
    from app.api.routes.runs.artifacts import get_run_root
    run_root = get_run_root(user_uuid, run_id)
    export_path = run_root / "export.zip"
    export_path.parent.mkdir(parents=True, exist_ok=True)

    async def _load_export_results() -> Dict[str, Any]:
        async with get_user_session_by_uuid(user_uuid) as session:
            return await _build_results_dict_from_normalized(session, run_id)

    # ------------------------------------------------------------------
    # Fetch normalized run results without consulting external telemetry data.
    # ------------------------------------------------------------------
    try:
        export_rs = await _load_export_results()
        logger.info("export_service: using normalized tables for run %s", run_id[:8])
    except Exception as exc:
        logger.warning("export_service: failed to build export for run %s: %s", run_id[:8], exc)
        raise

    # ------------------------------------------------------------------
    # Build ZIP on disk, then atomically replace export.zip
    # ------------------------------------------------------------------
    logger.info("export_service: dispatch zip write for run %s -> %s", run_id[:8], export_path)
    try:
        await asyncio.to_thread(
            _write_export_zip_sync,
            export_path=export_path,
            run_root=run_root,
            run_id=run_id,
            run_name=run_name,
            export_rs=export_rs,
        )
    except Exception as exc:
        logger.warning("export_service: zip write failed for run %s: %s", run_id[:8], exc, exc_info=True)
        raise

    logger.info("export_service: completed zip write for run %s", run_id[:8])
    logger.info("export_service: wrote %d bytes to %s", export_path.stat().st_size, export_path)
    return export_path


# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------

_README_CONTENT = """\
# APICostX Run Export

This ZIP archive contains all data produced by a single APICostX run.

## Contents

| Path | Description |
|------|-------------|
| `run_summary.json` | Raw results_summary blob (machine-readable) |
| `README.md` | This file |
| `spreadsheets/generated_doc_leaderboard.csv` | All generated docs with eval score, win rates, and winner flag |
| `spreadsheets/run_summary.csv` | One row per generated document with score |
| `spreadsheets/single_eval_heatmap.csv` | Per-document × criterion × judge score matrix |
| `spreadsheets/single_eval_criteria_deviation.csv` | Judge reliability deviations per criterion |
| `spreadsheets/combined_documents.csv` | Combined (post-merge) docs and their scores |
| `spreadsheets/timeline_execution_events.csv` | Execution timeline events |
| `spreadsheets/pairwise_rankings.csv` | Pairwise comparison win/loss rankings |
| `spreadsheets/pairwise_judge_consensus_deviation.csv` | Judge consensus deviations for pairwise eval |
| `spreadsheets/judge_quality.csv` | Per-judge per-criterion score detail |
| `reports/` | HTML / Markdown report files |
| `evaluations/` | Generated document markdown files |

All CSV files use UTF-8 encoding with a BOM for Excel compatibility.
"""


def _add_readme(zf: zipfile.ZipFile) -> None:
    zf.writestr("README.md", _README_CONTENT)


# ---------------------------------------------------------------------------
# run_summary.json
# ---------------------------------------------------------------------------

def _add_run_summary_json(
    zf: zipfile.ZipFile,
    run_id: str,
    run_name: str,
    results_summary: Dict[str, Any],
) -> None:
    payload = {
        "run_id": run_id,
        "run_name": run_name,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "results_summary": results_summary,
    }
    try:
        text = json.dumps(payload, indent=2, default=str)  # law-exempt: export wire format written directly to zip archive
    except Exception as exc:
        logger.warning("export_service: failed to serialize results_summary: %s", exc)
        text = json.dumps({"run_id": run_id, "error": str(exc)}, indent=2)  # law-exempt: export wire format
    zf.writestr("run_summary.json", text)


# ---------------------------------------------------------------------------
# Spreadsheet helpers
# ---------------------------------------------------------------------------

def _csv_bytes(rows: List[List[Any]], headers: List[str]) -> bytes:
    """Return UTF-8-BOM CSV bytes for Excel compatibility."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow([("" if v is None else v) for v in row])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _add_spreadsheets(
    zf: zipfile.ZipFile,
    run_id: str,
    rs: Dict[str, Any],
) -> None:
    sheets = {
        "generated_doc_leaderboard.csv": _build_generated_doc_leaderboard(rs),
        "run_summary.csv": _build_run_summary(rs),
        "single_eval_heatmap.csv": _build_single_eval_heatmap(rs),
        "single_eval_criteria_deviation.csv": _build_criteria_deviation(rs),
        "combined_documents.csv": _build_combined_documents(rs),
        "timeline_execution_events.csv": _build_timeline_events(rs),
        "pairwise_rankings.csv": _build_pairwise_rankings(rs),
        "pairwise_judge_consensus_deviation.csv": _build_pairwise_deviations(rs),
        "judge_quality.csv": _build_judge_quality(rs),
    }
    for name, (headers, rows) in sheets.items():
        try:
            zf.writestr(f"spreadsheets/{name}", _csv_bytes(rows, headers))
        except Exception as exc:
            logger.warning("export_service: failed to build sheet %s: %s", name, exc)


# ---------------------------------------------------------------------------
# 0. generated_doc_leaderboard.csv
# ---------------------------------------------------------------------------

def _build_generated_doc_leaderboard(
    rs: Dict[str, Any],
):
    """
    One row per generated document with a high-level performance summary.

    Columns:
      doc_uuid              – unique document identifier
      model                 – model that generated the doc
      eval_score_pct        – final single-eval score as XX.X%
      pre_combine_win_rate  – pairwise win rate before combining (XX.X%)
      post_combine_win_rate – pairwise win rate after combining (empty if N/A)
      is_winner             – yes / no
    """
    headers = [
        "doc_uuid",
        "model",
        "eval_score_pct",
        "pre_combine_win_rate",
        "post_combine_win_rate",
        "is_winner",
    ]

    winner = rs.get("winner") or ""

    # Per-doc eval scores from pre_combine_evals (run-level, keyed by doc_id).
    # Values are {criterion: avg_score_1_5}; convert to 0-100%.
    pce: Dict[str, Dict] = rs.get("pre_combine_evals") or {}

    # Per-doc scores also available from SDR single_eval_results.
    # Build a lookup: doc_id -> avg_score (1-5 scale) from SDR data.
    sdr_avg: Dict[str, float] = {}
    for sdr in (rs.get("source_doc_results") or {}).values():
        if not isinstance(sdr, dict):
            continue
        for doc_id, ser in (sdr.get("single_eval_results") or {}).items():
            if isinstance(ser, dict):
                avg = ser.get("avg_score")
                if avg is not None:
                    sdr_avg[doc_id] = float(avg)

    # Pre-combine pairwise win rates: doc_id -> (wins, losses)
    pre_win: Dict[str, tuple] = {}
    for item in (rs.get("pairwise") or {}).get("rankings") or []:
        if not isinstance(item, dict):
            continue
        did = item.get("doc_id") or item.get("id") or ""
        if did:
            pre_win[did] = (item.get("wins") or 0, item.get("losses") or 0)
    # Also check per-SDR pairwise_results for per-SDR tournament data.
    for sdr in (rs.get("source_doc_results") or {}).values():
        if not isinstance(sdr, dict):
            continue
        pw = sdr.get("pairwise_results") or {}
        for item in (pw.get("rankings") or pw.get("results") or []):
            if not isinstance(item, dict):
                continue
            did = item.get("doc_id") or item.get("id") or ""
            wins = item.get("wins") or item.get("win_count") or 0
            losses = item.get("losses") or item.get("loss_count") or 0
            if did and did not in pre_win:
                pre_win[did] = (wins, losses)

    # Post-combine pairwise win rates (if a separate post-combine tournament ran).
    post_win: Dict[str, tuple] = {}
    for item in (rs.get("post_combine_pairwise") or {}).get("rankings") or []:
        if not isinstance(item, dict):
            continue
        did = item.get("doc_id") or item.get("id") or ""
        if did:
            post_win[did] = (item.get("wins") or 0, item.get("losses") or 0)

    def _win_rate_str(wins, losses) -> str:
        total = wins + losses
        return f"{wins / total * 100:.1f}" if total > 0 else ""

    rows: List[List[Any]] = []
    seen_doc_ids: set = set()
    for sdr in (rs.get("source_doc_results") or {}).values():
        if not isinstance(sdr, dict):
            continue
        for gdoc in (sdr.get("generated_docs") or []):
            if not isinstance(gdoc, dict):
                continue
            doc_id = gdoc.get("doc_id") or gdoc.get("id") or ""
            if not doc_id or doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            model = gdoc.get("model") or ""

            # Eval score: prefer SDR avg_score (1-5), then pre_combine_evals avg.
            avg_score: Optional[float] = sdr_avg.get(doc_id)
            if avg_score is None and doc_id in pce:
                crit_scores = [v for v in pce[doc_id].values() if isinstance(v, (int, float))]
                avg_score = sum(crit_scores) / len(crit_scores) if crit_scores else None
            eval_pct = f"{avg_score / 5 * 100:.1f}" if avg_score is not None else ""

            # Win rates
            pre_w, pre_l = pre_win.get(doc_id, (None, None))
            pre_wr = _win_rate_str(pre_w, pre_l) if pre_w is not None else ""
            post_w, post_l = post_win.get(doc_id, (None, None))
            post_wr = _win_rate_str(post_w, post_l) if post_w is not None else ""

            rows.append([
                doc_id,
                model,
                eval_pct,
                pre_wr,
                post_wr,
                "yes" if doc_id == winner else "no",
            ])

    # Sort: winner first, then by eval score descending.
    def _sort_key(row):
        is_win = 0 if row[6] == "yes" else 1
        score = float(row[2]) if row[2] else -1.0
        return (is_win, -score)

    rows.sort(key=_sort_key)
    return headers, rows


# ---------------------------------------------------------------------------
# 1. run_summary.csv
# ---------------------------------------------------------------------------

def _build_run_summary(rs: Dict[str, Any]):
    headers = [
        "source_doc_id", "source_doc_name",
        "doc_id", "generator", "model", "iteration",
        "score_pct", "is_winner",
    ]
    rows: List[List[Any]] = []
    winner = rs.get("winner") or ""
    eval_scores = rs.get("eval_scores") or {}
    seen_run_summary_ids: set = set()

    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if not isinstance(sdr, dict):
            continue
        sdr_name = sdr.get("source_doc_name") or sdr_id
        for gdoc in (sdr.get("generated_docs") or []):
            if not isinstance(gdoc, dict):
                continue
            doc_id = gdoc.get("id") or gdoc.get("doc_id") or ""
            if not doc_id or doc_id in seen_run_summary_ids:
                continue
            seen_run_summary_ids.add(doc_id)
            # Score: check sdr single_eval_results first, then run-level eval_scores
            single_evals = sdr.get("single_eval_results") or sdr.get("single_eval_scores") or {}
            score_raw = single_evals.get(doc_id)
            if isinstance(score_raw, dict):
                score = score_raw.get("avg_score")
            elif isinstance(score_raw, (int, float)):
                score = float(score_raw)
            else:
                score = eval_scores.get(doc_id)
            score_pct = f"{float(score) * 100:.1f}" if score is not None else ""
            rows.append([
                sdr_id, sdr_name,
                doc_id,
                gdoc.get("generator") or "",
                gdoc.get("model") or "",
                gdoc.get("iteration") or 1,
                score_pct,
                "yes" if doc_id == winner else "no",
            ])
    return headers, rows


# ---------------------------------------------------------------------------
# 2. single_eval_heatmap.csv
# ---------------------------------------------------------------------------

def _build_single_eval_heatmap(rs: Dict[str, Any]):
    """
    Wide-format heatmap: one row per generated document.
    Columns are dynamically generated as {criterion} (single judge/trial),
    {criterion}__{judge} (multi-judge), or {criterion}__{judge}__T{trial}
    (multi-judge + multi-trial).
    Appended totals: total_score_raw, total_score_max, total_score_pct.
    """
    pce_detailed = rs.get("pre_combine_evals_detailed") or {}

    # Build lookup: doc_id -> sdr_id and doc metadata
    doc_meta: Dict[str, Dict] = {}
    doc_sdr: Dict[str, str] = {}
    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if not isinstance(sdr, dict):
            continue
        for gdoc in (sdr.get("generated_docs") or []):
            if isinstance(gdoc, dict):
                did = gdoc.get("id") or gdoc.get("doc_id") or ""
                doc_meta[did] = gdoc
                doc_sdr[did] = sdr_id

    # First pass: collect ordered column keys (criterion, judge, trial) preserving
    # insertion order so that the spreadsheet columns match the UI heatmap order.
    col_keys: List[tuple] = []
    seen_col_keys: set = set()
    # Also collect per-doc score maps: doc_id -> {col_key: score}
    per_doc_scores: Dict[str, Dict[tuple, Any]] = {}

    for doc_id, detail in pce_detailed.items():
        if not isinstance(detail, dict):
            continue
        per_doc_scores[doc_id] = {}
        for eval_entry in (detail.get("evaluations") or []):
            if not isinstance(eval_entry, dict):
                continue
            judge = eval_entry.get("judge_model") or ""
            trial = eval_entry.get("trial") or 1
            for crit_score in (eval_entry.get("scores") or []):
                if not isinstance(crit_score, dict):
                    continue
                criterion = crit_score.get("criterion") or ""
                score = crit_score.get("score")
                ck = (criterion, judge, trial)
                if ck not in seen_col_keys:
                    col_keys.append(ck)
                    seen_col_keys.add(ck)
                if score is not None:
                    per_doc_scores[doc_id][ck] = score

    # Determine how to name columns based on whether there are multiple judges/trials
    all_judges = {k[1] for k in col_keys}
    all_trials = {k[2] for k in col_keys}
    multi_judge = len(all_judges) > 1
    multi_trial = len(all_trials) > 1

    def _col_name(criterion: str, judge: str, trial) -> str:
        if multi_judge and multi_trial:
            return f"{criterion}__{judge}__T{trial}"
        if multi_judge:
            return f"{criterion}__{judge}"
        if multi_trial:
            return f"{criterion}__T{trial}"
        return criterion

    col_names = [_col_name(k[0], k[1], k[2]) for k in col_keys]
    headers = (
        ["source_doc_id", "doc_id", "generator", "model", "iteration"]
        + col_names
        + ["total_score_raw", "total_score_max", "total_score_pct"]
    )

    _MAX_PER_CRITERION = 5  # scores are on a 1-5 scale

    rows: List[List[Any]] = []
    for doc_id, score_map in per_doc_scores.items():
        meta = doc_meta.get(doc_id) or {}
        sdr_id = doc_sdr.get(doc_id) or ""
        row: List[Any] = [
            sdr_id,
            doc_id,
            meta.get("generator") or "",
            meta.get("model") or "",
            meta.get("iteration") or "",
        ]
        total_raw = 0
        total_max = 0
        for ck in col_keys:
            score = score_map.get(ck)
            row.append(score if score is not None else "")
            if score is not None:
                total_raw += score
                total_max += _MAX_PER_CRITERION
        score_pct = f"{total_raw / total_max * 100:.1f}" if total_max > 0 else ""
        row += [
            total_raw if total_max > 0 else "",
            total_max if total_max > 0 else "",
            score_pct,
        ]
        rows.append(row)
    return headers, rows


# ---------------------------------------------------------------------------
# 3. single_eval_criteria_deviation.csv
# ---------------------------------------------------------------------------

def _build_criteria_deviation(rs: Dict[str, Any]):
    """
    Wide-format deviation table: one row per judge.
    Columns are one per criterion (showing each judge's average deviation from
    the group mean on that criterion). Positive = scored higher than average;
    negative = scored lower.  Last column is total_deviation (sum across all
    criteria for that judge).
    """
    # Collect judge->criterion->deviation mapping (merge all SDRs)
    judge_crit_dev: Dict[str, Dict[str, float]] = {}

    raw_devs = rs.get("eval_deviations") or {}
    if raw_devs and isinstance(raw_devs, dict):
        for judge, crit_map in raw_devs.items():
            if isinstance(crit_map, dict):
                judge_crit_dev.setdefault(judge, {}).update(
                    {c: float(v) for c, v in crit_map.items() if v is not None}
                )
    else:
        for sdr in (rs.get("source_doc_results") or {}).values():
            if not isinstance(sdr, dict):
                continue
            d = sdr.get("eval_deviations") or {}
            for judge, crit_map in d.items():
                if isinstance(crit_map, dict):
                    judge_crit_dev.setdefault(judge, {}).update(
                        {c: float(v) for c, v in crit_map.items() if v is not None}
                    )

    # Collect ordered criteria — skip the pre-computed __TOTAL__ sentinel key
    # that the deviation calculator may store alongside real criteria names.
    all_criteria: List[str] = []
    seen_crit: set = set()
    for crit_map in judge_crit_dev.values():
        for c in crit_map:
            if c not in seen_crit and c != "__TOTAL__":
                all_criteria.append(c)
                seen_crit.add(c)

    headers = ["judge_model"] + all_criteria + ["total_deviation"]
    rows: List[List[Any]] = []
    for judge_model, crit_map in judge_crit_dev.items():
        row: List[Any] = [judge_model]
        total = 0.0
        for criterion in all_criteria:
            dev = crit_map.get(criterion)
            row.append(f"{dev:.4f}" if dev is not None else "")
            if dev is not None:
                total += float(dev)
        # Use the pre-computed __TOTAL__ when available, otherwise use our sum
        stored_total = crit_map.get("__TOTAL__")
        row.append(f"{float(stored_total):.4f}" if stored_total is not None else f"{total:.4f}")
        rows.append(row)
    return headers, rows


# ---------------------------------------------------------------------------
# 4. combined_documents.csv
# ---------------------------------------------------------------------------

def _build_combined_documents(rs: Dict[str, Any]):
    headers = [
        "source_doc_id", "source_doc_name",
        "combined_doc_id", "model", "generator", "iteration",
        "post_combine_score",
    ]
    rows: List[List[Any]] = []
    post_combine_evals = rs.get("post_combine_evals") or {}

    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if not isinstance(sdr, dict):
            continue
        sdr_name = sdr.get("source_doc_name") or sdr_id
        combined_docs = sdr.get("combined_docs") or []
        if not combined_docs and sdr.get("combined_doc"):
            combined_docs = [sdr["combined_doc"]]
        for cd in combined_docs:
            if not isinstance(cd, dict):
                continue
            cd_id = cd.get("id") or cd.get("doc_id") or ""
            # Post-combine score: average over all judges
            scores = post_combine_evals.get(cd_id) or sdr.get("post_combine_eval_scores") or {}
            avg_score: Optional[float] = None
            if isinstance(scores, dict) and scores:
                vals = [v for v in scores.values() if isinstance(v, (int, float))]
                avg_score = sum(vals) / len(vals) if vals else None
            elif isinstance(scores, (int, float)):
                avg_score = float(scores)
            rows.append([
                sdr_id, sdr_name,
                cd_id,
                cd.get("model") or "",
                cd.get("generator") or "",
                cd.get("iteration") or 1,
                f"{avg_score * 100:.1f}" if avg_score is not None else "",
            ])
    return headers, rows


# ---------------------------------------------------------------------------
# 5. timeline_execution_events.csv
# ---------------------------------------------------------------------------

def _build_timeline_events(rs: Dict[str, Any]):
    headers = [
        "source_doc_id", "phase", "event_type", "description",
        "model", "timestamp", "duration_seconds", "success",
    ]
    rows: List[List[Any]] = []

    def _add_events(events, source_doc_id=""):
        for te in (events or []):
            if not isinstance(te, dict):
                continue
            details = te.get("details") or {}
            rows.append([
                source_doc_id or details.get("source_doc_id") or "",
                te.get("phase") or "",
                te.get("event_type") or "",
                (te.get("description") or "").replace("\n", " ")[:500],
                te.get("model") or details.get("model") or "",
                te.get("timestamp") or "",
                te.get("duration_seconds") or "",
                te.get("success") if te.get("success") is not None else "",
            ])

    # Per-SDR timeline_events first (already filtered)
    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if isinstance(sdr, dict):
            _add_events(sdr.get("timeline_events"), sdr_id)

    # Run-level events not already in any SDR
    sdr_events_collected = set()
    for sdr in (rs.get("source_doc_results") or {}).values():
        if isinstance(sdr, dict):
            for te in (sdr.get("timeline_events") or []):
                if isinstance(te, dict):
                    sdr_events_collected.add(te.get("timestamp") or "")

    for te in (rs.get("timeline_events") or []):
        if isinstance(te, dict) and te.get("timestamp") not in sdr_events_collected:
            details = te.get("details") or {}
            rows.append([
                details.get("source_doc_id") or "",
                te.get("phase") or "",
                te.get("event_type") or "",
                (te.get("description") or "").replace("\n", " ")[:500],
                te.get("model") or details.get("model") or "",
                te.get("timestamp") or "",
                te.get("duration_seconds") or "",
                te.get("success") if te.get("success") is not None else "",
            ])
    return headers, rows


# ---------------------------------------------------------------------------
# 7. pairwise_rankings.csv
# ---------------------------------------------------------------------------

def _build_pairwise_rankings(rs: Dict[str, Any]):
    headers = [
        "source_doc_id", "source_doc_name",
        "rank", "doc_id", "model", "generator",
        "wins", "losses", "score",
    ]
    rows: List[List[Any]] = []

    def _parse_pairwise(sdr_id: str, sdr_name: str, pairwise_data, doc_meta: Dict):
        if not isinstance(pairwise_data, dict):
            return
        # rankings key may be a list of {doc_id, wins, losses, score} or similar
        rankings = pairwise_data.get("rankings") or pairwise_data.get("results") or []
        if not rankings and "winner" in pairwise_data:
            # Minimal structure: only winner known
            rankings = [{"doc_id": pairwise_data["winner"]}]
        for rank_idx, item in enumerate(rankings, start=1):
            if not isinstance(item, dict):
                continue
            doc_id = item.get("doc_id") or item.get("id") or ""
            meta = doc_meta.get(doc_id) or {}
            rows.append([
                sdr_id, sdr_name,
                rank_idx, doc_id,
                meta.get("model") or "",
                meta.get("generator") or "",
                item.get("wins") or item.get("win_count") or "",
                item.get("losses") or item.get("loss_count") or "",
                item.get("score") or item.get("elo") or "",
            ])

    # Build doc_meta lookup
    doc_meta: Dict[str, Dict] = {}
    for sdr in (rs.get("source_doc_results") or {}).values():
        if isinstance(sdr, dict):
            for gdoc in (sdr.get("generated_docs") or []):
                if isinstance(gdoc, dict):
                    did = gdoc.get("id") or gdoc.get("doc_id") or ""
                    doc_meta[did] = gdoc

    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if not isinstance(sdr, dict):
            continue
        sdr_name = sdr.get("source_doc_name") or sdr_id
        pairwise_data = sdr.get("pairwise_results")
        if pairwise_data:
            _parse_pairwise(sdr_id, sdr_name, pairwise_data, doc_meta)

    # Also check run-level pairwise
    run_pairwise = rs.get("pairwise") or {}
    if run_pairwise and not rows:
        _parse_pairwise("(run)", "(run)", run_pairwise, doc_meta)

    return headers, rows


# ---------------------------------------------------------------------------
# 8. pairwise_judge_consensus_deviation.csv
# ---------------------------------------------------------------------------

def _build_pairwise_deviations(rs: Dict[str, Any]):
    headers = ["source_doc_id", "source_doc_name", "judge_model", "deviation"]
    rows: List[List[Any]] = []

    for sdr_id, sdr in (rs.get("source_doc_results") or {}).items():
        if not isinstance(sdr, dict):
            continue
        sdr_name = sdr.get("source_doc_name") or sdr_id
        pairwise_data = sdr.get("pairwise_results") or {}
        if not isinstance(pairwise_data, dict):
            continue
        deviations = pairwise_data.get("judge_deviations") or pairwise_data.get("deviations") or {}
        if isinstance(deviations, dict):
            for judge_model, dev in deviations.items():
                rows.append([sdr_id, sdr_name, judge_model,
                              f"{float(dev):.4f}" if dev is not None else ""])
    return headers, rows


# ---------------------------------------------------------------------------
# 9. judge_quality.csv
# ---------------------------------------------------------------------------

# Constants matching the website's Judge Quality tab calculation
_JQ_LAMBDA = 0.10       # volatility penalty weight for Sortino score
_JQ_W_SELF = 0.50       # self-consistency weight (multi-trial)
_JQ_W_CONSENSUS = 0.35  # consensus-alignment weight
_JQ_W_VARIANCE = 0.15   # variance (score spread) weight


def _krippendorff_alpha(rating_matrix: List[List[Optional[float]]]) -> Optional[float]:
    """Compute Krippendorff's alpha for interval data (same algorithm as the website)."""
    if len(rating_matrix) < 2:
        return None
    n_items = len(rating_matrix[0]) if rating_matrix and rating_matrix[0] is not None else 0
    if n_items < 2:
        return None
    all_vals: set = set()
    for row in rating_matrix:
        for v in row:
            if v is not None:
                all_vals.add(float(v))
    if len(all_vals) < 2:
        return None
    values = sorted(all_vals)
    coincidence: Dict[float, Dict[float, float]] = {v: {w: 0.0 for w in values} for v in values}
    total_pairable = 0
    for item in range(n_items):
        ratings: List[float] = []
        for row in rating_matrix:
            if item < len(row) and row[item] is not None:
                ratings.append(float(row[item]))
        m = len(ratings)
        if m < 2:
            continue
        total_pairable += m
        for i in range(m):
            for j in range(m):
                if i == j:
                    continue
                coincidence[ratings[i]][ratings[j]] += 1.0 / (m - 1)
    if total_pairable < 2:
        return None
    marginals: Dict[float, float] = {c: sum(coincidence[c][k] for k in values) for c in values}
    total_marginal = sum(marginals.values())
    if total_marginal <= 0:
        return None
    Do = sum(
        coincidence[c][k] * (c - k) ** 2
        for c in values for k in values if c != k
    ) / total_marginal
    De = sum(
        marginals[values[i]] * marginals[values[j]] * (values[i] - values[j]) ** 2
        for i in range(len(values)) for j in range(len(values)) if i != j
    ) / (total_marginal * (total_marginal - 1))
    if De == 0:
        return 1.0
    return 1.0 - Do / De


def _build_judge_quality(rs: Dict[str, Any]):
    """
    One row per judge model.  Replicates every column shown in the website's
    Judge Quality tab:

    rank | judge_model | sortino_score_pct (★ Quality) | agreement_pct (Within 1pt)
    | consensus_score_pct (Group Align) | composite_quality_pct (Quality)
    | self_consistency_pct | avg_score_given | avg_score_pct | std_dev
    | min_score | max_score | total_scores_n | outlier_count
    | mean_trial_diff | krippendorff_alpha
    """
    pce_detailed = rs.get("pre_combine_evals_detailed") or {}
    evaluators: List[str] = list(rs.get("evaluator_list") or [])
    criteria: List[str] = list(rs.get("criteria_list") or [])

    # ------------------------------------------------------------------ #
    # Build flat EvalRow list (doc_id, judge, trial, {criterion: score})  #
    # ------------------------------------------------------------------ #
    class _EvalRow:
        __slots__ = ("doc_id", "judge", "trial", "scores")
        def __init__(self, doc_id, judge, trial, scores):
            self.doc_id = doc_id
            self.judge = judge
            self.trial = trial
            self.scores: Dict[str, float] = scores

    eval_rows: List[_EvalRow] = []
    for doc_id, detail in pce_detailed.items():
        if not isinstance(detail, dict):
            continue
        for ev in (detail.get("evaluations") or []):
            if not isinstance(ev, dict):
                continue
            judge = ev.get("judge_model") or ""
            if not judge:
                continue
            try:
                trial = int(ev.get("trial") or 1)
            except Exception:
                trial = 1
            score_map: Dict[str, float] = {}
            for sc in (ev.get("scores") or []):
                if isinstance(sc, dict):
                    c = sc.get("criterion")
                    s = sc.get("score")
                    if c and isinstance(s, (int, float)):
                        score_map[str(c)] = float(s)
            eval_rows.append(_EvalRow(doc_id=str(doc_id), judge=judge, trial=trial, scores=score_map))

    if not eval_rows:
        return [
            "rank", "judge_model", "sortino_score_pct", "agreement_pct",
            "consensus_score_pct", "composite_quality_pct", "self_consistency_pct",
            "avg_score_given", "avg_score_pct", "std_dev", "min_score", "max_score",
            "total_scores_n", "outlier_count", "mean_trial_diff",
            "krippendorff_alpha",
        ], []

    # Derive evaluators / criteria from data if not in results_summary
    if not evaluators:
        evaluators = sorted({r.judge for r in eval_rows})
    if not criteria:
        crit_set: set = set()
        for r in eval_rows:
            crit_set.update(r.scores.keys())
        criteria = sorted(crit_set)

    doc_ids = sorted({r.doc_id for r in eval_rows})
    trial_numbers = sorted({r.trial for r in eval_rows})
    has_multiple_trials = len(trial_numbers) > 1

    # consensus_map[doc_id][criterion] = mean score across all judges
    consensus_map: Dict[str, Dict[str, float]] = {}
    for d in doc_ids:
        consensus_map[d] = {}
        for crit in criteria:
            vals = [r.scores[crit] for r in eval_rows if r.doc_id == d and crit in r.scores]
            if vals:
                consensus_map[d][crit] = sum(vals) / len(vals)

    # ------------------------------------------------------------------ #
    # Krippendorff's alpha  (panel-level, same for every judge row)       #
    # ------------------------------------------------------------------ #
    item_keys = [f"{d}::{crit}" for d in doc_ids for crit in criteria]
    rating_matrix: List[List[Optional[float]]] = []
    for rater in evaluators:
        row_vals: List[Optional[float]] = []
        for key in item_keys:
            d_id, crit = key.split("::", 1)
            rater_scores = [r.scores[crit] for r in eval_rows if r.judge == rater and r.doc_id == d_id and crit in r.scores]
            if rater_scores:
                row_vals.append(float(round(sum(rater_scores) / len(rater_scores))))
            else:
                row_vals.append(None)
        rating_matrix.append(row_vals)
    alpha = _krippendorff_alpha(rating_matrix)

    # ------------------------------------------------------------------ #
    # Per-judge metrics                                                   #
    # ------------------------------------------------------------------ #
    result_rows: List[List[Any]] = []

    for judge in evaluators:
        j_evals = [r for r in eval_rows if r.judge == judge]
        if not j_evals:
            continue

        # Self-consistency (multi-trial only)
        trial_diffs: List[float] = []
        if has_multiple_trials:
            for d in doc_ids:
                d_evals = [r for r in j_evals if r.doc_id == d]
                for i in range(len(d_evals)):
                    for j2 in range(i + 1, len(d_evals)):
                        for crit in criteria:
                            si = d_evals[i].scores.get(crit)
                            sj = d_evals[j2].scores.get(crit)
                            if si is not None and sj is not None:
                                trial_diffs.append(abs(si - sj))
        mean_trial_diff = sum(trial_diffs) / len(trial_diffs) if trial_diffs else 0.0
        self_consistency = max(0.0, 1.0 - mean_trial_diff / 4.0) if has_multiple_trials else 1.0

        # Consensus deviation & outliers
        consensus_devs: List[float] = []
        outlier_count = 0
        total_scores = 0
        for ev in j_evals:
            for crit in criteria:
                score = ev.scores.get(crit)
                consensus = consensus_map.get(ev.doc_id, {}).get(crit)
                if score is None or consensus is None:
                    continue
                dev = abs(score - consensus)
                consensus_devs.append(dev)
                total_scores += 1
                if dev > 1.0:
                    outlier_count += 1
        mean_consensus_dev = sum(consensus_devs) / len(consensus_devs) if consensus_devs else 0.0
        consensus_score = max(0.0, 1.0 - mean_consensus_dev / 4.0)

        # Variance / std dev
        all_scores_flat = [v for ev in j_evals for v in ev.scores.values()]
        mean_score = sum(all_scores_flat) / len(all_scores_flat) if all_scores_flat else 0.0
        variance = sum((s - mean_score) ** 2 for s in all_scores_flat) / len(all_scores_flat) if all_scores_flat else 0.0
        std_dev = variance ** 0.5
        variance_score = max(0.0, 1.0 - std_dev / 2.0)

        # Sortino score (primary ★ Quality ranking metric)
        volatility = (mean_trial_diff / 4.0) if has_multiple_trials else 0.0
        sortino_score = max(0.0, min(1.0, consensus_score - _JQ_LAMBDA * volatility))

        # Agreement (Within 1pt)
        agreement_score = (total_scores - outlier_count) / total_scores if total_scores > 0 else 1.0

        # Composite quality (secondary Quality column)
        if has_multiple_trials:
            composite_quality = (
                _JQ_W_SELF * self_consistency
                + _JQ_W_CONSENSUS * consensus_score
                + _JQ_W_VARIANCE * variance_score
            )
        else:
            composite_quality = (
                (_JQ_W_CONSENSUS / (_JQ_W_CONSENSUS + _JQ_W_VARIANCE)) * consensus_score
                + (_JQ_W_VARIANCE / (_JQ_W_CONSENSUS + _JQ_W_VARIANCE)) * variance_score
            )

        result_rows.append([
            judge,
            f"{sortino_score * 100:.1f}",
            f"{agreement_score * 100:.1f}",
            f"{consensus_score * 100:.1f}",
            f"{composite_quality * 100:.1f}",
            f"{self_consistency * 100:.1f}" if has_multiple_trials else "",
            f"{mean_score:.4f}",
            f"{mean_score / 5 * 100:.1f}",
            f"{std_dev:.4f}",
            f"{min(all_scores_flat):.0f}" if all_scores_flat else "",
            f"{max(all_scores_flat):.0f}" if all_scores_flat else "",
            total_scores,
            outlier_count,
            f"{mean_trial_diff:.4f}" if has_multiple_trials else "",
            f"{alpha:.4f}" if alpha is not None else "",
        ])

    # Sort by sortino_score_pct descending (matches website ranking)
    result_rows.sort(key=lambda r: float(r[1]) if r[1] else 0.0, reverse=True)

    # Prepend rank
    for i, r in enumerate(result_rows, start=1):
        r.insert(0, i)

    headers = [
        "rank", "judge_model",
        "sortino_score_pct",      # ★ Quality
        "agreement_pct",          # Within 1pt
        "consensus_score_pct",    # Group Align
        "composite_quality_pct",  # Quality
        "self_consistency_pct",   # only populated for multi-trial runs
        "avg_score_given",
        "avg_score_pct",
        "std_dev",
        "min_score", "max_score",
        "total_scores_n", "outlier_count",
        "mean_trial_diff",
        "krippendorff_alpha",
    ]
    return headers, result_rows


# ---------------------------------------------------------------------------
# Reports and generated docs
# ---------------------------------------------------------------------------

def _add_reports(zf: zipfile.ZipFile, run_root: Path) -> None:
    """Add any report files found in run_root."""
    for pattern in ("*.html", "*.md", "report*"):
        for p in run_root.glob(pattern):
            try:
                zf.write(p, f"reports/{p.name}")
            except Exception as exc:
                logger.warning("export_service: could not add report %s: %s", p, exc)


def _add_generated_docs(zf: zipfile.ZipFile, run_root: Path) -> None:
    """Add markdown files from the generated/ subfolder."""
    generated_dir = run_root / "generated"
    if not generated_dir.exists():
        return
    for p in sorted(generated_dir.glob("*.md")):
        try:
            zf.write(p, f"evaluations/{p.name}")
        except Exception as exc:
            logger.warning("export_service: could not add generated doc %s: %s", p, exc)
