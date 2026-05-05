"""
OpenRouter provider adapter for FPF.

Guarantees (non-configurable):
- Server-side web search is always requested via ``openrouter:web_search``.
- Reasoning is always enabled.
- Requests fail fast at runtime if the selected model lacks tools or reasoning
  support in OpenRouter model metadata.
- Responses are accepted only when grounding and reasoning can be proven.
"""

from __future__ import annotations
from typing import Dict, Tuple, Optional, Any, List
import sys
import json
import copy
import logging
import random
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

LOG = logging.getLogger("fpf_openrouter_main")

# Provider-level flags: OpenRouter is a strict research path in FPF.
REQUIRES_GROUNDING = True
REQUIRES_REASONING = True

# OpenRouter uses OpenAI-compatible API
DEFAULT_API_BASE = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS_API_BASE = "https://openrouter.ai/api/v1"

# Allow all models - OpenRouter handles validation
ALLOWED_PREFIXES = tuple()  # No restrictions

_REQUIRED_SUPPORTED_PARAMETERS = {"tools", "reasoning"}
_DEFAULT_WEB_SEARCH_PARAMETERS = {
    "engine": "auto",
    "max_results": 5,
    "max_total_results": 10,
    "search_context_size": "medium",
}
_MODELS_CACHE_TTL_SECONDS = 300
_models_cache_lock = threading.Lock()
_models_cache: Dict[str, Any] = {
    "loaded_at": 0.0,
    "by_id": {},
}


def _json_preview(value: Any, limit: int = 1200) -> str:
    """Serialize a value for safe, bounded log output."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception as exc:
        text = f"<unserializable {type(value).__name__}: {exc}>"
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _safe_log_fragment(value: Any, limit: int = 120) -> str:
    raw = str(value or "unknown").strip()
    if raw.startswith("openrouter:"):
        raw = raw[len("openrouter:"):]
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in raw)
    return (safe or "unknown")[:limit]


def _redact_payload_log_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    secret_names = {"authorization", "x-api-key", "x-goog-api-key", "api-key"}
    redacted: Dict[str, Any] = {}
    for key, value in (headers or {}).items():
        if str(key).lower() in secret_names:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _write_openrouter_free_payload_log(
    context: Optional[Dict[str, Any]],
    *,
    attempt: int,
    event: str,
    provider_url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, Any]],
    request_body_bytes: Optional[int] = None,
    response_code: Optional[int] = None,
    response_raw: Optional[str] = None,
    response_json: Optional[Dict[str, Any]] = None,
    response_summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Optional[str]:
    if not context or not context.get("enabled"):
        return None
    log_dir_raw = context.get("log_dir")
    if not log_dir_raw:
        return None
    try:
        log_dir = Path(str(log_dir_raw))
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        millis = int((time.time() % 1) * 1000)
        run_id = _safe_log_fragment(context.get("run_id") or "run")
        model = context.get("model") or payload.get("model") or "unknown"
        event_safe = _safe_log_fragment(event, limit=40)
        model_safe = _safe_log_fragment(model)
        path = log_dir / f"{timestamp}{millis:03d}Z-{run_id}-attempt{attempt}-{event_safe}-{model_safe}.json"
        record: Dict[str, Any] = {
            "event": event,
            "attempt": attempt,
            "run_id": context.get("run_id"),
            "run_group_id": context.get("run_group_id"),
            "provider": context.get("provider") or "openrouter",
            "model": model,
            "provider_url": provider_url,
            "request": {
                "body_bytes": request_body_bytes,
                "headers": _redact_payload_log_headers(headers),
                "payload": payload,
            },
        }
        if response_code is not None or response_raw is not None or response_json is not None:
            record["response"] = {
                "status_code": response_code,
                "raw": response_raw,
                "json": response_json,
                "summary": response_summary,
            }
        if error:
            record["error"] = error
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False, default=str)
        LOG.info("[OPENROUTER FREE PAYLOAD LOG] wrote %s", path)
        return str(path)
    except Exception as exc:
        LOG.warning("[OPENROUTER FREE PAYLOAD LOG] failed event=%s attempt=%s error=%s", event, attempt, exc)
        return None


def _normalize_model(model: str) -> str:
    """
    Normalize model ID. OpenRouter models use 'provider/model' format.
    Strip any 'openrouter:' prefix if present.
    """
    raw = model or ""
    if raw.startswith("openrouter:"):
        raw = raw[len("openrouter:"):]
    return raw


def _is_openrouter_free_model(model: Any) -> bool:
    """Return true only for OpenRouter free model identifiers."""
    normalized = _normalize_model(str(model or "")).strip().lower()
    return normalized == "openrouter/free" or normalized.endswith(":free")


def _translate_sampling(cfg: Dict) -> Dict[str, Any]:
    """Translate FPF sampling parameters to OpenAI-compatible format."""
    out: Dict[str, Any] = {}

    if cfg.get("max_completion_tokens") is not None:
        out["max_tokens"] = int(cfg["max_completion_tokens"])
    elif cfg.get("max_tokens") is not None:
        out["max_tokens"] = int(cfg["max_tokens"])
    else:
        raise RuntimeError("OpenRouter requires 'max_tokens' or 'max_completion_tokens' in config - no fallback defaults allowed")

    if cfg.get("temperature") is not None:
        out["temperature"] = float(cfg["temperature"])
    if cfg.get("top_p") is not None:
        out["top_p"] = float(cfg["top_p"])

    return out


def _stringify_reasoning_value(value: Any) -> Optional[str]:
    """Convert provider reasoning payloads into a comparable string signal."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        parts: List[str] = []
        for item in value.values():
            piece = _stringify_reasoning_value(item)
            if piece:
                parts.append(piece)
        if parts:
            return "\n\n".join(parts)
        if value:
            return "OpenRouter returned structured reasoning data."
    if isinstance(value, list):
        parts = []
        for item in value:
            piece = _stringify_reasoning_value(item)
            if piece:
                parts.append(piece)
        if parts:
            return "\n\n".join(parts)
        if value:
            return "OpenRouter returned structured reasoning data."
    return None


