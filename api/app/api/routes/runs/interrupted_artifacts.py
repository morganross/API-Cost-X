from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ....config import get_settings


_DOCUMENT_A_MARKER = "**DOCUMENT A:**"
_DOCUMENT_B_MARKER = "**DOCUMENT B:**"
_WHITESPACE_RE = re.compile(r"\s+")


def hydrate_interrupted_run_detail_from_artifacts(
    payload: dict[str, Any],
    *,
    user_uuid: str,
    run_id: str,
    source_doc_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Recover display-safe partial artifacts for interrupted runs.

    This path is intentionally read-only. It never invents execution state. It
    only surfaces pairwise/timeline data already written to the run directory
    when normalized persistence was skipped by an interruption.
    """
    run_root = get_settings().data_dir / f"user_{user_uuid}" / "runs" / run_id
    if not run_root.exists():
        return payload

    pairwise_artifacts = _load_pairwise_artifacts(run_root)
    if not pairwise_artifacts:
        return payload

    source_results = payload.get("source_doc_results")
    if isinstance(source_results, dict) and source_results:
        recovered = _recover_source_doc_pairwise(source_results, run_root, pairwise_artifacts)
        if recovered:
            if not payload.get("pairwise_results") and len(source_results) == 1:
                only_result = next(iter(source_results.values()))
                if isinstance(only_result, dict) and only_result.get("pairwise_results"):
                    payload["pairwise_results"] = only_result["pairwise_results"]
            if not payload.get("timeline_events") and len(source_results) == 1:
                only_result = next(iter(source_results.values()))
                if isinstance(only_result, dict) and only_result.get("timeline_events"):
                    payload["timeline_events"] = only_result["timeline_events"]

    timeline = payload.get("timeline")
    if isinstance(timeline, dict) and not timeline.get("events"):
        recovered_event = _recover_timeline_event(
            pairwise_artifacts,
            source_doc_id=source_doc_id,
        )
        if recovered_event is not None:
            timeline["events"] = [recovered_event]

    return payload


def _recover_source_doc_pairwise(
    source_results: dict[str, Any],
    run_root: Path,
    pairwise_artifacts: list[dict[str, Any]],
) -> bool:
    recovered_any = False
    doc_text_cache: dict[str, str] = {}

    for source_result in source_results.values():
        if not isinstance(source_result, dict):
            continue
        generated_docs = [doc for doc in (source_result.get("generated_docs") or []) if isinstance(doc, dict)]
        if len(generated_docs) < 2:
            continue
        if source_result.get("pairwise_results"):
            continue

        doc_texts = {
            doc["id"]: _load_generated_doc_text(run_root, doc["id"], doc_text_cache)
            for doc in generated_docs
            if doc.get("id")
        }
        matched = _match_pairwise_artifact(doc_texts, pairwise_artifacts)
        if matched is None:
            continue

        source_result["pairwise_results"] = matched["pairwise_results"]
        if not source_result.get("winner_doc_id"):
            source_result["winner_doc_id"] = matched["winner_doc_id"]

        timeline_events = source_result.get("timeline_events") or []
        if not timeline_events:
            source_result["timeline_events"] = [matched["timeline_event"]]

        recovered_any = True

    return recovered_any


def _recover_timeline_event(
    pairwise_artifacts: list[dict[str, Any]],
    *,
    source_doc_id: Optional[str],
) -> Optional[dict[str, Any]]:
    if not pairwise_artifacts:
        return None
    artifact = pairwise_artifacts[-1]
    return _build_timeline_event(artifact, source_doc_id=source_doc_id)


def _load_pairwise_artifacts(run_root: Path) -> list[dict[str, Any]]:
    logs_dir = run_root / "logs" / run_root.name
    if not logs_dir.exists():
        return []

    artifacts: list[dict[str, Any]] = []
    for path in sorted(logs_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        parsed = _parse_pairwise_artifact(raw)
        if parsed is not None:
            artifacts.append(parsed)
    return artifacts


def _parse_pairwise_artifact(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    try:
        decision = json.loads(raw.get("human_text") or "")
    except Exception:
        return None

    if not isinstance(decision, dict):
        return None

    winner_label = str(decision.get("winner") or "").strip().upper()
    if winner_label not in {"A", "B", "TIE"}:
        return None

    prompt = _extract_prompt_text(raw)
    if not prompt:
        return None

    doc_a_text, doc_b_text = _extract_document_sections(prompt)
    if not doc_a_text or not doc_b_text:
        return None

    started_at = _parse_datetime(raw.get("started_at"))
    finished_at = _parse_datetime(raw.get("finished_at"))

    return {
        "winner_label": winner_label,
        "reason": str(decision.get("reason") or ""),
        "doc_a_text": doc_a_text,
        "doc_b_text": doc_b_text,
        "judge_model": (
            raw.get("request", {}).get("model")
            or raw.get("response", {}).get("model")
            or raw.get("model")
            or ""
        ),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _extract_prompt_text(raw: dict[str, Any]) -> str:
    request = raw.get("request") or {}
    input_items = request.get("input")
    if not isinstance(input_items, list):
        return ""
    for item in input_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str) and _DOCUMENT_A_MARKER in content and _DOCUMENT_B_MARKER in content:
            return content
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and _DOCUMENT_A_MARKER in text and _DOCUMENT_B_MARKER in text:
                    return text
    return ""


def _extract_document_sections(prompt: str) -> tuple[str, str]:
    doc_a_index = prompt.find(_DOCUMENT_A_MARKER)
    doc_b_index = prompt.find(_DOCUMENT_B_MARKER)
    if doc_a_index == -1 or doc_b_index == -1 or doc_b_index <= doc_a_index:
        return "", ""

    doc_a = prompt[doc_a_index + len(_DOCUMENT_A_MARKER):doc_b_index].strip()
    doc_b = prompt[doc_b_index + len(_DOCUMENT_B_MARKER):].strip()
    return doc_a, doc_b


def _load_generated_doc_text(run_root: Path, doc_id: str, cache: dict[str, str]) -> str:
    if doc_id in cache:
        return cache[doc_id]

    safe_doc_id = doc_id.replace(":", "_").replace("/", "_").replace("\\", "_")
    path = run_root / "generated" / f"{safe_doc_id}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    cache[doc_id] = text
    return text


def _match_pairwise_artifact(
    doc_texts: dict[str, str],
    pairwise_artifacts: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    for artifact in reversed(pairwise_artifacts):
        doc_id_a = _match_doc_id(artifact["doc_a_text"], doc_texts)
        doc_id_b = _match_doc_id(artifact["doc_b_text"], doc_texts)
        if not doc_id_a or not doc_id_b or doc_id_a == doc_id_b:
            continue

        winner_doc_id = None
        if artifact["winner_label"] == "A":
            winner_doc_id = doc_id_a
        elif artifact["winner_label"] == "B":
            winner_doc_id = doc_id_b

        pairwise_results = {
            "total_comparisons": 1,
            "winner_doc_id": winner_doc_id,
            "rankings": [
                {
                    "doc_id": doc_id_a,
                    "wins": 1 if winner_doc_id == doc_id_a else 0,
                    "losses": 1 if winner_doc_id == doc_id_b else 0,
                    "elo": 1550.0 if winner_doc_id == doc_id_a else 1450.0 if winner_doc_id == doc_id_b else 1500.0,
                    "colley": None,
                    "massey": None,
                    "bradley_terry": None,
                },
                {
                    "doc_id": doc_id_b,
                    "wins": 1 if winner_doc_id == doc_id_b else 0,
                    "losses": 1 if winner_doc_id == doc_id_a else 0,
                    "elo": 1550.0 if winner_doc_id == doc_id_b else 1450.0 if winner_doc_id == doc_id_a else 1500.0,
                    "colley": None,
                    "massey": None,
                    "bradley_terry": None,
                },
            ],
            "comparisons": [
                {
                    "doc_id_a": doc_id_a,
                    "doc_id_b": doc_id_b,
                    "winner": winner_doc_id or "tie",
                    "judge_model": artifact["judge_model"],
                    "reason": artifact["reason"],
                    "score_a": None,
                    "score_b": None,
                }
            ],
            "pairwise_deviations": {},
        }

        return {
            "pairwise_results": pairwise_results,
            "winner_doc_id": winner_doc_id,
            "timeline_event": _build_timeline_event(artifact),
        }
    return None


def _match_doc_id(section_text: str, doc_texts: dict[str, str]) -> Optional[str]:
    section_norm = _normalize_text(section_text)
    if not section_norm:
        return None

    best_doc_id = None
    best_score = 0
    for doc_id, doc_text in doc_texts.items():
        doc_norm = _normalize_text(doc_text)
        if not doc_norm:
            continue

        prefix = doc_norm[:240]
        if prefix and prefix in section_norm:
            score = len(prefix)
        else:
            score = _common_prefix_len(section_norm, doc_norm)

        if score > best_score:
            best_doc_id = doc_id
            best_score = score

    return best_doc_id if best_score >= 32 else None


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "")).strip()


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right), 320)
    for idx in range(limit):
        if left[idx] != right[idx]:
            return idx
    return limit


def _build_timeline_event(
    artifact: dict[str, Any],
    *,
    source_doc_id: Optional[str] = None,
) -> dict[str, Any]:
    duration_seconds = None
    if artifact["started_at"] and artifact["finished_at"]:
        duration_seconds = max((artifact["finished_at"] - artifact["started_at"]).total_seconds(), 0.0)

    event = {
        "phase": "pairwise",
        "event_type": "pairwise_eval",
        "description": "Recovered pairwise evaluation from saved interrupted-run artifact",
        "model": artifact["judge_model"],
        "timestamp": artifact["started_at"].isoformat() if artifact["started_at"] else None,
        "duration_seconds": duration_seconds,
        "success": True,
        "details": {
            "winner_label": artifact["winner_label"],
            "recovered_from_artifact": True,
        },
    }
    if source_doc_id:
        event["source_doc_id"] = source_doc_id
    return event


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
