"""
grounding_enforcer
- Centralized verification utilities to enforce that provider responses include:
  1) Grounding (provider-side web search / citations / tools)
  2) Reasoning (model-produced rationale/thinking, provider-specific shapes)

Detailed logging mode:
- Every validation check is logged with full details
- Per-run validation log files created in logs/validation/
- Bounded API response previews and response-shape summaries are logged
- Field-by-field inspection results logged
- Respects Python logging levels (DEBUG/INFO/WARNING/ERROR)
"""

from __future__ import annotations
from typing import Any, Dict, Optional, List
import logging
import json
import os
import threading
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("grounding_enforcer")


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert objects to JSON-serializable types."""
    if isinstance(obj, Path):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_json(item) for item in obj]
    else:
        return obj


class ValidationError(RuntimeError):
    """Custom exception for validation failures with detailed classification."""

    def __init__(self, message: str, missing_grounding: bool = False, missing_reasoning: bool = False):
        super().__init__(message)
        self.missing_grounding = missing_grounding
        self.missing_reasoning = missing_reasoning
        self.category = self._classify()

    def _classify(self) -> str:
        """Classify validation error for intelligent retry."""
        if self.missing_grounding and self.missing_reasoning:
            return "validation_both"
        elif self.missing_grounding:
            return "validation_grounding"
        elif self.missing_reasoning:
            return "validation_reasoning"
        return "validation_unknown"

# Global state for current run context (set by file_handler before validation).
# Use thread-local storage so each worker thread has its own context,
# avoiding cross-run contamination under high concurrency.
_CURRENT_RUN_CONTEXT = threading.local()


def set_run_context(run_id: str, provider: str, model: str, log_dir: Optional[Path] = None) -> None:
    """Set context for the current run to enable per-run logging.

    This is called by file_handler for each run, typically once per thread.
    Thread-local storage ensures that concurrent runs do not overwrite each
    other's context or validation logs.
    """
    actual_log_dir = log_dir or Path(__file__).parent / "logs" / "validation"
    actual_log_dir.mkdir(parents=True, exist_ok=True)

    _CURRENT_RUN_CONTEXT.run_id = run_id
    _CURRENT_RUN_CONTEXT.provider = provider
    _CURRENT_RUN_CONTEXT.model = model
    _CURRENT_RUN_CONTEXT.log_dir = str(actual_log_dir)
    _CURRENT_RUN_CONTEXT.timestamp = datetime.utcnow().isoformat()


def _get_context_as_dict() -> Dict[str, Any]:
    """Return the current thread-local run context as a plain dict.

    If no context has been set in this thread, an empty dict is returned.
    """
    if not hasattr(_CURRENT_RUN_CONTEXT, "run_id"):
        return {}

    return {
        "run_id": getattr(_CURRENT_RUN_CONTEXT, "run_id", "unknown"),
        "provider": getattr(_CURRENT_RUN_CONTEXT, "provider", "unknown"),
        "model": getattr(_CURRENT_RUN_CONTEXT, "model", "unknown"),
        "log_dir": getattr(_CURRENT_RUN_CONTEXT, "log_dir", None),
        "timestamp": getattr(_CURRENT_RUN_CONTEXT, "timestamp", None),
    }


def _log_validation_detail(category: str, check: str, result: Any, details: Dict[str, Any]) -> None:
    """
    Log a single validation check with full details to both Python logger and per-run file.

    Args:
        category: 'grounding' or 'reasoning'
        check: Name of the specific check being performed
        result: Boolean or extracted value
        details: Additional context (field values, structure info, etc.)
    """
    # Safe serialization for logging
    try:
        safe_details = _serialize_for_json(details)
    except Exception:
        safe_details = {"error": "Failed to serialize details"}

    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "category": category,
        "check": check,
        "result": result,
        "details": safe_details,
    }

    # Log to Python logger at DEBUG level
    try:
        details_str = json.dumps(safe_details, ensure_ascii=False, default=str)[:500]
    except Exception:
        details_str = "<unserializable>"

    LOG.debug("[VALIDATION] %s.%s = %s | %s", category, check, result, details_str)

    # Also log to console for extreme visibility (ASCII-safe to avoid Windows encoding issues)
    try:
        console_details = json.dumps(safe_details, ensure_ascii=True, default=str)[:200]
        LOG.info("[VALIDATION] %s.%s = %s | details=%s", category, check, result, console_details)
    except Exception:
        # If even ASCII-safe logging fails, just skip
        pass

    # Append to per-run validation log — now captured by SidecarLogHandler (Phase 6B)
    # The LOG.debug and LOG.info calls above carry the validation data.
    # File-based JSONL writes removed.


def _save_full_response(raw_json: Dict[str, Any], stage: str) -> None:
    """Log the complete raw response at a given validation stage (Phase 6B: sidecar, not file)."""
    try:
        shape = _summarize_response_shape(raw_json)
        LOG.info("[VALIDATION RAW] %s response_shape=%s", stage, _compact_log_value(shape, limit=3000))

        # Truncate to avoid huge entries; SidecarLogHandler will capture this as DETAIL.
        summary = json.dumps(raw_json, ensure_ascii=False, default=str)[:8000]
        LOG.debug("Full %s response preview (first 8000 chars): %s", stage, summary)
    except Exception as e:
        LOG.error("Failed to log %s response: %s", stage, e)


def _json_safe_get(d: Any, key: str, default=None):
    try:
        if isinstance(d, dict):
            return d.get(key, default)
    except Exception:
        pass
    return default


def _compact_log_value(value: Any, limit: int = 800) -> str:
    """Serialize a log payload into a bounded JSON string."""
    try:
        text = json.dumps(_serialize_for_json(value), ensure_ascii=False, default=str, sort_keys=True)
    except Exception as exc:
        text = f"<unserializable {type(value).__name__}: {exc}>"
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _summarize_response_shape(raw_json: Any) -> Dict[str, Any]:
    """Build a compact structural summary for validation debugging."""
    if not isinstance(raw_json, dict):
        return {"type": type(raw_json).__name__, "is_dict": False}

    usage = raw_json.get("usage") if isinstance(raw_json.get("usage"), dict) else {}
    choices = raw_json.get("choices") if isinstance(raw_json.get("choices"), list) else []
    output = raw_json.get("output") if isinstance(raw_json.get("output"), list) else []

    choice_summaries: List[Dict[str, Any]] = []
    for cidx, choice in enumerate(choices[:3]):
        if not isinstance(choice, dict):
            choice_summaries.append({"index": cidx, "type": type(choice).__name__})
            continue
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        choice_summaries.append(
            {
                "index": cidx,
                "choice_keys": list(choice.keys()),
                "message_keys": list(message.keys()) if isinstance(message, dict) else [],
                "content_type": type(content).__name__,
                "content_length": len(content) if isinstance(content, (str, list, dict)) else 0,
                "annotation_count": len(message.get("annotations") or []) if isinstance(message, dict) and isinstance(message.get("annotations"), list) else 0,
                "tool_call_count": len(message.get("tool_calls") or []) if isinstance(message, dict) and isinstance(message.get("tool_calls"), list) else 0,
                "has_reasoning_field": any(
                    key in message
                    for key in (
                        "reasoning",
                        "reasoning_content",
                        "reasoning_text",
                        "reasoning_details",
                        "thinking",
                        "thinking_content",
                        "analysis",
                    )
                ) if isinstance(message, dict) else False,
            }
        )

    return {
        "type": type(raw_json).__name__,
        "is_dict": True,
        "top_level_keys": list(raw_json.keys()),
        "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
        "server_tool_use": usage.get("server_tool_use") if isinstance(usage, dict) else None,
        "completion_tokens_details": usage.get("completion_tokens_details") if isinstance(usage, dict) else None,
        "output_tokens_details": usage.get("output_tokens_details") if isinstance(usage, dict) else None,
        "choice_count": len(choices),
        "choice_summaries": choice_summaries,
        "output_count": len(output),
        "top_level_citation_count": len(raw_json.get("citations") or []) if isinstance(raw_json.get("citations"), list) else 0,
        "top_level_search_result_count": len(raw_json.get("search_results") or []) if isinstance(raw_json.get("search_results"), list) else 0,
    }


def _has_url_text(value: Any) -> bool:
    """Return True when a value or nested child contains an explicit web URL."""
    if isinstance(value, str):
        return "http://" in value or "https://" in value
    if isinstance(value, dict):
        return any(_has_url_text(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_url_text(child) for child in value)
    return False


def _is_non_empty_container(value: Any) -> bool:
    """Return True for non-empty provider evidence containers."""
    return isinstance(value, (list, dict)) and len(value) > 0


def _looks_like_web_tool_payload(value: Any) -> bool:
    """Detect tool-call payloads that are specifically search/web/browse related."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str).lower()
    except Exception:
        text = str(value).lower()
    return any(
        token in text
        for token in (
            "web_search",
            "web-search",
            "web search",
            "search_results",
            "search results",
            "browser",
            "browse",
            "grounding",
            "url_citation",
            "citation",
        )
    )