def _coerce_reasoning_effort(cfg: Dict[str, Any]) -> str:
    """Resolve the strict OpenRouter reasoning effort for this request."""
    allowed_efforts = {"minimal", "low", "medium", "high", "xhigh"}
    reasoning_cfg = cfg.get("reasoning") or cfg.get("thinking") or {}
    effort = None
    if isinstance(reasoning_cfg, dict):
        effort = reasoning_cfg.get("effort") or reasoning_cfg.get("reasoning_effort")
    if not effort:
        effort = cfg.get("reasoning_effort")
    if not effort:
        budget = cfg.get("thinking_budget_tokens")
        if budget is not None:
            if budget < 4000:
                effort = "low"
            elif budget > 12000:
                effort = "high"
            else:
                effort = "medium"
    effort = str(effort).strip().lower() if effort is not None else "high"
    if effort not in allowed_efforts:
        effort = "high"
    return effort


def _build_web_search_parameters(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build strict OpenRouter server-tool search parameters."""
    params = dict(_DEFAULT_WEB_SEARCH_PARAMETERS)
    web_search_cfg = cfg.get("web_search") or {}
    if isinstance(web_search_cfg, dict):
        context_size = web_search_cfg.get("search_context_size")
        if isinstance(context_size, str) and context_size.strip().lower() in {"low", "medium", "high"}:
            params["search_context_size"] = context_size.strip().lower()

        max_results = web_search_cfg.get("max_results")
        if isinstance(max_results, int) and max_results > 0:
            params["max_results"] = max_results

        max_total_results = web_search_cfg.get("max_total_results")
        if isinstance(max_total_results, int) and max_total_results > 0:
            params["max_total_results"] = max_total_results

    if params["max_total_results"] < params["max_results"]:
        params["max_total_results"] = params["max_results"]
    return params


def _summarize_request_payload(payload: Dict[str, Any], headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a compact request summary for logs without leaking prompt text."""
    messages = payload.get("messages") or []
    message_summary = []
    for idx, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            content_length = len(content)
        elif isinstance(content, list):
            content_length = len(content)
        else:
            content_length = 0
        message_summary.append(
            {
                "index": idx,
                "role": message.get("role"),
                "content_type": type(content).__name__,
                "content_length": content_length,
            }
        )

    safe_header_keys = sorted((headers or {}).keys())
    return {
        "model": payload.get("model"),
        "message_count": len(messages),
        "messages": message_summary,
        "tool_types": [
            tool.get("type")
            for tool in (payload.get("tools") or [])
            if isinstance(tool, dict)
        ],
        "web_search_parameters": (
            payload.get("tools") or [{}]
        )[0].get("parameters") if payload.get("tools") else {},
        "tool_choice": payload.get("tool_choice"),
        "reasoning": payload.get("reasoning"),
        "response_format": payload.get("response_format"),
        "max_tokens": payload.get("max_tokens"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "header_keys": safe_header_keys,
        "has_authorization_header": "Authorization" in safe_header_keys,
    }


def _fetch_models_index(api_base: str = DEFAULT_MODELS_API_BASE) -> Dict[str, Dict[str, Any]]:
    """Fetch and cache OpenRouter model metadata for runtime capability checks."""
    now = time.time()
    with _models_cache_lock:
        loaded_at = float(_models_cache.get("loaded_at") or 0.0)
        by_id = _models_cache.get("by_id") or {}
        if by_id and (now - loaded_at) < _MODELS_CACHE_TTL_SECONDS:
            LOG.info(
                "[OPENROUTER CAPABILITIES] cache_hit models=%d age_seconds=%.1f",
                len(by_id),
                now - loaded_at,
            )
            return by_id

    url = api_base.rstrip("/") + "/models"
    base_delay_ms = 500
    max_delay_ms = 4000
    last_error: Optional[Exception] = None
    LOG.info("[OPENROUTER CAPABILITIES] cache_miss fetching=%s", url)

    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
        try:
            LOG.info("[OPENROUTER CAPABILITIES] attempt=%d/3 fetch_models", attempt)
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw)
            data = payload.get("data") or []
            refreshed = {
                model["id"]: model
                for model in data
                if isinstance(model, dict) and isinstance(model.get("id"), str)
            }
            with _models_cache_lock:
                _models_cache["loaded_at"] = time.time()
                _models_cache["by_id"] = refreshed
            LOG.info(
                "[OPENROUTER CAPABILITIES] fetch_success models=%d sample=%s",
                len(refreshed),
                list(sorted(refreshed.keys()))[:5],
            )
            return refreshed
        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            last_error = RuntimeError(
                f"OpenRouter models API error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}"
            )
            if attempt < 3 and _is_transient_error(last_error):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                LOG.warning(
                    "[OPENROUTER CAPABILITIES] transient_failure attempt=%d/3 retry_in=%.2fs error=%s",
                    attempt,
                    delay_ms / 1000.0,
                    last_error,
                )
                time.sleep(delay_ms / 1000.0)
                continue
            LOG.error(
                "[OPENROUTER CAPABILITIES] fatal_http_failure attempt=%d/3 error=%s",
                attempt,
                last_error,
            )
            raise last_error from he
        except Exception as e:
            last_error = RuntimeError(f"OpenRouter models API request failed: {e}")
            if attempt < 3 and _is_transient_error(e):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                LOG.warning(
                    "[OPENROUTER CAPABILITIES] transient_exception attempt=%d/3 retry_in=%.2fs error=%s",
                    attempt,
                    delay_ms / 1000.0,
                    e,
                )
                time.sleep(delay_ms / 1000.0)
                continue
            LOG.error(
                "[OPENROUTER CAPABILITIES] fatal_exception attempt=%d/3 error=%s",
                attempt,
                e,
            )
            raise last_error from e

    if last_error:
        raise last_error
    raise RuntimeError("OpenRouter models API request failed after all retries")


def _get_model_metadata(model_to_use: str) -> Optional[Dict[str, Any]]:
    """Look up OpenRouter metadata for the normalized model id."""
    return _fetch_models_index().get(model_to_use)


def _assert_model_supports_strict_research(model_to_use: str) -> Dict[str, Any]:
    """Fail fast if the selected model cannot satisfy strict FPF requirements."""
    model_metadata = _get_model_metadata(model_to_use)
    if not isinstance(model_metadata, dict):
        LOG.error(
            "[OPENROUTER CAPABILITIES] missing_metadata model=%s",
            model_to_use,
        )
        raise RuntimeError(
            f"OpenRouter strict FPF could not find model metadata for '{model_to_use}'. "
            "Strict web search + reasoning enforcement requires a live capability record."
        )

    supported_parameters = set(model_metadata.get("supported_parameters") or [])
    missing = sorted(_REQUIRED_SUPPORTED_PARAMETERS - supported_parameters)
    LOG.info(
        "[OPENROUTER CAPABILITIES] model=%s required=%s supported_sample=%s missing=%s",
        model_to_use,
        sorted(_REQUIRED_SUPPORTED_PARAMETERS),
        sorted(supported_parameters)[:20],
        missing,
    )
    if missing:
        raise RuntimeError(
            f"OpenRouter strict FPF requires tools and reasoning support. "
            f"Model '{model_to_use}' is missing: {', '.join(missing)}."
        )
    return model_metadata


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    """
    Build a strict OpenRouter chat completions payload.

    FPF always requests:
    - openrouter:web_search with engine=auto
    - reasoning enabled with an explicit effort level
    """
    model_cfg = cfg.get("model")
    if not model_cfg:
        raise RuntimeError("OpenRouter provider requires 'model' in config")
    model_to_use = _normalize_model(model_cfg)
    _assert_model_supports_strict_research(model_to_use)

    request_json = bool(cfg.get("json")) if cfg.get("json") is not None else False
    if request_json:
        json_instr = (
            "Return only a single valid JSON object. Do not include prose or fences. "
            "Output must be strictly valid JSON."
        )
        final_prompt = f"{json_instr}\n\n{prompt}"
    else:
        final_prompt = prompt

    sampling = _translate_sampling(cfg)

    messages: List[Dict[str, Any]] = []

    # Add system prompt if provided
    if cfg.get("system"):
        messages.append({"role": "system", "content": cfg["system"]})

    messages.append({"role": "user", "content": final_prompt})

    payload: Dict[str, Any] = {
        "model": model_to_use,
        "messages": messages,
        **sampling,
    }
    payload["tools"] = [
        {
            "type": "openrouter:web_search",
            "parameters": _build_web_search_parameters(cfg),
        }
    ]
    payload["reasoning"] = {
        "enabled": True,
        "effort": _coerce_reasoning_effort(cfg),
        "exclude": False,
    }

    # Optional: response_format for JSON mode
    if request_json:
        payload["response_format"] = {"type": "json_object"}

    # OpenRouter-specific headers (optional but recommended)
    headers: Dict[str, str] = {}

    # HTTP-Referer and X-Title for app identification (helps with rate limits)
    if cfg.get("http_referer"):
        headers["HTTP-Referer"] = cfg["http_referer"]
    if cfg.get("x_title"):
        headers["X-Title"] = cfg["x_title"]

    LOG.info(
        "[OPENROUTER REQUEST] Prepared strict payload: %s",
        _json_preview(_summarize_request_payload(payload, headers), limit=1800),
    )
    return payload, headers if headers else None


def _is_transient_error(exc: Exception) -> bool:
    """Check if an error is transient and worth retrying."""
    msg = str(exc).lower()
    transient = [
        "429",
        "rate limit",
        "timeout",
        "timed out",
        "500",
        "internal server error",
        "502",
        "503",
        "504",
        "connection",
        "network",
        "grounding",
        "validation",
        "reasoning",
        "web_search",
        "writer recovery",
        "no report content",
        "content missing",
        "extractable source evidence",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
    ]
    return any(tok in msg for tok in transient)


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    """
    Extract reasoning/thinking from OpenRouter response.

    For models that support reasoning (o1/o3, DeepSeek R1, Gemini with thinking),
    the reasoning may appear in different places depending on the underlying model.
    """
    if not isinstance(raw_json, dict):
        return None

    # Check for explicit reasoning fields (DeepSeek R1 style)
    top_level_reasoning = _stringify_reasoning_value(raw_json.get("reasoning"))
    if top_level_reasoning:
        return top_level_reasoning
    top_level_reasoning_details = _stringify_reasoning_value(raw_json.get("reasoning_details"))
    if top_level_reasoning_details:
        return top_level_reasoning_details
    top_level_thinking = _stringify_reasoning_value(raw_json.get("thinking"))
    if top_level_thinking:
        return top_level_thinking

    # Check choices for reasoning content
    choices = raw_json.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue

        message = choice.get("message") or {}

        # Some models put reasoning in a separate field
        message_reasoning = _stringify_reasoning_value(message.get("reasoning"))
        if message_reasoning:
            return message_reasoning
        message_reasoning_details = _stringify_reasoning_value(message.get("reasoning_details"))
        if message_reasoning_details:
            return message_reasoning_details
        message_thinking = _stringify_reasoning_value(message.get("thinking"))
        if message_thinking:
            return message_thinking

        # Check for reasoning in content blocks (Claude-style via OpenRouter)
        content = message.get("content")
        if isinstance(content, list):
            reasoning_parts = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype in ("thinking", "reasoning"):
                        text = block.get("text") or block.get("thinking") or block.get("content")
                        if isinstance(text, str) and text.strip():
                            reasoning_parts.append(text.strip())
            if reasoning_parts:
                return "\n\n".join(reasoning_parts)

    usage = raw_json.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    reasoning_tokens = 0
    for details in (completion_details, output_details):
        try:
            reasoning_tokens = max(reasoning_tokens, int(details.get("reasoning_tokens") or 0))
        except Exception:
            continue
    if reasoning_tokens > 0:
        return f"OpenRouter reported {reasoning_tokens} reasoning tokens."

    return None


def _summarize_response_proof(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact proof-oriented response summary for logs."""
    usage = raw_json.get("usage") or {}
    server_tool_use = usage.get("server_tool_use") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}

    try:
        web_search_requests = int(server_tool_use.get("web_search_requests") or 0)
    except Exception:
        web_search_requests = 0

    reasoning_tokens = 0
    for details in (completion_details, output_details):
        try:
            reasoning_tokens = max(reasoning_tokens, int(details.get("reasoning_tokens") or 0))
        except Exception:
            continue

    message_annotation_count = 0
    content_annotation_count = 0
    url_text_hits = 0
    reasoning_block_count = 0
    message_reasoning_fields = 0
    choices = raw_json.get("choices") or []

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            continue

        annotations = message.get("annotations")
        if isinstance(annotations, list):
            message_annotation_count += len(
                [
                    annotation
                    for annotation in annotations
                    if isinstance(annotation, dict)
                    and (
                        str(annotation.get("type") or "").lower() == "url_citation"
                        or isinstance(annotation.get("url_citation"), dict)
                        or (isinstance(annotation.get("url"), str) and annotation.get("url", "").strip())
                    )
                ]
            )

        if any(message.get(field) for field in ("reasoning", "reasoning_details", "thinking")):
            message_reasoning_fields += 1

        content = message.get("content")
        if isinstance(content, str):
            if "http://" in content or "https://" in content:
                url_text_hits += 1
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("thinking", "reasoning"):
                    reasoning_block_count += 1
                block_annotations = block.get("annotations")
                if isinstance(block_annotations, list):
                    content_annotation_count += len(
                        [
                            annotation
                            for annotation in block_annotations
                            if isinstance(annotation, dict)
                            and (
                                str(annotation.get("type") or "").lower() == "url_citation"
                                or isinstance(annotation.get("url_citation"), dict)
                                or (isinstance(annotation.get("url"), str) and annotation.get("url", "").strip())
                            )
                        ]
                    )
                text = block.get("text")
                if isinstance(text, str) and ("http://" in text or "https://" in text):
                    url_text_hits += 1

    extracted_reasoning = extract_reasoning(raw_json)
    return {
        "top_level_keys": list(raw_json.keys()) if isinstance(raw_json, dict) else [],
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "usage_keys": list(usage.keys()) if isinstance(usage, dict) else [],
        "server_tool_use": server_tool_use,
        "web_search_requests": web_search_requests,
        "completion_tokens_details": completion_details,
        "output_tokens_details": output_details,
        "reasoning_tokens": reasoning_tokens,
        "message_annotation_count": message_annotation_count,
        "content_annotation_count": content_annotation_count,
        "url_text_hits": url_text_hits,
        "message_reasoning_fields": message_reasoning_fields,
        "reasoning_block_count": reasoning_block_count,
        "has_extracted_reasoning": bool(isinstance(extracted_reasoning, str) and extracted_reasoning.strip()),
        "extracted_reasoning_length": len(extracted_reasoning) if isinstance(extracted_reasoning, str) else 0,
    }


def _extract_response_text(raw_json: Dict[str, Any]) -> Optional[str]:
    """Extract only real assistant text content; never stringify the full JSON."""
    if not isinstance(raw_json, dict):
        return None

    choices = raw_json.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message") or {}
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        return text or None

    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = None
            if block.get("type") == "text":
                text = block.get("text")
            elif isinstance(block.get("text"), str):
                text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return "\n\n".join(text_parts)

    return None


def _has_usable_report_content(raw_json: Dict[str, Any]) -> bool:
    """Content is usable only when real assistant text exists."""
    return bool(_extract_response_text(raw_json))


def _compact_source_item(item: Dict[str, Any], max_content_chars: int) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key in ("url", "title", "content", "path", "kind"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            if key == "content" and len(value) > max_content_chars:
                value = value[:max_content_chars] + "...[truncated]"
            compact[key] = value
    return compact


def _extract_free_validated_response_evidence(raw_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract source material from a primary response that already passed validation.

    This does not decide whether grounding passed; grounding_enforcer already did
    that before recovery is allowed to run.
    """
    max_sources = 10
    max_content_chars = 4000
    max_total_chars = 25000
    evidence: List[Dict[str, Any]] = []
    seen = set()

    def add(kind: str, path: str, value: Any) -> None:
        if len(evidence) >= max_sources:
            return
        if not isinstance(value, dict):
            return

        candidate: Dict[str, Any] = {"kind": kind, "path": path}
        url_citation = value.get("url_citation")
        if isinstance(url_citation, dict):
            value = {**value, **url_citation}

        for key in ("url", "link", "href", "uri"):
            raw_url = value.get(key)
            if isinstance(raw_url, str) and raw_url.strip():
                candidate["url"] = raw_url.strip()
                break

        raw_title = value.get("title") or value.get("name")
        if isinstance(raw_title, str) and raw_title.strip():
            candidate["title"] = raw_title.strip()

        raw_content = value.get("content") or value.get("text") or value.get("snippet")
        if isinstance(raw_content, str) and raw_content.strip():
            candidate["content"] = raw_content.strip()

        identity = (
            candidate.get("url"),
            candidate.get("title"),
            candidate.get("content"),
        )
        if not any(identity) or identity in seen:
            return
        seen.add(identity)
        evidence.append(_compact_source_item(candidate, max_content_chars))

    def walk(value: Any, path: str) -> None:
        if len(evidence) >= max_sources:
            return
        if isinstance(value, dict):
            value_type = str(value.get("type") or "").lower()
            if value_type == "url_citation" or isinstance(value.get("url_citation"), dict):
                add("url_citation", path, value)
            elif any(isinstance(value.get(key), str) and value.get(key, "").strip() for key in ("url", "link", "href", "uri")):
                add("source_link", path, value)
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                walk(child, f"{path}[{idx}]")

    walk(raw_json, "response")

    trimmed: List[Dict[str, Any]] = []
    total_chars = 0
    for item in evidence:
        item_chars = sum(len(str(v)) for v in item.values())
        if total_chars + item_chars > max_total_chars:
            break
        trimmed.append(item)
        total_chars += item_chars

    usage = raw_json.get("usage") if isinstance(raw_json, dict) else {}
    server_tool_use = usage.get("server_tool_use") if isinstance(usage, dict) else {}
    try:
        web_search_requests = int((server_tool_use or {}).get("web_search_requests") or 0)
    except Exception:
        web_search_requests = 0

    return {
        "source_count": len(trimmed),
        "sources": trimmed,
        "web_search_requests": web_search_requests,
        "truncated_source_count": max(0, len(evidence) - len(trimmed)),
        "total_evidence_chars": total_chars,
    }


def _stringify_original_messages(payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    messages = payload.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "message")
            content = message.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                text = "\n".join(text_parts)
            else:
                text = json.dumps(content, ensure_ascii=False, default=str)
            if text and text.strip():
                parts.append(f"[{role}]\n{text.strip()}")
    return "\n\n".join(parts)


def _build_free_writer_payload(original_payload: Dict[str, Any], evidence_bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Build the recovery request. It deliberately omits web-search tools."""
    payload = {
        key: value
        for key, value in original_payload.items()
        if key not in {"messages", "tools", "tool_choice", "response_format"}
    }

    original_prompt = _stringify_original_messages(original_payload)
    sources = evidence_bundle.get("sources") or []
    evidence_text = json.dumps(sources, indent=2, ensure_ascii=False, default=str)
    recovery_prompt = (
        "You are writing the final FilePromptForge report.\n\n"
        "The previous OpenRouter response already passed required web-search grounding "
        "and reasoning validation, but it did not include final report content.\n\n"
        "Use only the validated source evidence below and the original request. "
        "Do not output tool calls. Do not summarize the search process. "
        "Do not return JSON. Return the final report text only.\n\n"
        "Original request:\n"
        f"{original_prompt}\n\n"
        "Validated source evidence:\n"
        f"{evidence_text}\n"
    )

    messages: List[Dict[str, Any]] = []
    for message in original_payload.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "system" and isinstance(message.get("content"), str):
            messages.append({"role": "system", "content": message["content"]})
    messages.append({"role": "user", "content": recovery_prompt})
    payload["messages"] = messages
    payload["reasoning"] = {
        "enabled": True,
        "effort": str((original_payload.get("reasoning") or {}).get("effort") or "high"),
        "exclude": False,
    }
    return payload


def _merge_recovery_usage(primary: Dict[str, Any], writer: Dict[str, Any]) -> Dict[str, Any]:
    primary_usage = primary.get("usage") if isinstance(primary, dict) else {}
    writer_usage = writer.get("usage") if isinstance(writer, dict) else {}
    primary_usage = primary_usage if isinstance(primary_usage, dict) else {}
    writer_usage = writer_usage if isinstance(writer_usage, dict) else {}

    merged: Dict[str, Any] = dict(primary_usage)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = []
        for usage in (primary_usage, writer_usage):
            try:
                values.append(int(usage.get(key) or 0))
            except Exception:
                values.append(0)
        if any(values):
            merged[key] = sum(values)

    merged["server_tool_use"] = primary_usage.get("server_tool_use") or {}
    merged["fpf_recovery_usage"] = {
        "primary": primary_usage,
        "writer": writer_usage,
    }
    return merged


def _combine_free_recovery_response(
    *,
    model: Any,
    primary_raw_json: Dict[str, Any],
    writer_raw_json: Dict[str, Any],
    evidence_bundle: Dict[str, Any],
    writer_content: str,
) -> Dict[str, Any]:
    combined = copy.deepcopy(primary_raw_json)
    writer_choices = writer_raw_json.get("choices") if isinstance(writer_raw_json, dict) else []
    writer_choice = writer_choices[0] if isinstance(writer_choices, list) and writer_choices and isinstance(writer_choices[0], dict) else {}
    writer_message = writer_choice.get("message") if isinstance(writer_choice, dict) else {}
    writer_message = writer_message if isinstance(writer_message, dict) else {}

    choices = combined.setdefault("choices", [])
    if not isinstance(choices, list):
        choices = []
        combined["choices"] = choices
    if not choices or not isinstance(choices[0], dict):
        choices.insert(0, {"index": 0, "message": {"role": "assistant"}})
    first_choice = choices[0]
    message = first_choice.setdefault("message", {})
    if not isinstance(message, dict):
        message = {"role": "assistant"}
        first_choice["message"] = message
    message["role"] = message.get("role") or "assistant"
    message["content"] = writer_content
    if writer_message.get("reasoning") is not None:
        message["writer_recovery_reasoning"] = writer_message.get("reasoning")
    if writer_message.get("reasoning_details") is not None:
        message["writer_recovery_reasoning_details"] = writer_message.get("reasoning_details")
    if writer_message.get("thinking") is not None:
        message["writer_recovery_thinking"] = writer_message.get("thinking")
    first_choice["finish_reason"] = writer_choice.get("finish_reason") or first_choice.get("finish_reason") or "stop"

    combined["fpf_mode"] = "openrouter_free_writer_recovery_v1"
    combined["provider"] = "openrouter"
    combined["model"] = combined.get("model") or model
    combined["usage"] = _merge_recovery_usage(primary_raw_json, writer_raw_json)
    combined["fpf_writer_recovery"] = {
        "writer_response_id": writer_raw_json.get("id") if isinstance(writer_raw_json, dict) else None,
        "grounding_proof": {
            "stage": "primary_response",
            "passed": True,
            "evidence_count": evidence_bundle.get("source_count"),
            "web_search_requests": evidence_bundle.get("web_search_requests"),
        },
        "reasoning_proof": {
            "primary_passed": True,
            "writer_passed": True,
        },
        "evidence": evidence_bundle,
        "writer_response": writer_raw_json,
    }
    return combined


def _execute_free_writer_recovery(
    *,
    provider_url: str,
    original_payload: Dict[str, Any],
    primary_raw_json: Dict[str, Any],
    primary_response_summary: Dict[str, Any],
    headers: Dict[str, Any],
    verify_helpers: Any,
    timeout: Optional[int],
    attempt: int,
    full_payload_log_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    evidence_bundle = _extract_free_validated_response_evidence(primary_raw_json)
    if not evidence_bundle.get("sources"):
        raise RuntimeError("OpenRouter free writer recovery refused: validated primary response had no extractable source evidence.")

    recovery_payload = _build_free_writer_payload(original_payload, evidence_bundle)
    recovery_data = json.dumps(recovery_payload).encode("utf-8")
    provider_mod = sys.modules.get(__name__) or __import__(__name__)

    LOG.warning(
        "[OPENROUTER FREE RECOVERY] attempt=%d model=%s primary validation passed but content missing; source_count=%s web_search_requests=%s",
        attempt,
        original_payload.get("model"),
        evidence_bundle.get("source_count"),
        evidence_bundle.get("web_search_requests"),
    )
    _write_openrouter_free_payload_log(
        full_payload_log_context,
        attempt=attempt,
        event="writer_recovery_request",
        provider_url=provider_url,
        payload=recovery_payload,
        headers=headers,
        request_body_bytes=len(recovery_data),
        response_summary={
            "primary_response_summary": primary_response_summary,
            "evidence": evidence_bundle,
        },
    )

    req = urllib.request.Request(provider_url, data=recovery_data, headers=headers, method="POST")
    if timeout is None:
        resp_ctx = urllib.request.urlopen(req)
    else:
        resp_ctx = urllib.request.urlopen(req, timeout=timeout)
    with resp_ctx as resp:
        raw = resp.read().decode("utf-8")
        try:
            response_code = resp.getcode()
        except Exception:
            response_code = None
        writer_raw_json = json.loads(raw)

    writer_summary = _summarize_response_proof(writer_raw_json)
    _write_openrouter_free_payload_log(
        full_payload_log_context,
        attempt=attempt,
        event="writer_recovery_response",
        provider_url=provider_url,
        payload=recovery_payload,
        headers=headers,
        request_body_bytes=len(recovery_data),
        response_code=response_code,
        response_raw=raw,
        response_json=writer_raw_json,
        response_summary=writer_summary,
    )

    writer_content = _extract_response_text(writer_raw_json)
    if not writer_content:
        raise RuntimeError("OpenRouter free writer recovery failed: recovery response had no report content.")

    if hasattr(verify_helpers, "detect_reasoning"):
        writer_has_reasoning = bool(verify_helpers.detect_reasoning(writer_raw_json, provider=provider_mod))
    else:
        writer_has_reasoning = bool(extract_reasoning(writer_raw_json))
    if not writer_has_reasoning:
        raise verify_helpers.ValidationError(
            "OpenRouter free writer recovery failed: recovery response missing reasoning proof.",
            missing_grounding=False,
            missing_reasoning=True,
        )

    LOG.info(
        "[OPENROUTER FREE RECOVERY] attempt=%d model=%s recovery passed content_length=%d source_count=%s",
        attempt,
        original_payload.get("model"),
        len(writer_content),
        evidence_bundle.get("source_count"),
    )
    return _combine_free_recovery_response(
        model=original_payload.get("model"),
        primary_raw_json=primary_raw_json,
        writer_raw_json=writer_raw_json,
        evidence_bundle=evidence_bundle,
        writer_content=writer_content,
    )


def parse_response(raw_json: Dict) -> str:
    """
    Parse the OpenRouter response to extract the main text content.
    Uses OpenAI-compatible format.
    """
    if not isinstance(raw_json, dict):
        return str(raw_json)

    text = _extract_response_text(raw_json)
    if text:
        return text

    mode = raw_json.get("fpf_mode")
    if mode == "openrouter_free_writer_recovery_v1":
        raise RuntimeError("OpenRouter writer-recovery envelope has no final report content.")
    raise RuntimeError("OpenRouter response has no final report content; refusing to write raw provider JSON.")


def execute_and_verify(
    provider_url: str,
    payload: Dict,
    headers: Optional[Dict],
    verify_helpers,
    timeout: Optional[int] = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    full_payload_log_context: Optional[Dict[str, Any]] = None,
) -> Dict:
    """
    Execute the OpenRouter request and run validation.
    """
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    base_delay_ms = retry_delay * 1000
    max_delay_ms = max(base_delay_ms * 4, 120000)
    last_error: Optional[Exception] = None
    request_summary = _summarize_request_payload(payload, hdrs)
    LOG.info(
        "[OPENROUTER REQUEST] Starting validation-enabled request: %s",
        _json_preview(
            {
                "provider_url": provider_url,
                "timeout": timeout,
                "max_retries": max_retries,
                "request": request_summary,
            },
            limit=1800,
        ),
    )

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(provider_url, data=data, headers=hdrs, method="POST")
        raw_for_attempt: Optional[str] = None
        response_code_for_attempt: Optional[int] = None
        try:
            LOG.info(
                "[OPENROUTER REQUEST] attempt=%d/%d model=%s timeout=%s",
                attempt,
                max_retries,
                payload.get("model"),
                timeout,
            )
            _write_openrouter_free_payload_log(
                full_payload_log_context,
                attempt=attempt,
                event="request",
                provider_url=provider_url,
                payload=payload,
                headers=hdrs,
                request_body_bytes=len(data),
            )
            if timeout is None:
                resp_ctx = urllib.request.urlopen(req)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read().decode("utf-8")
                raw_for_attempt = raw
                response_code = None
                try:
                    response_code = resp.getcode()
                except Exception:
                    response_code = None
                response_code_for_attempt = response_code
                raw_json = json.loads(raw)
                response_summary = _summarize_response_proof(raw_json)
                _write_openrouter_free_payload_log(
                    full_payload_log_context,
                    attempt=attempt,
                    event="response",
                    provider_url=provider_url,
                    payload=payload,
                    headers=hdrs,
                    request_body_bytes=len(data),
                    response_code=response_code,
                    response_raw=raw,
                    response_json=raw_json,
                    response_summary=response_summary,
                )
                LOG.info(
                    "[OPENROUTER RESPONSE] attempt=%d/%d status=%s summary=%s",
                    attempt,
                    max_retries,
                    response_code,
                    _json_preview(response_summary, limit=1800),
                )

                # Pass this module for provider-level flag checking
                provider_mod = sys.modules.get(__name__) or __import__(__name__)
                try:
                    verify_helpers.assert_grounding_and_reasoning(raw_json, provider=provider_mod)
                except verify_helpers.ValidationError as ve:
                    _write_openrouter_free_payload_log(
                        full_payload_log_context,
                        attempt=attempt,
                        event="validation_error",
                        provider_url=provider_url,
                        payload=payload,
                        headers=hdrs,
                        request_body_bytes=len(data),
                        response_code=response_code,
                        response_raw=raw,
                        response_json=raw_json,
                        response_summary=response_summary,
                        error=str(ve),
                    )
                    LOG.warning(
                        "[OPENROUTER VALIDATION] attempt=%d/%d failed missing_grounding=%s missing_reasoning=%s error=%s proof=%s",
                        attempt,
                        max_retries,
                        getattr(ve, "missing_grounding", None),
                        getattr(ve, "missing_reasoning", None),
                        ve,
                        _json_preview(response_summary, limit=1800),
                    )
                    raise
                LOG.info(
                    "[OPENROUTER VALIDATION] attempt=%d/%d passed model=%s web_search_requests=%s reasoning_tokens=%s annotations=%s url_hits=%s",
                    attempt,
                    max_retries,
                    payload.get("model"),
                    response_summary.get("web_search_requests"),
                    response_summary.get("reasoning_tokens"),
                    (response_summary.get("message_annotation_count") or 0)
                    + (response_summary.get("content_annotation_count") or 0),
                    response_summary.get("url_text_hits"),
                )
                if _is_openrouter_free_model(payload.get("model")):
                    if _has_usable_report_content(raw_json):
                        LOG.info(
                            "[OPENROUTER FREE RECOVERY] attempt=%d/%d model=%s content present; recovery skipped",
                            attempt,
                            max_retries,
                            payload.get("model"),
                        )
                        _write_openrouter_free_payload_log(
                            full_payload_log_context,
                            attempt=attempt,
                            event="primary_content_present",
                            provider_url=provider_url,
                            payload=payload,
                            headers=hdrs,
                            request_body_bytes=len(data),
                            response_code=response_code,
                            response_raw=raw,
                            response_json=raw_json,
                            response_summary=response_summary,
                        )
                    else:
                        _write_openrouter_free_payload_log(
                            full_payload_log_context,
                            attempt=attempt,
                            event="primary_content_missing",
                            provider_url=provider_url,
                            payload=payload,
                            headers=hdrs,
                            request_body_bytes=len(data),
                            response_code=response_code,
                            response_raw=raw,
                            response_json=raw_json,
                            response_summary=response_summary,
                            error="Primary response passed grounding/reasoning validation but had no final report content.",
                        )
                        return _execute_free_writer_recovery(
                            provider_url=provider_url,
                            original_payload=payload,
                            primary_raw_json=raw_json,
                            primary_response_summary=response_summary,
                            headers=hdrs,
                            verify_helpers=verify_helpers,
                            timeout=timeout,
                            attempt=attempt,
                            full_payload_log_context=full_payload_log_context,
                        )
                return raw_json

        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            last_error = RuntimeError(f"HTTP error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}")
            _write_openrouter_free_payload_log(
                full_payload_log_context,
                attempt=attempt,
                event="http_error",
                provider_url=provider_url,
                payload=payload,
                headers=hdrs,
                request_body_bytes=len(data),
                response_code=getattr(he, "code", None),
                response_raw=msg,
                error=str(last_error),
            )

            if attempt < max_retries and _is_transient_error(last_error):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                LOG.warning(
                    "[OPENROUTER RETRY] attempt=%d/%d retry_in=%.2fs reason=%s",
                    attempt,
                    max_retries,
                    delay_ms / 1000.0,
                    last_error,
                )
                time.sleep(delay_ms / 1000.0)
                continue
            LOG.error(
                "[OPENROUTER REQUEST] fatal_http_error attempt=%d/%d error=%s",
                attempt,
                max_retries,
                last_error,
            )
            raise last_error from he
        except Exception as e:
            last_error = RuntimeError(f"HTTP request failed: {e}")
            _write_openrouter_free_payload_log(
                full_payload_log_context,
                attempt=attempt,
                event="exception",
                provider_url=provider_url,
                payload=payload,
                headers=hdrs,
                request_body_bytes=len(data),
                response_code=response_code_for_attempt,
                response_raw=raw_for_attempt,
                error=str(last_error),
            )
            if attempt < max_retries and _is_transient_error(e):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                LOG.warning(
                    "[OPENROUTER RETRY] attempt=%d/%d retry_in=%.2fs exception=%s",
                    attempt,
                    max_retries,
                    delay_ms / 1000.0,
                    e,
                )
                time.sleep(delay_ms / 1000.0)
                continue
            LOG.error(
                "[OPENROUTER REQUEST] fatal_exception attempt=%d/%d error=%s",
                attempt,
                max_retries,
                e,
            )
            raise last_error from e

    if last_error:
        raise last_error
    raise RuntimeError("HTTP request failed after all retries")


def list_available_models(api_key: str, api_base: str = "https://openrouter.ai/api/v1") -> List[str]:
    """
    List available models from OpenRouter.

    OpenRouter provides a /models endpoint that returns all available models.
    """
    url = api_base.rstrip("/") + "/models"
    hdrs = {
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw)

    models = []
    for m in data.get("data", []):
        if isinstance(m, dict) and m.get("id"):
            models.append(m["id"])
    return sorted(models)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="OpenRouter provider utilities for FPF")
    parser.add_argument("--list-models", action="store_true", help="List available models using OPENROUTER_API_KEY")
    parser.add_argument("--api-base", default="https://openrouter.ai/api/v1", help="Override API base URL")

    args = parser.parse_args()

    if args.list_models:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY not set in environment")
        try:
            models = list_available_models(api_key, api_base=args.api_base)
            print(json.dumps(models, indent=2))
        except Exception as exc:
            raise SystemExit(f"Failed to list models: {exc}")