def _annotation_is_grounding_evidence(annotation: Any) -> bool:
    """Detect OpenAI/OpenRouter style citation annotations."""
    if not isinstance(annotation, dict):
        return False
    annotation_type = str(annotation.get("type") or "").lower()
    if annotation_type in {"url_citation", "web_search", "web_search_result", "citation"}:
        return True
    if isinstance(annotation.get("url_citation"), dict):
        return True
    if _has_url_text(annotation):
        return True
    return False


def _collect_openrouter_grounding_evidence(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect every credible OpenRouter grounding signal we know how to identify.

    This is intentionally permissive about response shape, but not permissive
    about evidence: normal text, normal choices, requested tools, model support,
    and reasoning alone do not count.
    """
    evidence: List[Dict[str, Any]] = []
    counters: Dict[str, int] = {
        "server_tool_use": 0,
        "message_annotations": 0,
        "content_annotations": 0,
        "top_level_citations": 0,
        "top_level_search_results": 0,
        "nested_citation_containers": 0,
        "nested_search_containers": 0,
        "nested_grounding_containers": 0,
        "tool_calls": 0,
        "link_fields": 0,
    }

    def add(kind: str, path: str, detail: Optional[Dict[str, Any]] = None) -> None:
        counters[kind] = counters.get(kind, 0) + 1
        evidence.append(
            {
                "kind": kind,
                "path": path,
                "detail": detail or {},
            }
        )

    usage = raw_json.get("usage") or {}
    server_tool_use = usage.get("server_tool_use") or {}
    try:
        web_search_requests = int(server_tool_use.get("web_search_requests") or 0)
    except Exception:
        web_search_requests = 0
    if web_search_requests > 0:
        add("server_tool_use", "usage.server_tool_use.web_search_requests", {"count": web_search_requests})

    # Top-level provider citation/search fields are strong evidence when present.
    for key in ("citations", "citation", "search_results", "web_search_results", "web_results"):
        value = raw_json.get(key)
        if _is_non_empty_container(value):
            if "citation" in key:
                add("top_level_citations", key, {"count": len(value)})
            else:
                add("top_level_search_results", key, {"count": len(value)})

    choices = raw_json.get("choices") or []
    for cidx, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            continue

        annotations = message.get("annotations")
        if isinstance(annotations, list):
            matched = [a for a in annotations if _annotation_is_grounding_evidence(a)]
            for aidx, annotation in enumerate(matched[:20]):
                add(
                    "message_annotations",
                    f"choices[{cidx}].message.annotations[{aidx}]",
                    {
                        "type": annotation.get("type"),
                        "keys": list(annotation.keys()),
                        "has_url": _has_url_text(annotation),
                    },
                )

        for key in ("citations", "citation", "sources", "search_results", "web_search_results", "web_results"):
            value = message.get(key)
            if _is_non_empty_container(value):
                if "citation" in key:
                    add("nested_citation_containers", f"choices[{cidx}].message.{key}", {"count": len(value)})
                elif "search" in key or "web" in key:
                    add("nested_search_containers", f"choices[{cidx}].message.{key}", {"count": len(value)})
                elif _has_url_text(value):
                    add("link_fields", f"choices[{cidx}].message.{key}", {"count": len(value), "has_url": True})

        message_tool_calls = message.get("tool_calls")
        if isinstance(message_tool_calls, list) and _looks_like_web_tool_payload(message_tool_calls):
            add("tool_calls", f"choices[{cidx}].message.tool_calls", {"count": len(message_tool_calls)})

        content = message.get("content")
        if isinstance(content, str):
            pass
        elif isinstance(content, list):
            for bidx, block in enumerate(content):
                if not isinstance(block, dict):
                    continue

                block_annotations = block.get("annotations")
                if isinstance(block_annotations, list):
                    matched = [a for a in block_annotations if _annotation_is_grounding_evidence(a)]
                    for aidx, annotation in enumerate(matched[:20]):
                        add(
                            "content_annotations",
                            f"choices[{cidx}].message.content[{bidx}].annotations[{aidx}]",
                            {
                                "type": annotation.get("type"),
                                "keys": list(annotation.keys()),
                                "has_url": _has_url_text(annotation),
                            },
                        )

                for key in ("url", "uri", "href", "link", "source_url"):
                    if isinstance(block.get(key), str) and _has_url_text(block.get(key)):
                        add(
                            "link_fields",
                            f"choices[{cidx}].message.content[{bidx}].{key}",
                            {"type": block.get("type")},
                        )

                for key in ("citations", "citation", "sources", "source", "search_results", "web_search_results", "web_results"):
                    value = block.get(key)
                    if _is_non_empty_container(value):
                        if "citation" in key:
                            add("nested_citation_containers", f"choices[{cidx}].message.content[{bidx}].{key}", {"count": len(value)})
                        elif "search" in key or "web" in key:
                            add("nested_search_containers", f"choices[{cidx}].message.content[{bidx}].{key}", {"count": len(value)})
                        elif _has_url_text(value):
                            add("link_fields", f"choices[{cidx}].message.content[{bidx}].{key}", {"count": len(value), "has_url": True})

    # Generic recursive scan catches provider variants that put evidence outside choices.
    grounding_container_keys = {
        "groundingmetadata",
        "grounding_metadata",
        "groundingchunks",
        "grounding_chunks",
        "groundingsupports",
        "grounding_supports",
        "web_search_results",
        "web_results",
        "search_results",
    }
    citation_container_keys = {"citations", "citation"}
    link_field_keys = {"url", "uri", "href", "link", "source_url"}
    tool_container_keys = {"tool_calls", "tools", "tool_results"}

    def walk(value: Any, path: str, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_str = str(key)
                key_lower = key_str.lower()
                child_path = f"{path}.{key_str}" if path else key_str

                if key_lower in link_field_keys and isinstance(child, str) and _has_url_text(child):
                    add("link_fields", child_path, {"key": key_str})
                elif key_lower in citation_container_keys and _is_non_empty_container(child):
                    add("nested_citation_containers", child_path, {"count": len(child), "has_url": _has_url_text(child)})
                elif key_lower in grounding_container_keys and _is_non_empty_container(child):
                    kind = "nested_search_containers" if "search" in key_lower or "web" in key_lower else "nested_grounding_containers"
                    add(kind, child_path, {"count": len(child), "has_url": _has_url_text(child)})
                elif key_lower in tool_container_keys and _is_non_empty_container(child) and _looks_like_web_tool_payload(child):
                    add("tool_calls", child_path, {"count": len(child) if hasattr(child, "__len__") else None})

                walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            for idx, child in enumerate(value[:200]):
                walk(child, f"{path}[{idx}]", depth + 1)

    walk(raw_json, "")

    # De-duplicate repeated recursive and explicit hits by kind+path.
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in evidence:
        key = (item.get("kind"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    deduped_counts: Dict[str, int] = {}
    for item in deduped:
        kind = str(item.get("kind") or "unknown")
        deduped_counts[kind] = deduped_counts.get(kind, 0) + 1

    return {
        "passed": bool(deduped),
        "evidence_count": len(deduped),
        "evidence_counts": deduped_counts,
        "evidence": deduped[:40],
        "truncated_evidence_count": max(0, len(deduped) - 40),
        "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
        "server_tool_use": server_tool_use,
        "web_search_requests": web_search_requests,
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "top_level_keys": list(raw_json.keys()) if isinstance(raw_json, dict) else [],
    }


def _collect_openrouter_reasoning_evidence(raw_json: Dict[str, Any], provider: Optional[Any] = None) -> Dict[str, Any]:
    """
    Collect explicit OpenRouter reasoning signals across provider response variants.

    This accepts only provider-visible reasoning proof: reasoning token counts,
    explicit reasoning/thinking fields, reasoning content blocks, or a successful
    provider extractor. Ordinary answer text does not count.
    """
    evidence: List[Dict[str, Any]] = []
    provider_extract_error = None

    def add(kind: str, path: str, detail: Optional[Dict[str, Any]] = None) -> None:
        evidence.append({"kind": kind, "path": path, "detail": detail or {}})

    usage = raw_json.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    try:
        reasoning_tokens = max(
            int(completion_details.get("reasoning_tokens") or 0),
            int(output_details.get("reasoning_tokens") or 0),
            int(usage.get("reasoning_tokens") or 0),
        )
    except Exception:
        reasoning_tokens = 0
    if reasoning_tokens > 0:
        add("usage_reasoning_tokens", "usage.*.reasoning_tokens", {"count": reasoning_tokens})

    provider_reasoning = None
    try:
        if provider is not None and hasattr(provider, "extract_reasoning"):
            provider_reasoning = provider.extract_reasoning(raw_json)
    except Exception as exc:
        provider_extract_error = str(exc)

    has_provider_reasoning = isinstance(provider_reasoning, str) and provider_reasoning.strip() != ""
    if has_provider_reasoning:
        add(
            "provider_extract_reasoning",
            "provider.extract_reasoning",
            {"length": len(provider_reasoning), "preview": provider_reasoning[:200]},
        )

    explicit_reasoning_keys = {
        "reasoning",
        "reasoning_content",
        "reasoning_text",
        "reasoning_details",
        "thinking",
        "thinking_content",
        "thinking_blocks",
        "thoughts",
        "analysis",
        "redacted_reasoning",
    }
    reasoning_block_types = {
        "reasoning",
        "reasoning_text",
        "thinking",
        "thinking_text",
        "analysis",
        "redacted_reasoning",
    }

    def has_non_empty_reasoning_value(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip() != ""
        if isinstance(value, (list, dict)):
            return len(value) > 0
        return value is not None

    def inspect_container(container: Dict[str, Any], path: str) -> None:
        for key, value in container.items():
            key_lower = str(key).lower()
            child_path = f"{path}.{key}"
            if key_lower in explicit_reasoning_keys and has_non_empty_reasoning_value(value):
                add(
                    "explicit_reasoning_field",
                    child_path,
                    {
                        "key": key,
                        "type": type(value).__name__,
                        "length": len(value) if isinstance(value, (str, list, dict)) else None,
                    },
                )

    choices = raw_json.get("choices") or []
    for cidx, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if isinstance(message, dict):
            inspect_container(message, f"choices[{cidx}].message")
            content = message.get("content")
            if isinstance(content, list):
                for bidx, block in enumerate(content):
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "").lower()
                    if block_type in reasoning_block_types and has_non_empty_reasoning_value(block):
                        add(
                            "reasoning_content_block",
                            f"choices[{cidx}].message.content[{bidx}]",
                            {"type": block.get("type"), "keys": list(block.keys())},
                        )
                    inspect_container(block, f"choices[{cidx}].message.content[{bidx}]")

    output = raw_json.get("output") or []
    if isinstance(output, list):
        for oidx, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if item_type in reasoning_block_types and has_non_empty_reasoning_value(item):
                add("reasoning_output_item", f"output[{oidx}]", {"type": item.get("type"), "keys": list(item.keys())})
            inspect_container(item, f"output[{oidx}]")
            content = item.get("content") or item.get("contents")
            if isinstance(content, list):
                for cidx, block in enumerate(content):
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "").lower()
                    if block_type in reasoning_block_types and has_non_empty_reasoning_value(block):
                        add(
                            "reasoning_output_content_block",
                            f"output[{oidx}].content[{cidx}]",
                            {"type": block.get("type"), "keys": list(block.keys())},
                        )
                    inspect_container(block, f"output[{oidx}].content[{cidx}]")

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in evidence:
        key = (item.get("kind"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    evidence_counts: Dict[str, int] = {}
    for item in deduped:
        kind = str(item.get("kind") or "unknown")
        evidence_counts[kind] = evidence_counts.get(kind, 0) + 1

    return {
        "passed": bool(deduped),
        "evidence_count": len(deduped),
        "evidence_counts": evidence_counts,
        "evidence": deduped[:40],
        "truncated_evidence_count": max(0, len(deduped) - 40),
        "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
        "completion_tokens_details": completion_details,
        "output_tokens_details": output_details,
        "reasoning_tokens": reasoning_tokens,
        "has_provider_reasoning": has_provider_reasoning,
        "provider_reasoning_length": len(provider_reasoning) if isinstance(provider_reasoning, str) else 0,
        "provider_extract_error": provider_extract_error,
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "output_count": len(output) if isinstance(output, list) else 0,
        "top_level_keys": list(raw_json.keys()) if isinstance(raw_json, dict) else [],
    }


def _detect_openrouter_grounding(raw_json: Dict[str, Any]) -> bool:
    """
    Evidence-driven OpenRouter grounding detection.

    OpenRouter passes grounding when any credible web/search/citation/source
    proof is present. Normal text, requested tools, model support, and reasoning
    alone do not count as grounding.
    """
    grounding_summary = _collect_openrouter_grounding_evidence(raw_json)
    result = bool(grounding_summary.get("passed"))

    _log_validation_detail(
        "grounding",
        "openrouter.evidence_result",
        result,
        grounding_summary,
    )
    LOG.info(
        "[OPENROUTER VALIDATION] grounding_evidence=%s",
        _compact_log_value(grounding_summary, limit=3000),
    )
    if result:
        LOG.info(
            "=== GROUNDING DETECTION END: TRUE (OpenRouter evidence found: %s) ===",
            _compact_log_value(grounding_summary.get("evidence_counts", {}), limit=800),
        )
    else:
        LOG.warning(
            "[OPENROUTER VALIDATION] grounding_rejected_no_evidence=%s",
            _compact_log_value(grounding_summary, limit=3000),
        )
        LOG.info("=== GROUNDING DETECTION END: FALSE (OpenRouter no credible grounding evidence) ===")
    return result


def _detect_perplexity_grounding(raw_json: Dict[str, Any]) -> bool:
    """
    Strict native Perplexity grounding detection.

    Perplexity passes grounding when both are true:
    1. usage.num_search_queries > 0
    2. The response exposes citation/search evidence via citations or search_results
    """
    usage = raw_json.get("usage") or {}
    try:
        num_search_queries = int(usage.get("num_search_queries") or 0)
    except Exception:
        num_search_queries = 0

    search_proven = num_search_queries > 0
    _log_validation_detail(
        "grounding",
        "perplexity.search_queries",
        search_proven,
        {
            "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
            "num_search_queries": num_search_queries,
        },
    )

    citations = raw_json.get("citations")
    search_results = raw_json.get("search_results")
    citation_count = len(citations) if isinstance(citations, list) else 0
    search_result_count = len(search_results) if isinstance(search_results, list) else 0
    citation_proven = citation_count > 0 or search_result_count > 0

    _log_validation_detail(
        "grounding",
        "perplexity.citation_proof",
        citation_proven,
        {
            "citation_count": citation_count,
            "search_result_count": search_result_count,
            "citations_preview": citations[:3] if isinstance(citations, list) else None,
            "search_results_preview": search_results[:2] if isinstance(search_results, list) else None,
        },
    )

    result = search_proven and citation_proven
    summary = {
        "num_search_queries": num_search_queries,
        "citation_count": citation_count,
        "search_result_count": search_result_count,
        "passed": result,
    }
    _log_validation_detail("grounding", "perplexity.strict_result", result, summary)
    LOG.info("[PERPLEXITY VALIDATION] grounding_summary=%s", _compact_log_value(summary, limit=1000))
    return result


def detect_grounding(raw_json: Dict[str, Any], provider: Optional[Any] = None) -> bool:
    """
    Heuristics to detect that provider-side grounding/search was used.
    Every check is logged with field-by-field inspection.
    """
    LOG.info("=== GROUNDING DETECTION START ===")
    _save_full_response(raw_json, "grounding_check")
    ctx_provider = _get_context_as_dict().get("provider", "").lower()
    provider_name = getattr(provider, "__name__", "") if provider is not None else ""
    provider_name = str(provider_name).lower()

    if not isinstance(raw_json, dict):
        _log_validation_detail("grounding", "type_check", False, {"type": str(type(raw_json)), "reason": "not a dict"})
        LOG.info("=== GROUNDING DETECTION END: FALSE (not dict) ===")
        return False

    # Log top-level keys for structure visibility
    top_keys = list(raw_json.keys())
    _log_validation_detail("grounding", "structure", None, {"top_level_keys": top_keys, "key_count": len(top_keys)})

    if ctx_provider == "openrouter" or "openrouter" in provider_name:
        return _detect_openrouter_grounding(raw_json)
    if ctx_provider == "perplexity" or "perplexity" in provider_name:
        return _detect_perplexity_grounding(raw_json)

    # Check 0: Anthropic/Claude content blocks for tool_use or server_tool_use
    try:
        content_blocks = raw_json.get("content")
        _log_validation_detail(
            "grounding",
            "content_blocks",
            isinstance(content_blocks, list),
            {"type": type(content_blocks).__name__, "length": len(content_blocks) if isinstance(content_blocks, list) else 0},
        )

        if isinstance(content_blocks, list):
            for idx, block in enumerate(content_blocks):
                if not isinstance(block, dict):
                    _log_validation_detail("grounding", f"content[{idx}]", None, {"type": type(block).__name__, "skipped": True})
                    continue
                btype = block.get("type")
                name = block.get("name") or block.get("tool_name")
                has_tool = btype in ("tool_use", "server_tool_use", "web_search_tool_result")
                has_web_name = isinstance(name, str) and "web_search" in name.lower()
                has_result = isinstance(block.get("results"), list) or isinstance(block.get("search_results"), list)
                _log_validation_detail(
                    "grounding",
                    f"content[{idx}].tool",
                    has_tool or has_web_name or has_result,
                    {
                        "type": btype,
                        "name": name,
                        "keys": list(block.keys()),
                        "has_results": has_result,
                    },
                )
                if has_tool or has_web_name or has_result:
                    LOG.info("=== GROUNDING DETECTION END: TRUE (Anthropic content tool) ===")
                    return True
    except Exception as e:
        _log_validation_detail("grounding", "content_blocks", False, {"error": str(e)})

    # Check 1: Direct tool call evidence
    try:
        tc = raw_json.get("tool_calls")
        tc_type = type(tc).__name__
        tc_len = len(tc) if isinstance(tc, list) else 0
        tc_present = isinstance(tc, list) and len(tc) > 0

        _log_validation_detail("grounding", "tool_calls", tc_present, {
            "type": tc_type,
            "length": tc_len,
            "value_preview": str(tc)[:200] if tc else None
        })

        if tc_present:
            LOG.info("=== GROUNDING DETECTION END: TRUE (tool_calls found) ===")
            return True
    except Exception as e:
        _log_validation_detail("grounding", "tool_calls", False, {"error": str(e)})

    # Check 2: Tools field
    try:
        tls = raw_json.get("tools")
        tls_type = type(tls).__name__
        tls_len = len(tls) if isinstance(tls, list) else 0
        tls_present = isinstance(tls, list) and len(tls) > 0

        _log_validation_detail("grounding", "tools", tls_present, {
            "type": tls_type,
            "length": tls_len,
            "value_preview": str(tls)[:200] if tls else None
        })

        if tls_present:
            LOG.info("=== GROUNDING DETECTION END: TRUE (tools found) ===")
            return True
    except Exception as e:
        _log_validation_detail("grounding", "tools", False, {"error": str(e)})

    # Check 3: Scan output blocks for URLs/citations
    try:
        output = raw_json.get("output") or raw_json.get("outputs")
        output_type = type(output).__name__
        output_len = len(output) if isinstance(output, list) else 0

        _log_validation_detail("grounding", "output_blocks", None, {
            "type": output_type,
            "length": output_len,
            "present": output is not None
        })

        if isinstance(output, list):
            for idx, item in enumerate(output):
                if not isinstance(item, dict):
                    _log_validation_detail("grounding", f"output[{idx}]", None, {"type": type(item).__name__, "skipped": True})
                    continue

                item_keys = list(item.keys())
                _log_validation_detail("grounding", f"output[{idx}].keys", None, {"keys": item_keys})

                content = item.get("content") or item.get("contents")
                content_type = type(content).__name__
                content_len = len(content) if isinstance(content, list) else 0

                if isinstance(content, list):
                    for cidx, c in enumerate(content):
                        if isinstance(c, dict):
                            c_keys = list(c.keys())
                            has_link_fields = any(k in c for k in ("source", "url", "link", "href"))

                            _log_validation_detail("grounding", f"output[{idx}].content[{cidx}]", has_link_fields, {
                                "type": "dict",
                                "keys": c_keys,
                                "has_link_fields": has_link_fields,
                                "link_field_types": {k: type(c.get(k)).__name__ for k in ("source", "url", "link", "href") if k in c}
                            })

                            if has_link_fields:
                                LOG.info("=== GROUNDING DETECTION END: TRUE (link fields in output content) ===")
                                return True

                            t = c.get("text")
                            if isinstance(t, str):
                                has_url = "http://" in t or "https://" in t
                                has_citation = "Citation:" in t or "[source]" in t

                                _log_validation_detail("grounding", f"output[{idx}].content[{cidx}].text", has_url or has_citation, {
                                    "length": len(t),
                                    "has_url": has_url,
                                    "has_citation": has_citation,
                                    "preview": t[:200]
                                })

                                if has_url or has_citation:
                                    LOG.info("=== GROUNDING DETECTION END: TRUE (URLs/citations in text) ===")
                                    return True
                        elif isinstance(c, str):
                            has_url = "http://" in c or "https://" in c
                            has_citation = "Citation:" in c

                            _log_validation_detail("grounding", f"output[{idx}].content[{cidx}]", has_url or has_citation, {
                                "type": "str",
                                "length": len(c),
                                "has_url": has_url,
                                "has_citation": has_citation,
                                "preview": c[:200]
                            })

                            if has_url or has_citation:
                                LOG.info("=== GROUNDING DETECTION END: TRUE (URLs/citations in string content) ===")
                                return True
    except Exception as e:
        _log_validation_detail("grounding", "output_scan", False, {"error": str(e)})

    # Check 4: String search in full JSON
    try:
        s = json.dumps(raw_json, ensure_ascii=False)
        has_web_search = "web_search" in s
        has_tool_call = "tool_call" in s or "tool_calls" in s

        _log_validation_detail("grounding", "json_string_search", has_web_search or has_tool_call, {
            "json_length": len(s),
            "has_web_search": has_web_search,
            "has_tool_call": has_tool_call,
            "json_preview": s[:500]
        })

        if has_web_search or has_tool_call:
            LOG.info("=== GROUNDING DETECTION END: TRUE (string search match) ===")
            return True
    except Exception as e:
        _log_validation_detail("grounding", "json_string_search", False, {"error": str(e)})

    # Check 5: Gemini-specific checks
    try:
        cands = raw_json.get("candidates")
        cands_type = type(cands).__name__
        cands_len = len(cands) if isinstance(cands, list) else 0

        _log_validation_detail("grounding", "candidates", None, {
            "type": cands_type,
            "length": cands_len,
            "present": cands is not None
        })

        if isinstance(cands, list):
            for cidx, cand in enumerate(cands):
                cand_keys = list(cand.keys()) if isinstance(cand, dict) else []
                _log_validation_detail("grounding", f"candidates[{cidx}].keys", None, {"keys": cand_keys})

                # Check groundingMetadata
                gm = _json_safe_get(cand, "groundingMetadata")
                gm_type = type(gm).__name__
                gm_keys = list(gm.keys()) if isinstance(gm, dict) else []
                gm_len = len(gm) if isinstance(gm, dict) else 0

                _log_validation_detail("grounding", f"candidates[{cidx}].groundingMetadata", None, {
                    "type": gm_type,
                    "keys": gm_keys,
                    "length": gm_len,
                    "present": gm is not None
                })

                if isinstance(gm, dict) and len(gm) > 0:
                    wsq = gm.get("webSearchQueries")
                    gs = gm.get("groundingSupports")
                    cs = gm.get("confidenceScores")
                    sep = gm.get("searchEntryPoint")

                    _log_validation_detail("grounding", f"candidates[{cidx}].groundingMetadata.fields", None, {
                        "webSearchQueries": {"type": type(wsq).__name__, "length": len(wsq) if isinstance(wsq, list) else 0, "present": wsq is not None, "value": wsq},
                        "groundingSupports": {"type": type(gs).__name__, "length": len(gs) if isinstance(gs, list) else 0, "present": gs is not None},
                        "confidenceScores": {"type": type(cs).__name__, "present": cs is not None, "value": cs},
                        "searchEntryPoint": {"type": type(sep).__name__, "present": sep is not None, "value": sep}
                    })

                    if wsq or gs or cs or sep:
                        LOG.info("=== GROUNDING DETECTION END: TRUE (Gemini groundingMetadata fields) ===")
                        return True

                    # Any non-empty groundingMetadata counts
                    LOG.info("=== GROUNDING DETECTION END: TRUE (non-empty groundingMetadata) ===")
                    return True

                # Check citations
                cit = _json_safe_get(cand, "citations")
                cit_type = type(cit).__name__
                cit_len = len(cit) if isinstance(cit, list) else 0

                _log_validation_detail("grounding", f"candidates[{cidx}].citations", cit_len > 0, {
                    "type": cit_type,
                    "length": cit_len,
                    "value_preview": str(cit)[:200] if cit else None
                })

                if isinstance(cit, list) and len(cit) > 0:
                    LOG.info("=== GROUNDING DETECTION END: TRUE (citations present) ===")
                    return True

                # Check citationMetadata
                citm = _json_safe_get(cand, "citationMetadata")
                citm_type = type(citm).__name__
                citm_len = len(citm) if isinstance(citm, dict) else 0

                _log_validation_detail("grounding", f"candidates[{cidx}].citationMetadata", citm_len > 0, {
                    "type": citm_type,
                    "length": citm_len,
                    "value_preview": str(citm)[:200] if citm else None
                })

                if isinstance(citm, dict) and len(citm) > 0:
                    LOG.info("=== GROUNDING DETECTION END: TRUE (citationMetadata present) ===")
                    return True

                # Check content.parts
                content = _json_safe_get(cand, "content") or {}
                parts = _json_safe_get(content, "parts")
                parts_type = type(parts).__name__
                parts_len = len(parts) if isinstance(parts, list) else 0

                _log_validation_detail("grounding", f"candidates[{cidx}].content.parts", None, {
                    "type": parts_type,
                    "length": parts_len
                })

                if isinstance(parts, list):
                    for pidx, p in enumerate(parts):
                        if isinstance(p, dict):
                            p_keys = list(p.keys())
                            cm = _json_safe_get(p, "citationMetadata")
                            cm_type = type(cm).__name__
                            cm_len = len(cm) if isinstance(cm, dict) else 0

                            _log_validation_detail("grounding", f"candidates[{cidx}].content.parts[{pidx}].citationMetadata", cm_len > 0, {
                                "part_keys": p_keys,
                                "type": cm_type,
                                "length": cm_len,
                                "value": cm
                            })

                            if isinstance(cm, dict) and len(cm) > 0:
                                LOG.info("=== GROUNDING DETECTION END: TRUE (part citationMetadata) ===")
                                return True

                            # Check for URI fields
                            uri_fields = {}
                            for k in ("uri", "url", "link", "href"):
                                v = p.get(k)
                                if isinstance(v, str) and v.strip():
                                    uri_fields[k] = v

                            _log_validation_detail("grounding", f"candidates[{cidx}].content.parts[{pidx}].uri_fields", len(uri_fields) > 0, {
                                "fields_found": uri_fields
                            })

                            if uri_fields:
                                LOG.info("=== GROUNDING DETECTION END: TRUE (URI fields in parts) ===")
                                return True
    except Exception as e:
        _log_validation_detail("grounding", "gemini_checks", False, {"error": str(e)})

    # Check 6: Tavily-specific checks (only if provider is tavily)
    try:
        if ctx_provider == "tavily":
            # Top-level sources list with URLs/titles counts as grounding evidence
            sources = raw_json.get("sources")
            src_present = isinstance(sources, list) and any(
                isinstance(s, dict) and (s.get("url") or s.get("title")) for s in sources or []
            )
            _log_validation_detail("grounding", "tavily.sources", src_present, {
                "type": type(sources).__name__,
                "length": len(sources) if isinstance(sources, list) else 0,
                "sample": sources[:2] if isinstance(sources, list) else None
            })
            if src_present:
                LOG.info("=== GROUNDING DETECTION END: TRUE (tavily sources) ===")
                return True

            # Scan top-level content/report/answer for URLs or embedded citations
            content = raw_json.get("content") or raw_json.get("report") or raw_json.get("answer")
            if isinstance(content, str):
                has_url = "http://" in content or "https://" in content
                has_citation = "Citation:" in content
                _log_validation_detail("grounding", "tavily.content_str", has_url or has_citation, {
                    "length": len(content),
                    "has_url": has_url,
                    "has_citation": has_citation,
                    "preview": content[:200]
                })
                if has_url or has_citation:
                    LOG.info("=== GROUNDING DETECTION END: TRUE (tavily content URLs) ===")
                    return True
            elif isinstance(content, (dict, list)):
                # Walk shallowly for url/link/href fields
                def _contains_uri(obj: Any) -> bool:
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("url", "link", "href") and isinstance(v, str) and v.strip():
                                return True
                            if isinstance(v, (dict, list)) and _contains_uri(v):
                                return True
                    elif isinstance(obj, list):
                        for item in obj:
                            if _contains_uri(item):
                                return True
                    return False

                has_uri = _contains_uri(content)
                _log_validation_detail("grounding", "tavily.content_nested", has_uri, {
                    "type": type(content).__name__
                })
                if has_uri:
                    LOG.info("=== GROUNDING DETECTION END: TRUE (tavily nested URLs) ===")
                    return True
    except Exception as e:
        _log_validation_detail("grounding", "tavily_checks", False, {"error": str(e)})

    LOG.info("=== GROUNDING DETECTION END: FALSE (no matches) ===")
    return False


def _extract_reasoning_generic(raw_json: Dict[str, Any]) -> Optional[str]:
    """
    Provider-agnostic best-effort extraction of reasoning-like content.
    All extraction attempts logged.
    """
    LOG.debug("Starting generic reasoning extraction")

    if not isinstance(raw_json, dict):
        _log_validation_detail("reasoning", "generic.type_check", False, {"type": str(type(raw_json))})
        return None

    # Check top-level reasoning
    r = raw_json.get("reasoning")
    r_type = type(r).__name__

    if isinstance(r, str) and r.strip():
        _log_validation_detail("reasoning", "generic.top_level_str", True, {
            "type": r_type,
            "length": len(r),
            "preview": r[:200]
        })
        return r.strip()

    if isinstance(r, dict):
        parts: List[str] = []
        for k, v in r.items():
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())

        _log_validation_detail("reasoning", "generic.top_level_dict", len(parts) > 0, {
            "type": r_type,
            "keys": list(r.keys()),
            "string_parts_found": len(parts)
        })

        if parts:
            return "\n\n".join(parts)

    # Check outputs
    output = raw_json.get("output") or raw_json.get("outputs")
    output_type = type(output).__name__

    _log_validation_detail("reasoning", "generic.output", None, {
        "type": output_type,
        "length": len(output) if isinstance(output, list) else 0
    })

    if isinstance(output, list):
        for idx, item in enumerate(output):
            if not isinstance(item, dict):
                continue

            # Check reasoning field
            ri = item.get("reasoning")
            if isinstance(ri, str) and ri.strip():
                _log_validation_detail("reasoning", f"generic.output[{idx}].reasoning", True, {
                    "type": "str",
                    "length": len(ri),
                    "preview": ri[:200]
                })
                return ri.strip()

            if isinstance(ri, dict):
                parts = []
                for v in ri.values():
                    if isinstance(v, str) and v.strip():
                        parts.append(v.strip())

                _log_validation_detail("reasoning", f"generic.output[{idx}].reasoning", len(parts) > 0, {
                    "type": "dict",
                    "keys": list(ri.keys()),
                    "string_parts": len(parts)
                })

                if parts:
                    return "\n\n".join(parts)

            # Check content blocks
            content = item.get("content") or item.get("contents")
            if isinstance(content, list):
                for cidx, c in enumerate(content):
                    if isinstance(c, dict):
                        t = c.get("type")
                        text = c.get("text")

                        is_reasoning_type = t in {"reasoning", "analysis", "explanation"}
                        has_text = isinstance(text, str) and text.strip()

                        _log_validation_detail("reasoning", f"generic.output[{idx}].content[{cidx}]", is_reasoning_type and has_text, {
                            "type": t,
                            "has_text": has_text,
                            "text_length": len(text) if isinstance(text, str) else 0
                        })

                        if is_reasoning_type and has_text:
                            return text.strip()

    _log_validation_detail("reasoning", "generic.final", False, {"reason": "no reasoning content found"})
    return None


def detect_reasoning(raw_json: Dict[str, Any], provider: Optional[Any] = None) -> bool:
    """
    Detect that provider returned reasoning.
    All checks logged.
    """
    LOG.info("=== REASONING DETECTION START ===")
    _save_full_response(raw_json, "reasoning_check")

    provider_name = provider.__name__ if provider and hasattr(provider, '__name__') else str(type(provider))
    ctx_provider = _get_context_as_dict().get("provider", "").lower()
    _log_validation_detail("reasoning", "provider", None, {"provider": provider_name, "has_extract_reasoning": hasattr(provider, "extract_reasoning") if provider else False})

    if ctx_provider == "openrouter" or "openrouter" in str(provider_name).lower():
        reasoning_summary = _collect_openrouter_reasoning_evidence(raw_json, provider=provider)
        result = bool(reasoning_summary.get("passed"))
        _log_validation_detail(
            "reasoning",
            "openrouter.evidence_result",
            result,
            reasoning_summary,
        )
        LOG.info(
            "[OPENROUTER VALIDATION] reasoning_evidence=%s",
            _compact_log_value(reasoning_summary, limit=3000),
        )
        if result:
            LOG.info(
                "=== REASONING DETECTION END: TRUE (OpenRouter evidence found: %s) ===",
                _compact_log_value(reasoning_summary.get("evidence_counts", {}), limit=800),
            )
        else:
            LOG.warning(
                "[OPENROUTER VALIDATION] reasoning_rejected_no_evidence=%s",
                _compact_log_value(reasoning_summary, limit=3000),
            )
            LOG.info("=== REASONING DETECTION END: FALSE (OpenRouter no explicit reasoning evidence) ===")
        return result

    if ctx_provider == "perplexity" or "perplexity" in str(provider_name).lower():
        usage = raw_json.get("usage") or {}
        try:
            reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
        except Exception:
            reasoning_tokens = 0

        _log_validation_detail(
            "reasoning",
            "perplexity.reasoning_tokens",
            reasoning_tokens > 0,
            {
                "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
                "reasoning_tokens": reasoning_tokens,
            },
        )

        provider_reasoning = None
        try:
            if provider is not None and hasattr(provider, "extract_reasoning"):
                provider_reasoning = provider.extract_reasoning(raw_json)
        except Exception as e:
            _log_validation_detail("reasoning", "perplexity.provider_extract", False, {"error": str(e)})

        has_provider_reasoning = isinstance(provider_reasoning, str) and provider_reasoning.strip() != ""
        result = reasoning_tokens > 0 or has_provider_reasoning
        summary = {
            "reasoning_tokens": reasoning_tokens,
            "has_provider_reasoning": has_provider_reasoning,
            "provider_reasoning_length": len(provider_reasoning) if isinstance(provider_reasoning, str) else 0,
            "passed": result,
        }
        _log_validation_detail("reasoning", "perplexity.strict_result", result, summary)
        LOG.info("[PERPLEXITY VALIDATION] reasoning_summary=%s", _compact_log_value(summary, limit=1000))
        return result

    # Try provider-specific extraction
    try:
        if provider is not None and hasattr(provider, "extract_reasoning"):
            r = provider.extract_reasoning(raw_json)
            r_type = type(r).__name__
            r_len = len(r) if isinstance(r, str) else 0
            has_reasoning = isinstance(r, str) and r.strip()

            _log_validation_detail("reasoning", "provider.extract_reasoning", has_reasoning, {
                "type": r_type,
                "length": r_len,
                "preview": r[:200] if isinstance(r, str) else None
            })

            if has_reasoning:
                LOG.info("=== REASONING DETECTION END: TRUE (provider extractor) ===")
                return True
    except Exception as e:
        _log_validation_detail("reasoning", "provider.extract_reasoning", False, {"error": str(e)})

    # Gemini-specific heuristics
    try:
        cands = raw_json.get("candidates")
        if isinstance(cands, list) and cands:
            gm = cands[0].get("groundingMetadata")

            if isinstance(gm, dict):
                wsq = gm.get("webSearchQueries")
                gs = gm.get("groundingSupports")
                sc = gm.get("supportingContent")
                cs = gm.get("confidenceScores")

                has_signals = bool(wsq or gs or sc or cs)

                _log_validation_detail("reasoning", "gemini.groundingMetadata_as_reasoning", has_signals, {
                    "has_webSearchQueries": bool(wsq),
                    "has_groundingSupports": bool(gs),
                    "has_supportingContent": bool(sc),
                    "has_confidenceScores": bool(cs),
                    "webSearchQueries": wsq,
                    "groundingSupports_count": len(gs) if isinstance(gs, list) else 0,
                    "confidenceScores": cs
                })

                if has_signals:
                    LOG.info("=== REASONING DETECTION END: TRUE (Gemini groundingMetadata as reasoning) ===")
                    return True

            # Check content parts
            content = cands[0].get("content") or {}
            parts = content.get("parts")

            if isinstance(parts, list):
                for pidx, p in enumerate(parts):
                    if isinstance(p, dict):
                        t = p.get("text")
                        has_text = isinstance(t, str) and t.strip()

                        _log_validation_detail("reasoning", f"gemini.content.parts[{pidx}].text", has_text, {
                            "type": type(t).__name__,
                            "length": len(t) if isinstance(t, str) else 0,
                            "preview": t[:200] if isinstance(t, str) else None
                        })

                        if has_text:
                            LOG.info("=== REASONING DETECTION END: TRUE (Gemini content text) ===")
                            return True
    except Exception as e:
        _log_validation_detail("reasoning", "gemini.heuristics", False, {"error": str(e)})

    # Generic extraction
    generic = _extract_reasoning_generic(raw_json)
    has_generic = isinstance(generic, str) and generic.strip() != ""

    _log_validation_detail("reasoning", "generic", has_generic, {
        "type": type(generic).__name__,
        "length": len(generic) if isinstance(generic, str) else 0,
        "preview": generic[:200] if isinstance(generic, str) else None
    })

    if has_generic:
        LOG.info("=== REASONING DETECTION END: TRUE (generic extraction) ===")
    else:
        LOG.info("=== REASONING DETECTION END: FALSE (no matches) ===")

    return has_generic


def assert_grounding_and_reasoning(raw_json: Dict[str, Any], provider: Optional[Any] = None) -> None:
    """
    Assert both grounding and reasoning are present; raise RuntimeError if either is missing.
    Full validation summary saved before raising.

    Provider-level override:
    - If provider has REQUIRES_GROUNDING = False, grounding check is skipped.
    - Reasoning check still applies unless provider has REQUIRES_REASONING = False.
    """
    LOG.info("=" * 80)
    LOG.info("VALIDATION CHECKPOINT: assert_grounding_and_reasoning")
    LOG.info("=" * 80)

    # Check provider-level flags for grounding/reasoning requirements
    requires_grounding = getattr(provider, "REQUIRES_GROUNDING", True)
    requires_reasoning = getattr(provider, "REQUIRES_REASONING", True)

    provider_name = getattr(provider, "__name__", str(provider)) if provider else "unknown"
    LOG.info("Provider: %s | REQUIRES_GROUNDING=%s | REQUIRES_REASONING=%s",
             provider_name, requires_grounding, requires_reasoning)
    if "openrouter" in str(provider_name).lower():
        LOG.info(
            "[OPENROUTER VALIDATION] run_context=%s",
            _compact_log_value(_get_context_as_dict(), limit=1000),
        )
    if "perplexity" in str(provider_name).lower():
        LOG.info(
            "[PERPLEXITY VALIDATION] run_context=%s",
            _compact_log_value(_get_context_as_dict(), limit=1000),
        )

    # Detect grounding only if required
    if requires_grounding:
        g = detect_grounding(raw_json, provider=provider)
    else:
        g = True  # Skip grounding check
        LOG.info("Grounding check SKIPPED for provider: %s", provider_name)

    # Detect reasoning only if required
    if requires_reasoning:
        r = detect_reasoning(raw_json, provider=provider)
    else:
        r = True  # Skip reasoning check
        LOG.info("Reasoning check SKIPPED for provider: %s", provider_name)

    # Log final summary
    validation_summary = {
        "timestamp": datetime.utcnow().isoformat(),
        "run_context": _serialize_for_json(_get_context_as_dict()),
        "provider": provider_name,
        "requires_grounding": requires_grounding,
        "requires_reasoning": requires_reasoning,
        "grounding_detected": g if requires_grounding else "skipped",
        "reasoning_detected": r if requires_reasoning else "skipped",
        "validation_passed": g and r
    }

    _log_validation_detail("summary", "final", g and r, validation_summary)
    if "openrouter" in str(provider_name).lower():
        LOG.info(
            "[OPENROUTER VALIDATION] final_summary=%s",
            _compact_log_value(validation_summary, limit=1000),
        )
    if "perplexity" in str(provider_name).lower():
        LOG.info(
            "[PERPLEXITY VALIDATION] final_summary=%s",
            _compact_log_value(validation_summary, limit=1000),
        )

    grounding_status = g if requires_grounding else "skipped"
    reasoning_status = r if requires_reasoning else "skipped"
    LOG.info("VALIDATION SUMMARY: grounding=%s reasoning=%s PASSED=%s", grounding_status, reasoning_status, g and r)
    LOG.info("="*80 + " [VALIDATION SUMMARY] grounding=%s reasoning=%s PASSED=%s " + "="*80,
             grounding_status, reasoning_status, g and r)

    missing = []
    if not g:
        missing.append("grounding (web_search/citations)")
    if not r:
        missing.append("reasoning (thinking/rationale)")

    if missing:
        error_msg = "Provider response failed mandatory checks: missing " + " and ".join(missing) + ". Enforcement is strict; no report may be written. See logs for details."

        # Log failure report — captured by SidecarLogHandler (Phase 6B)
        LOG.warning(
            "VALIDATION FAILURE REPORT: missing=%s summary=%s",
            missing,
            json.dumps(validation_summary, ensure_ascii=False, default=str)[:500],
        )
        if "openrouter" in str(provider_name).lower():
            LOG.warning(
                "[OPENROUTER VALIDATION] failure_missing=%s summary=%s",
                missing,
                _compact_log_value(validation_summary, limit=1000),
            )
        if "perplexity" in str(provider_name).lower():
            LOG.warning(
                "[PERPLEXITY VALIDATION] failure_missing=%s summary=%s",
                missing,
                _compact_log_value(validation_summary, limit=1000),
            )

        LOG.error("VALIDATION FAILED: %s", error_msg)
        # Raise ValidationError with classification info for intelligent retry
        raise ValidationError(error_msg, missing_grounding=not g, missing_reasoning=not r)

    LOG.info("VALIDATION PASSED: Both grounding and reasoning detected")
    LOG.info("=" * 80)
