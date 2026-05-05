"""
AI-Q adapter.

Wraps the standalone NVIDIA AI-Q async jobs API so APICostX can treat AI-Q as
another generation provider without importing AI-Q internals directly.
"""

import asyncio
import contextlib
import inspect
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from app.adapters.base import BaseAdapter, GenerationConfig, GenerationResult, GeneratorType, ProgressCallback
from app.security.key_injection import PROVIDER_TO_ENV_VAR, inject_provider_keys_for_user_auto

logger = logging.getLogger(__name__)

SUPPORTED_RUNTIME_PROVIDERS = {"openai", "anthropic", "google", "openrouter", "nvidia"}
AIQ_DEFAULT_SOFT_TIMEOUT_SECONDS = 1800
AIQ_DEFAULT_JOB_EXPIRY_SECONDS = 86400
AIQ_HARD_WAIT_GRACE_SECONDS = 60
AIQ_STREAM_CONNECT_TIMEOUT_SECONDS = 5.0
AIQ_STREAM_READ_TIMEOUT_SECONDS = 45.0
AIQ_STREAM_RECONNECT_DELAY_SECONDS = 2.0
AIQ_LOG_PAYLOAD_PREVIEW_CHARS = 240
AIQ_LOG_DETAIL_MAX_JSON_CHARS = 6000
AIQ_STREAM_SUMMARY_INTERVAL_SECONDS = 15.0
AIQ_STREAM_SAMPLE_LIMIT = 12
AIQ_STREAM_VISIBLE_EVENT_TYPES = {
    "job.status",
    "job.error",
    "job.cancelled",
    "job.cancellation_requested",
    "job.shutdown",
    "workflow.start",
    "workflow.end",
}
OPENAI_GPT5_MAX_COMPLETION_TOKENS = 128_000
NVIDIA_NIM_CONTEXT_LIMITS = {
    "nvidia/nemotron-3-super-120b-a12b": 262_144,
}
OPENAI_DEFAULT_ONLY_SAMPLING_MODEL_PREFIXES = ("gpt-5",)
OPENAI_ONLY_PROFILE = "openai_web"
OPENAI_ONLY_PROFILE_ALIASES = {OPENAI_ONLY_PROFILE, "openai_web_canary", "acm_openai_web"}
DEFAULT_OPENAI_ONLY_DATA_SOURCES = ["web_search"]
AIQ_ROLE_BINDING_KEYS = (
    "intent_classifier_llm",
    "clarifier_llm",
    "clarifier_planner_llm",
    "shallow_research_llm",
    "orchestrator_llm",
    "researcher_llm",
    "planner_llm",
    "summary_model",
)
SECRET_FIELD_NAMES = {"api_key", "authorization", "token", "access_token", "refresh_token", "secret"}
NVIDIA_API_KEY_FALLBACK_FILES = (
    Path("/opt/apicostx/apicostx/.env"),
    Path("/home/ubuntu/experiments/aiq-2.0.0/deploy/.env"),
)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalize_provider(provider: str) -> str:
    if provider == "openaidp":
        return "openai"
    if provider == "googledp":
        return "google"
    return provider


def _split_model_key(model_key: str) -> tuple[str, str]:
    provider, sep, model = model_key.partition(":")
    if not sep or not provider or not model:
        raise ValueError(f"Invalid AI-Q APICostX model key: {model_key}")
    provider = _normalize_provider(provider.strip())
    if provider not in SUPPORTED_RUNTIME_PROVIDERS:
        raise ValueError(f"AI-Q does not support provider '{provider}' for APICostX model key {model_key}")
    return provider, model.strip()


def _model_key_from_config(provider: str, model: str) -> str:
    model_text = str(model or "").strip()
    if not provider or not model_text:
        return ""
    model_provider, sep, _ = model_text.partition(":")
    if sep and _normalize_provider(model_provider.strip().lower()) in SUPPORTED_RUNTIME_PROVIDERS:
        return model_text
    return f"{provider}:{model_text}"


def _to_litellm_model_name(provider: str, model: str) -> str:
    if provider == "nvidia":
        return model
    if provider == "openrouter":
        return f"openrouter/{model}"
    return f"{provider}/{model}"


def _get_system_nvidia_api_key() -> str:
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if api_key:
        return api_key

    for path in NVIDIA_API_KEY_FALLBACK_FILES:
        try:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("NVIDIA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def _extract_runtime_template(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {}
    allowed = {}
    for key in ("temperature", "top_p", "max_tokens", "max_completion_tokens", "num_retries", "seed"):
        value = section.get(key)
        if value is not None:
            allowed[key] = value
    if "max_retries" in section and "num_retries" not in allowed and section.get("max_retries") is not None:
        allowed["num_retries"] = section.get("max_retries")
    return allowed


def _first_runtime_template(sections: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Return the first non-empty GUI/runtime template for a generated AI-Q LLM."""
    for key in keys:
        template = _extract_runtime_template(sections.get(key))
        if template:
            return template
    return {}


def _openai_model_requires_default_sampling(model: str) -> bool:
    normalized_model = str(model or "").strip().lower()
    if normalized_model.startswith("openai/"):
        normalized_model = normalized_model.removeprefix("openai/")
    return normalized_model.startswith(OPENAI_DEFAULT_ONLY_SAMPLING_MODEL_PREFIXES)


def _cap_int_value(value: Any, maximum: int) -> tuple[Any, bool]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return value, False
    if parsed <= maximum:
        return value, False
    return maximum, True


def _normalize_openai_runtime_template(
    model: str,
    template: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Translate ACM's provider-neutral AI-Q knobs into native OpenAI config keys."""
    normalized = dict(template)
    adjustments: list[dict[str, Any]] = []

    max_tokens = normalized.pop("max_tokens", None)
    if max_tokens is not None and normalized.get("max_completion_tokens") is None:
        normalized["max_completion_tokens"] = max_tokens
        adjustments.append(
            {
                "action": "renamed",
                "from": "max_tokens",
                "to": "max_completion_tokens",
                "value": max_tokens,
                "reason": "Native OpenAI AI-Q configs use max_completion_tokens for this integration.",
            }
        )
    elif max_tokens is not None:
        adjustments.append(
            {
                "action": "omitted",
                "field": "max_tokens",
                "value": max_tokens,
                "reason": (
                    "max_completion_tokens was already provided, so the duplicate "
                    "max_tokens value was ignored."
                ),
            }
        )
    num_retries = normalized.pop("num_retries", None)
    if num_retries is not None and normalized.get("max_retries") is None:
        normalized["max_retries"] = num_retries
        adjustments.append(
            {
                "action": "renamed",
                "from": "num_retries",
                "to": "max_retries",
                "value": num_retries,
                "reason": "Native OpenAI AI-Q configs use max_retries.",
            }
        )
    elif num_retries is not None:
        adjustments.append(
            {
                "action": "omitted",
                "field": "num_retries",
                "value": num_retries,
                "reason": (
                    "max_retries was already provided, so the duplicate num_retries "
                    "value was ignored."
                ),
            }
        )

    if _openai_model_requires_default_sampling(model) and normalized.get("max_completion_tokens") is not None:
        original_value = normalized["max_completion_tokens"]
        capped_value, was_capped = _cap_int_value(original_value, OPENAI_GPT5_MAX_COMPLETION_TOKENS)
        if was_capped:
            normalized["max_completion_tokens"] = capped_value
            adjustments.append(
                {
                    "action": "capped",
                    "field": "max_completion_tokens",
                    "from_value": original_value,
                    "to_value": capped_value,
                    "reason": (
                        "This OpenAI model family rejects completion-token limits above "
                        f"{OPENAI_GPT5_MAX_COMPLETION_TOKENS}."
                    ),
                }
            )

    if _openai_model_requires_default_sampling(model):
        for sampling_key in ("temperature", "top_p"):
            if sampling_key not in normalized:
                continue
            sampling_value = normalized.pop(sampling_key)
            adjustments.append(
                {
                    "action": "omitted",
                    "field": sampling_key,
                    "value": sampling_value,
                    "reason": (
                        "This OpenAI model family only accepts default sampling for AI-Q/OpenAI runs; "
                        f"explicit {sampling_key} would make AI-Q reject the job."
                    ),
                }
            )

    return normalized, adjustments


def _normalize_nvidia_nim_runtime_template(
    template: dict[str, Any],
    model: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Translate ACM's provider-neutral AI-Q knobs into native NVIDIA NIM config keys."""
    normalized = dict(template)
    adjustments: list[dict[str, Any]] = []

    max_completion_tokens = normalized.pop("max_completion_tokens", None)
    if max_completion_tokens is not None and normalized.get("max_tokens") is None:
        normalized["max_tokens"] = max_completion_tokens
        adjustments.append(
            {
                "action": "renamed",
                "from": "max_completion_tokens",
                "to": "max_tokens",
                "value": max_completion_tokens,
                "reason": "Native NVIDIA NIM AI-Q configs use max_tokens.",
            }
        )
    elif max_completion_tokens is not None:
        adjustments.append(
            {
                "action": "omitted",
                "field": "max_completion_tokens",
                "value": max_completion_tokens,
                "reason": "max_tokens was already provided, so the duplicate max_completion_tokens value was ignored.",
            }
        )

    num_retries = normalized.pop("num_retries", None)
    if num_retries is not None and normalized.get("max_retries") is None:
        normalized["max_retries"] = num_retries
        adjustments.append(
            {
                "action": "renamed",
                "from": "num_retries",
                "to": "max_retries",
                "value": num_retries,
                "reason": "Native NVIDIA NIM AI-Q configs use max_retries.",
            }
        )
    elif num_retries is not None:
        adjustments.append(
            {
                "action": "omitted",
                "field": "num_retries",
                "value": num_retries,
                "reason": "max_retries was already provided, so the duplicate num_retries value was ignored.",
            }
        )

    context_limit = NVIDIA_NIM_CONTEXT_LIMITS.get(model)
    if context_limit and normalized.get("max_tokens") is not None:
        original_max_tokens = normalized["max_tokens"]
        safe_max_tokens = max(1, context_limit // 2)
        capped_max_tokens, was_capped = _cap_int_value(original_max_tokens, safe_max_tokens)
        if was_capped:
            normalized["max_tokens"] = capped_max_tokens
            adjustments.append(
                {
                    "action": "capped",
                    "field": "max_tokens",
                    "value": capped_max_tokens,
                    "original_value": original_max_tokens,
                    "reason": (
                        "Native NVIDIA NIM max_tokens is a completion budget inside the model context window; "
                        "AI-Q must leave room for prompts, tool state, and repair attempts."
                    ),
                }
            )

    return normalized, adjustments


def _validate_nvidia_runtime_payload(
    runtime_llm_overrides: dict[str, Any],
    llm_bindings: dict[str, str],
) -> None:
    """Fail fast if a bound NVIDIA report model would be submitted through the wrong provider path."""
    bound_llm_keys = {str(value).strip() for value in llm_bindings.values() if str(value).strip()}
    for llm_key in sorted(bound_llm_keys):
        llm_config = runtime_llm_overrides.get(llm_key)
        if not isinstance(llm_config, dict):
            continue
        llm_type = str(llm_config.get("_type") or "").strip().lower()
        model_name = str(llm_config.get("model_name") or "").strip()
        if llm_type == "nim":
            if not model_name:
                raise ValueError(f"AI-Q NVIDIA role {llm_key} is missing model_name")
            if model_name.startswith("nvidia_nim/"):
                raise ValueError(
                    f"AI-Q NVIDIA role {llm_key} must use the NIM model name without the LiteLLM "
                    "nvidia_nim/ prefix"
                )
            if not llm_config.get("api_key"):
                raise ValueError(f"AI-Q NVIDIA role {llm_key} is missing an injected NVIDIA API key")
            if "max_completion_tokens" in llm_config:
                raise ValueError(f"AI-Q NVIDIA role {llm_key} must use max_tokens, not max_completion_tokens")
            if "num_retries" in llm_config:
                raise ValueError(f"AI-Q NVIDIA role {llm_key} must use max_retries, not num_retries")
        if model_name.startswith("nvidia/") and llm_type != "nim":
            raise ValueError(
                f"AI-Q NVIDIA role {llm_key} must use native _type='nim', not {llm_type!r}"
            )


def _prune_unbound_nvidia_runtime_overrides(
    runtime_llm_overrides: dict[str, Any],
    llm_bindings: dict[str, str],
) -> list[str]:
    """Remove stale NVIDIA-like base LLM configs that are not referenced by active AI-Q role bindings."""
    bound_llm_keys = {str(value).strip() for value in llm_bindings.values() if str(value).strip()}
    removed: list[str] = []
    for llm_key, llm_config in list(runtime_llm_overrides.items()):
        if llm_key in bound_llm_keys or not isinstance(llm_config, dict):
            continue
        if _is_nvidia_like_llm(llm_config):
            runtime_llm_overrides.pop(llm_key, None)
            removed.append(llm_key)
    return removed


def _normalize_data_sources(data_sources: Any) -> list[str]:
    """Translate ACM/GUI source IDs into the names AI-Q's tool filter expects."""
    aliases = {
        "web": "web_search",
        "web_search": "web_search",
        "knowledge": "knowledge_layer",
        "knowledge_layer": "knowledge_layer",
        "documents": "knowledge_layer",
        "document": "knowledge_layer",
        "internal": "knowledge_layer",
    }
    normalized: list[str] = []
    for source in list(data_sources or []):
        source_key = str(source).strip()
        if not source_key:
            continue
        mapped = aliases.get(source_key.lower(), source_key)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


def _sanitize_config_overrides(config_overrides: Any) -> dict[str, Any]:
    sanitized = _redact_secrets(config_overrides)
    return sanitized if isinstance(sanitized, dict) else {}


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in SECRET_FIELD_NAMES or key_text.lower().endswith("_api_key"):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _json_for_log(value: Any) -> Any:
    """Return a JSON-safe, secret-redacted value for durable user logs."""
    return json.loads(json.dumps(_redact_secrets(value), default=str))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _truncate_log_text(value: Any, limit: int = AIQ_LOG_PAYLOAD_PREVIEW_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _compact_payload_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return {
            "type": "str",
            "chars": len(value),
            "preview": _truncate_log_text(value),
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            "first": _compact_payload_value(value[0]) if value else None,
        }
    if isinstance(value, dict):
        summary: dict[str, Any] = {
            "type": "dict",
            "keys": [str(key) for key in list(value.keys())[:20]],
        }
        for key in (
            "status",
            "name",
            "tool",
            "tool_name",
            "model",
            "model_name",
            "provider",
            "message",
            "error",
            "reason",
        ):
            if key in value:
                summary[key] = _compact_payload_value(value.get(key))
        usage = _extract_aiq_usage(value)
        if usage:
            summary["usage"] = {
                "input_tokens": _safe_nonnegative_int(
                    usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                    or usage.get("input_token_count")
                ),
                "output_tokens": _safe_nonnegative_int(
                    usage.get("output_tokens")
                    or usage.get("completion_tokens")
                    or usage.get("output_token_count")
                ),
                "total_tokens": _safe_nonnegative_int(
                    usage.get("total_tokens")
                    or usage.get("total_token_count")
                ),
            }
        for key in ("content", "output", "result", "results", "report", "documents"):
            if key in value and key not in summary:
                summary[key] = _compact_payload_value(value.get(key))
        return summary
    return {
        "type": type(value).__name__,
        "preview": _truncate_log_text(value),
    }


def _aiq_payload_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload": _compact_payload_value(payload)}
    summary: dict[str, Any] = {}
    for key in ("task_id", "job_id", "aiq_event_id", "aiq_event_type"):
        if key in payload:
            summary[key] = payload.get(key)
    if "data" in payload:
        summary["data"] = _compact_payload_value(payload.get("data"))
    else:
        for key, value in payload.items():
            if key not in summary:
                summary[key] = _compact_payload_value(value)
    return summary


def _aiq_payload_summary_text(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("task_id", "job_id", "aiq_event_id", "aiq_event_type"):
        value = summary.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    data = summary.get("data")
    if isinstance(data, dict):
        for key in ("status", "name", "tool", "tool_name", "model", "provider"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("preview") or value.get("chars")
            if value not in (None, ""):
                parts.append(f"{key}={value}")
    return "; ".join(parts[:8])


def _bounded_aiq_detail_payload(payload: Any) -> dict[str, Any]:
    safe_payload = _json_for_log(payload)
    payload_text = json.dumps(safe_payload, default=str, separators=(",", ":"))
    if len(payload_text) <= AIQ_LOG_DETAIL_MAX_JSON_CHARS:
        return safe_payload if isinstance(safe_payload, dict) else {"payload": safe_payload}
    return {
        "payload_summary": _aiq_payload_summary(payload),
        "payload_json_chars": len(payload_text),
        "payload_truncated": True,
        "payload_preview": payload_text[:AIQ_LOG_DETAIL_MAX_JSON_CHARS] + "...[truncated]",
    }


def _parse_sse_data(data_text: str) -> Any:
    if not data_text:
        return {}
    try:
        return json.loads(data_text)
    except json.JSONDecodeError:
        return {"raw": data_text}


def _safe_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0


def _extract_aiq_usage(data: Any) -> Optional[dict[str, Any]]:
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    candidates = []
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("usage"),
                metadata.get("usage_metadata"),
                metadata.get("token_usage"),
            ]
        )
    candidates.extend([data.get("usage"), data.get("usage_metadata"), data.get("token_usage")])
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None


def _usage_detail(usage: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = usage.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _aiq_event_level(event_type: str, data: Any) -> str:
    event_key = event_type.lower()
    status = ""
    if isinstance(data, dict):
        status = str(data.get("status") or "").lower()
    if "error" in event_key or status in {"failed", "failure", "error"}:
        return "ERROR"
    if "cancel" in event_key or "shutdown" in event_key or status in {"cancelled", "canceled", "interrupted"}:
        return "WARNING"
    if event_key in {"job.heartbeat", "stream.mode"}:
        return "DEBUG"
    return "INFO"


def _aiq_event_message(event_type: str, data: Any) -> str:
    if not isinstance(data, dict):
        return f"AI-Q event: {event_type}"

    if event_type == "job.status":
        return f"AI-Q job status: {data.get('status') or 'unknown'}"
    if event_type == "job.heartbeat":
        uptime = data.get("uptime_seconds")
        return f"AI-Q heartbeat{f': {uptime}s' if uptime is not None else ''}"
    if event_type == "stream.mode":
        mode = data.get("mode")
        return f"AI-Q stream mode: {mode or 'unknown'}"
    if event_type == "tool.start":
        name = data.get("name") or data.get("tool") or "unknown"
        return f"AI-Q tool started: {name}"
    if event_type == "tool.end":
        name = data.get("name") or data.get("tool") or "unknown"
        status = data.get("status")
        return f"AI-Q tool ended: {name}{f' ({status})' if status else ''}"
    if event_type in {"job.error", "job.cancelled", "job.cancellation_requested", "job.shutdown"}:
        error = data.get("error") or data.get("reason") or data.get("message")
        return f"AI-Q {event_type.replace('.', ' ')}{f': {error}' if error else ''}"

    name = data.get("name") or data.get("type") or data.get("status")
    return f"AI-Q event: {event_type}{f' ({name})' if name else ''}"


def _aiq_event_is_terminal(event_type: str, data: Any) -> bool:
    if event_type in {"job.error", "job.cancelled", "job.shutdown"}:
        return True
    if event_type != "job.status" or not isinstance(data, dict):
        return False
    status = str(data.get("status") or "").lower()
    return status in {"success", "completed", "failure", "failed", "interrupted", "cancelled", "canceled"}


def _aiq_terminal_failure_message(event_type: str, data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if event_type == "job.error":
        return str(data.get("error") or data.get("message") or "AI-Q job failed")
    if event_type in {"job.cancelled", "job.shutdown"}:
        return str(data.get("reason") or data.get("message") or f"AI-Q {event_type.replace('.', ' ')}")
    if event_type != "job.status":
        return None
    status = str(data.get("status") or "").lower()
    if status in {"failure", "failed", "error"}:
        return str(data.get("error") or data.get("message") or "AI-Q job failed")
    if status in {"interrupted", "cancelled", "canceled"}:
        return str(data.get("reason") or data.get("message") or "AI-Q job cancelled")
    return None


def _increment_aiq_count(counts: dict[str, int], key: Any) -> None:
    normalized = str(key or "unknown").strip() or "unknown"
    counts[normalized] = int(counts.get(normalized, 0) or 0) + 1


def _aiq_stream_actor_name(event_type: str, data: Any) -> str:
    if not isinstance(data, dict):
        return "unknown"
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if event_type.startswith("tool."):
        return _aiq_tool_name(data) or str(data.get("type") or "unknown").strip() or "unknown"
    if event_type.startswith("llm."):
        return (
            str(
                data.get("model")
                or data.get("model_name")
                or data.get("llm")
                or data.get("name")
                or metadata.get("model")
                or metadata.get("provider")
                or "unknown"
            ).strip()
            or "unknown"
        )
    if event_type == "artifact.update":
        return (
            str(
                data.get("name")
                or data.get("artifact")
                or data.get("artifact_name")
                or data.get("artifact_id")
                or data.get("path")
                or data.get("type")
                or "artifact"
            ).strip()
            or "artifact"
        )
    return str(data.get("name") or data.get("type") or data.get("status") or "unknown").strip() or "unknown"


def _new_aiq_stream_summary(*, task_id: str, job_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "job_id": job_id,
        "total_events": 0,
        "first_event_id": None,
        "last_event_id": None,
        "last_event_type": "",
        "event_counts": {},
        "tool_counts": {},
        "llm_counts": {},
        "artifact_updates": 0,
        "artifact_samples": [],
        "warning_samples": [],
        "_last_summary_at": 0.0,
        "_last_summary_total_events": 0,
    }


def _add_aiq_stream_sample(samples: list[str], value: Any) -> None:
    sample = str(value or "").strip()
    if not sample or sample in samples or len(samples) >= AIQ_STREAM_SAMPLE_LIMIT:
        return
    samples.append(sample)


def _update_aiq_stream_summary(
    summary: dict[str, Any],
    *,
    event_id: Optional[int],
    event_type: str,
    data: Any,
) -> None:
    summary["total_events"] = int(summary.get("total_events", 0) or 0) + 1
    if summary.get("first_event_id") is None and event_id is not None:
        summary["first_event_id"] = event_id
    if event_id is not None:
        summary["last_event_id"] = event_id
    summary["last_event_type"] = event_type

    event_counts = summary.setdefault("event_counts", {})
    if isinstance(event_counts, dict):
        _increment_aiq_count(event_counts, event_type)

    actor_name = _aiq_stream_actor_name(event_type, data)
    if event_type.startswith("tool."):
        tool_counts = summary.setdefault("tool_counts", {})
        if isinstance(tool_counts, dict):
            _increment_aiq_count(tool_counts, f"{actor_name}.{event_type.rsplit('.', 1)[-1]}")
    elif event_type.startswith("llm."):
        llm_counts = summary.setdefault("llm_counts", {})
        if isinstance(llm_counts, dict):
            _increment_aiq_count(llm_counts, f"{actor_name}.{event_type.rsplit('.', 1)[-1]}")
    elif event_type == "artifact.update":
        summary["artifact_updates"] = int(summary.get("artifact_updates", 0) or 0) + 1
        artifact_samples = summary.setdefault("artifact_samples", [])
        if isinstance(artifact_samples, list):
            _add_aiq_stream_sample(artifact_samples, actor_name)

    level = _aiq_event_level(event_type, data)
    if level in {"WARNING", "ERROR"}:
        warning_samples = summary.setdefault("warning_samples", [])
        if isinstance(warning_samples, list):
            _add_aiq_stream_sample(warning_samples, _aiq_event_message(event_type, data))


def _top_aiq_counts(counts: Any, *, limit: int = 5) -> str:
    if not isinstance(counts, dict) or not counts:
        return ""
    items = sorted(
        ((str(key), int(value or 0)) for key, value in counts.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return ", ".join(f"{key}={value}" for key, value in items[:limit])


def _aiq_stream_summary_snapshot(summary: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not summary:
        return {}
    snapshot: dict[str, Any] = {}
    for key, value in summary.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            snapshot[key] = dict(value)
        elif isinstance(value, list):
            snapshot[key] = list(value)
        else:
            snapshot[key] = value
    return snapshot


def _aiq_stream_summary_message(summary: dict[str, Any], *, final: bool) -> str:
    prefix = "AI-Q stream final summary" if final else "AI-Q stream summary"
    parts = [f"{prefix}: {int(summary.get('total_events', 0) or 0)} events"]
    event_counts = _top_aiq_counts(summary.get("event_counts"), limit=5)
    if event_counts:
        parts.append(f"event_counts: {event_counts}")
    tool_counts = _top_aiq_counts(summary.get("tool_counts"), limit=4)
    if tool_counts:
        parts.append(f"tools: {tool_counts}")
    llm_counts = _top_aiq_counts(summary.get("llm_counts"), limit=4)
    if llm_counts:
        parts.append(f"llms: {llm_counts}")
    artifact_updates = int(summary.get("artifact_updates", 0) or 0)
    if artifact_updates:
        parts.append(f"artifact_updates={artifact_updates}")
    last_event_type = summary.get("last_event_type")
    last_event_id = summary.get("last_event_id")
    if last_event_type:
        parts.append(f"last={last_event_type}#{last_event_id if last_event_id is not None else '?'}")
    return "; ".join(parts)


def _aiq_should_log_stream_event(event_type: str, data: Any) -> bool:
    if event_type in {"job.heartbeat", "stream.mode"}:
        return False
    if _aiq_event_level(event_type, data) in {"WARNING", "ERROR"}:
        return True
    if _aiq_event_is_terminal(event_type, data):
        return True
    return event_type in AIQ_STREAM_VISIBLE_EVENT_TYPES


def _aiq_stream_summary_due(summary: dict[str, Any], *, now: float) -> bool:
    last_summary_at = float(summary.get("_last_summary_at", 0.0) or 0.0)
    if last_summary_at <= 0.0:
        summary["_last_summary_at"] = now
        summary["_last_summary_total_events"] = int(summary.get("total_events", 0) or 0)
        return False
    if now - last_summary_at < AIQ_STREAM_SUMMARY_INTERVAL_SECONDS:
        return False
    total_events = int(summary.get("total_events", 0) or 0)
    if total_events <= int(summary.get("_last_summary_total_events", 0) or 0):
        return False
    summary["_last_summary_at"] = now
    summary["_last_summary_total_events"] = total_events
    return True


def _aiq_adjustment_rollup(adjustments: list[dict[str, Any]]) -> tuple[str, str]:
    action_counts: dict[str, int] = {}
    examples: list[str] = []
    level = "INFO"
    for adjustment in adjustments:
        action = str(adjustment.get("action") or "adjusted")
        field = str(adjustment.get("field") or adjustment.get("from") or "parameter")
        llm_key = str(adjustment.get("llm_key") or "unknown_llm")
        model = str(adjustment.get("model") or "").strip()
        _increment_aiq_count(action_counts, action)
        if action == "omitted":
            level = "WARNING"
        if len(examples) < 5:
            model_text = f" ({model})" if model else ""
            examples.append(f"{action} {field} for {llm_key}{model_text}")

    actions = _top_aiq_counts(action_counts, limit=5)
    parts = [f"{len(adjustments)} change(s)"]
    if actions:
        parts.append(f"actions: {actions}")
    if examples:
        parts.append(f"examples: {', '.join(examples)}")
    return level, "; ".join(parts)


def _is_openai_only_run(provider: Any, profile: str) -> bool:
    return _normalize_provider(str(provider or "").strip().lower()) == "openai" or profile in OPENAI_ONLY_PROFILE_ALIASES


def _is_nvidia_like_llm(llm_config: dict[str, Any]) -> bool:
    llm_type = str(llm_config.get("_type") or "").strip().lower()
    model_name = str(llm_config.get("model_name") or llm_config.get("model") or "").strip().lower()
    base_url = str(llm_config.get("base_url") or llm_config.get("api_base") or "").strip().lower()
    return (
        llm_type == "nim"
        or model_name.startswith("nvidia/")
        or model_name.startswith("openai/gpt-oss")
        or "integrate.api.nvidia.com" in base_url
    )


def _validate_openai_only_payload(
    *,
    profile: str,
    data_sources: list[str],
    llm_bindings: dict[str, str],
    runtime_llm_overrides: dict[str, Any],
    advanced_yaml_overrides: dict[str, Any],
) -> None:
    if profile not in OPENAI_ONLY_PROFILE_ALIASES:
        raise ValueError(f"OpenAI-only AI-Q runs must use the {OPENAI_ONLY_PROFILE!r} profile")
    if advanced_yaml_overrides:
        raise ValueError("OpenAI-only AI-Q canary does not allow advanced_yaml_overrides")
    if data_sources != DEFAULT_OPENAI_ONLY_DATA_SOURCES:
        raise ValueError("OpenAI-only AI-Q canary must use data_sources=['web_search']")

    for llm_key, llm_config in runtime_llm_overrides.items():
        if not isinstance(llm_config, dict):
            raise ValueError(f"OpenAI-only AI-Q LLM override {llm_key!r} must be a dict")
        llm_type = str(llm_config.get("_type") or "").strip().lower()
        if llm_type != "openai":
            raise ValueError(f"OpenAI-only AI-Q LLM override {llm_key!r} must use _type='openai'")
        if _is_nvidia_like_llm(llm_config):
            raise ValueError(f"OpenAI-only AI-Q LLM override {llm_key!r} contains NVIDIA/NIM values")

    missing_binding_keys = [key for key in AIQ_ROLE_BINDING_KEYS if key not in llm_bindings]
    if missing_binding_keys:
        raise ValueError(f"OpenAI-only AI-Q run is missing role bindings: {missing_binding_keys}")

    for binding_key in AIQ_ROLE_BINDING_KEYS:
        llm_key = llm_bindings[binding_key]
        llm_config = runtime_llm_overrides.get(llm_key)
        if not isinstance(llm_config, dict):
            raise ValueError(f"AI-Q role {binding_key} references missing LLM {llm_key!r}")

        llm_type = str(llm_config.get("_type") or "").strip().lower()
        if llm_type != "openai":
            raise ValueError(f"AI-Q role {binding_key} must use native _type='openai', not {llm_type!r}")
        if _is_nvidia_like_llm(llm_config):
            raise ValueError(f"AI-Q role {binding_key} contains NVIDIA/NIM model or endpoint values")
        if not llm_config.get("api_key"):
            raise ValueError(f"AI-Q role {binding_key} is missing an injected OpenAI API key")
        if "max_tokens" in llm_config:
            raise ValueError(f"AI-Q role {binding_key} must use max_completion_tokens, not max_tokens")
        model_name = str(llm_config.get("model_name") or "").strip()
        if _openai_model_requires_default_sampling(model_name):
            forbidden_sampling_keys = [
                key for key in ("temperature", "top_p")
                if llm_config.get(key) is not None
            ]
            if forbidden_sampling_keys:
                raise ValueError(
                    f"AI-Q role {binding_key} uses OpenAI model {model_name!r} with unsupported "
                    f"sampling fields: {forbidden_sampling_keys}"
                )


def _write_aiq_debug_artifact(
    *,
    user_uuid: str,
    extra: dict[str, Any],
    filename: str,
    payload: Any,
) -> Optional[str]:
    run_id = str(extra.get("run_id") or "").strip()
    if not run_id:
        return None
    try:
        from app.config import get_settings

        settings = get_settings()
        artifact_dir = settings.data_dir / f"user_{user_uuid}" / "runs" / run_id / "logs" / "aiq"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", str(filename or "aiq_debug_artifact.json")).strip("._")
        filename = filename or "aiq_debug_artifact.json"
        artifact_path = artifact_dir / filename
        artifact_path.write_text(json.dumps(_redact_secrets(payload), indent=2, sort_keys=True), encoding="utf-8")
        return str(artifact_path)
    except Exception as exc:
        logger.warning("Failed to write AI-Q debug artifact %s: %s", filename, exc)
        return None


class AiqAdapter(BaseAdapter):
    """HTTP adapter for the standalone AI-Q service."""

    def __init__(self, base_url: Optional[str] = None, poll_interval_seconds: float = 2.0):
        self.base_url = (base_url or os.getenv("API_COST_X_AIQ_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self._task_to_job: dict[str, str] = {}
        self._cancel_requested: set[str] = set()

    @property
    def name(self) -> GeneratorType:
        return GeneratorType.AIQ

    @property
    def display_name(self) -> str:
        return "AI-Q"

    async def _emit_progress(
        self,
        progress_callback: Optional[ProgressCallback],
        stage: str,
        progress: float,
        message: str,
    ) -> None:
        if progress_callback is None:
            return
        result = progress_callback(stage, progress, message)
        if inspect.isawaitable(result):
            await result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"

        def _do_request() -> dict[str, Any]:
            response = requests.request(method, url, json=json_body, timeout=timeout)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

        return await asyncio.to_thread(_do_request)

    async def _maybe_await(self, result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    async def _flush_log_writer(self, log_writer: Any) -> None:
        flush = getattr(log_writer, "flush", None)
        if not callable(flush):
            return
        try:
            await self._maybe_await(flush())
        except Exception as exc:
            logger.debug("AI-Q log writer flush failed: %s", exc)

    async def _write_aiq_log(
        self,
        log_writer: Any,
        *,
        level: str,
        event_type: str,
        message: str,
        payload: Any = None,
    ) -> None:
        if log_writer is None:
            return

        event = getattr(log_writer, "event", None)
        if not callable(event):
            return

        event_message = message
        if payload is not None:
            summary_text = _aiq_payload_summary_text(_aiq_payload_summary(payload))
            if summary_text:
                event_message = f"{message} ({summary_text})"

        await self._maybe_await(event("aiq", level, event_type, event_message))

        if payload is not None and _env_flag("API_COST_X_AIQ_LOG_DETAIL_PAYLOADS", default=False):
            detail = getattr(log_writer, "detail", None)
            if callable(detail):
                await self._maybe_await(
                    detail(
                        "aiq",
                        level,
                        f"{message} payload summary",
                        payload=_bounded_aiq_detail_payload(payload),
                        event_type=event_type,
                    )
                )

    async def _write_aiq_stream_summary(
        self,
        log_writer: Any,
        stream_summary: Optional[dict[str, Any]],
        *,
        final: bool,
    ) -> None:
        if log_writer is None or not stream_summary:
            return
        snapshot = _aiq_stream_summary_snapshot(stream_summary)
        if int(snapshot.get("total_events", 0) or 0) <= 0:
            return

        message = _aiq_stream_summary_message(snapshot, final=final)
        event = getattr(log_writer, "event", None)
        if callable(event):
            await self._maybe_await(event("aiq", "INFO", "aiq_stream_summary", message))

        if final:
            detail = getattr(log_writer, "detail", None)
            if callable(detail):
                await self._maybe_await(
                    detail(
                        "aiq",
                        "INFO",
                        "AI-Q stream final summary detail",
                        payload=snapshot,
                        event_type="aiq_stream_summary",
                    )
                )

    async def _record_aiq_usage_event(
        self,
        *,
        event_type: str,
        data: Any,
        usage_totals: Optional[dict[str, Any]],
    ) -> bool:
        if event_type != "llm.end":
            return False

        usage = _extract_aiq_usage(data)
        if not usage:
            return False

        input_details = _usage_detail(
            usage,
            "input_token_details",
            "input_tokens_details",
            "prompt_tokens_details",
        )
        output_details = _usage_detail(
            usage,
            "output_token_details",
            "output_tokens_details",
            "completion_tokens_details",
        )
        input_tokens = _safe_nonnegative_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        output_tokens = _safe_nonnegative_int(usage.get("output_tokens") or usage.get("completion_tokens"))
        total_tokens = _safe_nonnegative_int(usage.get("total_tokens"))
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        cached_tokens = _safe_nonnegative_int(
            input_details.get("cached_tokens")
            or input_details.get("cache_read")
            or usage.get("cached_tokens")
            or usage.get("cache_read")
        )
        reasoning_tokens = _safe_nonnegative_int(
            output_details.get("reasoning_tokens")
            or output_details.get("reasoning")
            or usage.get("reasoning_tokens")
        )

        if usage_totals is not None:
            usage_totals["input_tokens"] = int(usage_totals.get("input_tokens", 0) or 0) + input_tokens
            usage_totals["output_tokens"] = int(usage_totals.get("output_tokens", 0) or 0) + output_tokens
            usage_totals["total_tokens"] = int(usage_totals.get("total_tokens", 0) or 0) + total_tokens
            usage_totals["cached_tokens"] = int(usage_totals.get("cached_tokens", 0) or 0) + cached_tokens
            usage_totals["reasoning_tokens"] = int(usage_totals.get("reasoning_tokens", 0) or 0) + reasoning_tokens

        logger.debug(
            "AI-Q usage captured without persistence: input_tokens=%s output_tokens=%s total_tokens=%s",
            input_tokens,
            output_tokens,
            total_tokens,
        )
        return True
    async def _log_aiq_stream_event(
        self,
        log_writer: Any,
        *,
        task_id: str,
        job_id: str,
        event_id: Optional[int],
        event_type: str,
        data: Any,
        usage_totals: Optional[dict[str, Any]] = None,
        stream_summary: Optional[dict[str, Any]] = None,
    ) -> bool:
        if stream_summary is not None:
            _update_aiq_stream_summary(
                stream_summary,
                event_id=event_id,
                event_type=event_type,
                data=data,
            )

        should_log_event = _aiq_should_log_stream_event(event_type, data)
        payload = {
            "task_id": task_id,
            "job_id": job_id,
            "aiq_event_id": event_id,
            "aiq_event_type": event_type,
            "data": data,
        }
        if should_log_event:
            await self._write_aiq_log(
                log_writer,
                level=_aiq_event_level(event_type, data),
                event_type=event_type,
                message=_aiq_event_message(event_type, data),
                payload=payload,
            )
        await self._record_aiq_usage_event(
            event_type=event_type,
            data=data,
            usage_totals=usage_totals,
        )
        if (
            stream_summary is not None
            and log_writer is not None
            and _aiq_stream_summary_due(stream_summary, now=asyncio.get_running_loop().time())
        ):
            await self._write_aiq_stream_summary(log_writer, stream_summary, final=False)
        return _aiq_event_is_terminal(event_type, data)

    def _read_aiq_stream_once(
        self,
        *,
        job_id: str,
        start_event_id: int,
        stop_event: threading.Event,
        on_event,
    ) -> tuple[int, bool]:
        path = (
            f"/v1/jobs/async/job/{job_id}/stream"
            if start_event_id <= 0
            else f"/v1/jobs/async/job/{job_id}/stream/{start_event_id}"
        )
        url = f"{self.base_url}{path}"
        last_event_id = start_event_id
        event_id: Optional[int] = None
        event_type = "message"
        data_lines: list[str] = []
        terminal_seen = False

        def dispatch() -> None:
            nonlocal event_id, event_type, data_lines, last_event_id, terminal_seen
            if event_id is None and not data_lines and event_type == "message":
                return
            data = _parse_sse_data("\n".join(data_lines))
            if event_id is not None:
                last_event_id = max(last_event_id, event_id)
            terminal_seen = bool(on_event(event_id, event_type, data)) or terminal_seen
            event_id = None
            event_type = "message"
            data_lines = []

        with requests.get(
            url,
            stream=True,
            timeout=(AIQ_STREAM_CONNECT_TIMEOUT_SECONDS, AIQ_STREAM_READ_TIMEOUT_SECONDS),
        ) as response:
            response.raise_for_status()
            for raw_line in response.iter_lines(decode_unicode=True):
                if stop_event.is_set() or terminal_seen:
                    break
                if raw_line is None:
                    continue
                line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
                if line == "":
                    dispatch()
                    continue
                if line.startswith(":"):
                    continue

                field, sep, value = line.partition(":")
                if not sep:
                    continue
                if value.startswith(" "):
                    value = value[1:]
                if field == "id":
                    try:
                        event_id = int(value)
                    except ValueError:
                        event_id = None
                elif field == "event":
                    event_type = value or "message"
                elif field == "data":
                    data_lines.append(value)

            if data_lines or event_id is not None or event_type != "message":
                dispatch()

        return last_event_id, terminal_seen

    async def _capture_aiq_stream(
        self,
        *,
        job_id: str,
        task_id: str,
        log_writer: Any,
        stop_event: threading.Event,
        terminal_failures: Optional[dict[str, Any]] = None,
        usage_totals: Optional[dict[str, Any]] = None,
        stream_summary: Optional[dict[str, Any]] = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        last_event_id = 0
        if stream_summary is None:
            stream_summary = _new_aiq_stream_summary(task_id=task_id, job_id=job_id)

        def on_event(event_id: Optional[int], event_type: str, data: Any) -> bool:
            failure_message = _aiq_terminal_failure_message(event_type, data)
            if failure_message and terminal_failures is not None:
                terminal_failures.update(
                    {
                        "message": failure_message,
                        "event_id": event_id,
                        "event_type": event_type,
                        "data": data,
                    }
                )
            future = asyncio.run_coroutine_threadsafe(
                self._log_aiq_stream_event(
                    log_writer,
                    task_id=task_id,
                    job_id=job_id,
                    event_id=event_id,
                    event_type=event_type,
                    data=data,
                    usage_totals=usage_totals,
                    stream_summary=stream_summary,
                ),
                loop,
            )
            return bool(future.result(timeout=AIQ_STREAM_READ_TIMEOUT_SECONDS))

        try:
            await self._write_aiq_log(
                log_writer,
                level="INFO",
                event_type="aiq_stream_start",
                message=f"AI-Q stream capture starting: job_id={job_id}",
                payload={"task_id": task_id, "job_id": job_id},
            )

            while not stop_event.is_set():
                try:
                    last_event_id, terminal_seen = await asyncio.to_thread(
                        self._read_aiq_stream_once,
                        job_id=job_id,
                        start_event_id=last_event_id,
                        stop_event=stop_event,
                        on_event=on_event,
                    )
                    if terminal_seen:
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._write_aiq_log(
                        log_writer,
                        level="WARNING",
                        event_type="aiq_stream_error",
                        message=(
                            "AI-Q stream capture error; will retry from event "
                            f"{last_event_id}: {type(exc).__name__}"
                        ),
                        payload={
                            "task_id": task_id,
                            "job_id": job_id,
                            "last_event_id": last_event_id,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(AIQ_STREAM_RECONNECT_DELAY_SECONDS)
                    continue

                await asyncio.sleep(AIQ_STREAM_RECONNECT_DELAY_SECONDS)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(self._write_aiq_stream_summary(log_writer, stream_summary, final=True))

    async def health_check(self) -> bool:
        try:
            payload = await self._request("GET", "/health", timeout=10.0)
        except Exception as exc:
            logger.warning("AI-Q health check failed: %s", exc)
            return False
        return bool(payload) and payload.get("status") == "healthy"

    async def cancel(self, task_id: str) -> bool:
        self._cancel_requested.add(task_id)
        job_id = self._task_to_job.get(task_id)
        if not job_id:
            return False
        try:
            await self._request("POST", f"/v1/jobs/async/job/{job_id}/cancel", timeout=15.0)
            return True
        except Exception as exc:
            logger.warning("AI-Q cancel failed for task=%s job=%s: %s", task_id, job_id, exc)
            return False

    async def generate(
        self,
        query: str,
        config: GenerationConfig,
        *,
        user_uuid: str,
        document_content: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        log_writer: Any = None,
    ) -> GenerationResult:
        del document_content

        if not query or not query.strip():
            raise ValueError("AI-Q query is required")

        extra = config.extra or {}
        task_id = str(extra.get("task_id") or str(uuid.uuid4())[:8])
        agent_type = str(extra.get("agent_type") or "deep_researcher")
        timeout_seconds = _coerce_positive_int(
            extra.get("timeout_seconds"),
            AIQ_DEFAULT_SOFT_TIMEOUT_SECONDS,
        )
        expiry_seconds = _coerce_positive_int(
            extra.get("job_expiry_seconds"),
            AIQ_DEFAULT_JOB_EXPIRY_SECONDS,
        )
        key_mode = str(extra.get("key_mode") or "system")
        profile = str(extra.get("profile") or "deep_web_default").strip() or "deep_web_default"
        small_model = str(extra.get("small_model") or "").strip()
        data_sources = _normalize_data_sources(extra.get("data_sources"))
        started_at = datetime.utcnow()
        config_provider = _normalize_provider(str(config.provider or "").strip().lower())
        if config_provider == "openai" and profile in {"deep_web_default", "web_default_llamaindex"}:
            profile = OPENAI_ONLY_PROFILE
        openai_only_run = _is_openai_only_run(config_provider, profile)
        if openai_only_run and not data_sources:
            data_sources = list(DEFAULT_OPENAI_ONLY_DATA_SOURCES)
        llm_bindings = {
            key: value.strip()
            for key in AIQ_ROLE_BINDING_KEYS
            for value in [extra.get(key)]
            if isinstance(value, str) and value.strip()
        }
        config_overrides = extra.get("config_overrides")
        if not isinstance(config_overrides, dict):
            config_overrides = {}
        advanced_yaml_overrides = extra.get("advanced_yaml_overrides")
        if not isinstance(advanced_yaml_overrides, dict):
            advanced_yaml_overrides = {}

        provider_env: dict[str, str] = {}
        base_llm_overrides = dict(config_overrides.get("llms") or {}) if isinstance(config_overrides.get("llms"), dict) else {}
        runtime_config_overrides = dict(config_overrides)
        runtime_llm_overrides = {} if openai_only_run else dict(base_llm_overrides)
        openai_parameter_adjustments: list[dict[str, Any]] = []
        nvidia_parameter_adjustments: list[dict[str, Any]] = []
        pruned_nvidia_llm_overrides: list[str] = []
        if openai_only_run:
            runtime_config_overrides.pop("llms", None)

        big_model_key = ""
        if config.provider and config.model:
            big_model_key = _model_key_from_config(config_provider, config.model)

        def _build_runtime_llm(model_key: str, template: dict[str, Any], llm_key: str) -> dict[str, Any]:
            provider, base_model = _split_model_key(model_key)
            env_var = PROVIDER_TO_ENV_VAR.get(provider)
            api_key = provider_env.get(env_var, "") if env_var else ""
            if provider == "nvidia" and not api_key:
                api_key = _get_system_nvidia_api_key()
            if not api_key:
                raise ValueError(f"Missing API key for AI-Q APICostX model provider '{provider}'")

            if provider == "openai":
                normalized_template, adjustments = _normalize_openai_runtime_template(base_model, template)
                for adjustment in adjustments:
                    openai_parameter_adjustments.append(
                        {
                            "llm_key": llm_key,
                            "model": base_model,
                            **adjustment,
                        }
                    )
                return {
                    "_type": "openai",
                    "model_name": base_model,
                    "api_key": api_key,
                    **normalized_template,
                }

            if provider == "nvidia":
                normalized_template, adjustments = _normalize_nvidia_nim_runtime_template(template, base_model)
                for adjustment in adjustments:
                    nvidia_parameter_adjustments.append(
                        {
                            "llm_key": llm_key,
                            "model": base_model,
                            **adjustment,
                        }
                    )
                return {
                    "_type": "nim",
                    "model_name": base_model,
                    "api_key": api_key,
                    **normalized_template,
                }

            payload = {
                "_type": "litellm",
                "model_name": _to_litellm_model_name(provider, base_model),
                "api_key": api_key,
                **template,
            }
            if provider == "openrouter":
                payload.setdefault("base_url", "https://openrouter.ai/api/v1")
            return payload

        if big_model_key or small_model:
            provider_env = await inject_provider_keys_for_user_auto(user_uuid, {}, key_mode=key_mode)

        big_provider = ""
        if big_model_key:
            big_provider, _ = _split_model_key(big_model_key)

        if big_model_key and config_provider != "aiq":
            if big_provider == "openai":
                nano_llm_key = "acm_nano_llm"
                big_llm_key = "acm_big_llm"
                nano_template = _first_runtime_template(
                    base_llm_overrides,
                    nano_llm_key,
                    "nemotron_nano_llm",
                )
                big_template = _first_runtime_template(
                    base_llm_overrides,
                    big_llm_key,
                    "gpt_oss_llm",
                    "openai_gpt_5_2",
                )
            else:
                nano_llm_key = "nemotron_nano_llm" if big_provider == "nvidia" else "acm_nano_llm"
                big_llm_key = "gpt_oss_llm" if big_provider == "nvidia" else "acm_big_llm"
                nano_template = _first_runtime_template(
                    base_llm_overrides,
                    nano_llm_key,
                    "acm_nano_llm",
                    "nemotron_nano_llm",
                )
                big_template = _first_runtime_template(
                    base_llm_overrides,
                    big_llm_key,
                    "acm_big_llm",
                    "gpt_oss_llm",
                    "openai_gpt_5_2",
                )
            runtime_llm_overrides[big_llm_key] = _build_runtime_llm(big_model_key, big_template, big_llm_key)
            llm_bindings.update(
                {
                    "orchestrator_llm": big_llm_key,
                    "planner_llm": big_llm_key,
                }
            )

            if not small_model:
                runtime_llm_overrides[nano_llm_key] = _build_runtime_llm(big_model_key, nano_template, nano_llm_key)
                llm_bindings.update(
                    {
                        "clarifier_llm": nano_llm_key,
                        "clarifier_planner_llm": nano_llm_key,
                        "shallow_research_llm": nano_llm_key,
                        "researcher_llm": nano_llm_key,
                    }
                )

            if big_provider == "openai" and not small_model:
                intent_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_intent_llm",
                    "nemotron_llm_intent",
                )
                summary_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_summary_llm",
                    "summary_llm",
                )
                runtime_llm_overrides["acm_intent_llm"] = _build_runtime_llm(
                    big_model_key,
                    intent_template,
                    "acm_intent_llm",
                )
                runtime_llm_overrides["acm_summary_llm"] = _build_runtime_llm(
                    big_model_key,
                    summary_template,
                    "acm_summary_llm",
                )
                llm_bindings.update(
                    {
                        "intent_classifier_llm": "acm_intent_llm",
                        "summary_model": "acm_summary_llm",
                    }
                )

        if small_model:
            small_provider, _ = _split_model_key(small_model)
            if small_provider == "openai":
                intent_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_intent_llm",
                    "nemotron_llm_intent",
                )
                nano_template = _first_runtime_template(
                    base_llm_overrides,
                    "nemotron_nano_llm",
                    "acm_nano_llm",
                )
                summary_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_summary_llm",
                    "summary_llm",
                )
            else:
                intent_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_intent_llm",
                    "nemotron_llm_intent",
                )
                nano_template = _first_runtime_template(
                    base_llm_overrides,
                    "nemotron_nano_llm",
                    "acm_nano_llm",
                )
                summary_template = _first_runtime_template(
                    base_llm_overrides,
                    "acm_summary_llm",
                    "summary_llm",
                )
            runtime_llm_overrides["acm_intent_llm"] = _build_runtime_llm(
                small_model,
                intent_template,
                "acm_intent_llm",
            )
            runtime_llm_overrides["nemotron_nano_llm"] = _build_runtime_llm(
                small_model,
                nano_template,
                "nemotron_nano_llm",
            )
            runtime_llm_overrides["acm_summary_llm"] = _build_runtime_llm(
                small_model,
                summary_template,
                "acm_summary_llm",
            )
            llm_bindings.update(
                {
                    "intent_classifier_llm": "acm_intent_llm",
                    "clarifier_llm": "nemotron_nano_llm",
                    "clarifier_planner_llm": "nemotron_nano_llm",
                    "shallow_research_llm": "nemotron_nano_llm",
                    "researcher_llm": "nemotron_nano_llm",
                    "summary_model": "acm_summary_llm",
                }
            )

        pruned_nvidia_llm_overrides = _prune_unbound_nvidia_runtime_overrides(runtime_llm_overrides, llm_bindings)
        if runtime_llm_overrides:
            runtime_config_overrides["llms"] = runtime_llm_overrides
        elif "llms" in runtime_config_overrides:
            runtime_config_overrides.pop("llms", None)
        _validate_nvidia_runtime_payload(runtime_llm_overrides, llm_bindings)

        if openai_only_run:
            _validate_openai_only_payload(
                profile=profile,
                data_sources=data_sources,
                llm_bindings=llm_bindings,
                runtime_llm_overrides=runtime_llm_overrides,
                advanced_yaml_overrides=advanced_yaml_overrides,
            )

        submit_payload: dict[str, Any] = {
            "agent_type": agent_type,
            "input": query.strip(),
            "profile": profile,
        }
        if isinstance(expiry_seconds, int) and expiry_seconds > 0:
            submit_payload["expiry_seconds"] = expiry_seconds
        if llm_bindings:
            submit_payload["llm_bindings"] = llm_bindings
        if data_sources:
            submit_payload["data_sources"] = data_sources
        if runtime_config_overrides:
            submit_payload["config_overrides"] = runtime_config_overrides
        if advanced_yaml_overrides:
            submit_payload["advanced_yaml_overrides"] = advanced_yaml_overrides

        debug_artifact_paths: dict[str, str] = {}
        if bool(extra.get("preserve_debug_artifacts", True)):
            artifact_path = _write_aiq_debug_artifact(
                user_uuid=user_uuid,
                extra=extra,
                filename=f"{task_id}_aiq_submit_payload.json",
                payload=submit_payload,
            )
            if artifact_path:
                debug_artifact_paths["submit_payload"] = artifact_path
            artifact_path = _write_aiq_debug_artifact(
                user_uuid=user_uuid,
                extra=extra,
                filename=f"{task_id}_aiq_effective_config.json",
                payload={
                    "profile": profile,
                    "agent_type": agent_type,
                    "data_sources": data_sources,
                    "llm_bindings": llm_bindings,
                    "config_overrides": runtime_config_overrides,
                    "advanced_yaml_overrides": advanced_yaml_overrides,
                    "openai_only": openai_only_run,
                    "openai_parameter_adjustments": openai_parameter_adjustments,
                    "nvidia_parameter_adjustments": nvidia_parameter_adjustments,
                    "pruned_nvidia_llm_overrides": pruned_nvidia_llm_overrides,
                },
            )
            if artifact_path:
                debug_artifact_paths["effective_config"] = artifact_path

        stream_stop_event = threading.Event()
        stream_task: Optional[asyncio.Task] = None
        stream_terminal_failure: dict[str, Any] = {}
        aiq_stream_summary: Optional[dict[str, Any]] = None
        aiq_usage_totals: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }

        await self._emit_progress(progress_callback, "queued", 0.05, "Submitting AI-Q job")
        await self._write_aiq_log(
            log_writer,
            level="INFO",
            event_type="aiq_submit_start",
            message="Submitting AI-Q job",
            payload={
                "task_id": task_id,
                "profile": profile,
                "agent_type": agent_type,
                "data_sources": data_sources,
                "llm_bindings": llm_bindings,
                "timeout_seconds": timeout_seconds,
                "job_expiry_seconds": expiry_seconds,
                "openai_parameter_adjustments": openai_parameter_adjustments,
                "nvidia_parameter_adjustments": nvidia_parameter_adjustments,
                "pruned_nvidia_llm_overrides": pruned_nvidia_llm_overrides,
            },
        )
        if openai_parameter_adjustments:
            adjustment_level, adjustment_summary = _aiq_adjustment_rollup(openai_parameter_adjustments)
            await self._write_aiq_log(
                log_writer,
                level=adjustment_level,
                event_type="aiq_openai_parameter_adjustments",
                message=f"AI-Q OpenAI parameter adjustments: {adjustment_summary}",
                payload={
                    "task_id": task_id,
                    "adjustments": openai_parameter_adjustments,
                },
            )
        if pruned_nvidia_llm_overrides:
            await self._write_aiq_log(
                log_writer,
                level="INFO",
                event_type="aiq_nvidia_config_pruned",
                message=(
                    "AI-Q removed unbound NVIDIA base LLM overrides: "
                    f"{len(pruned_nvidia_llm_overrides)} "
                    f"({', '.join(pruned_nvidia_llm_overrides[:8])})"
                ),
                payload={
                    "task_id": task_id,
                    "llm_keys": pruned_nvidia_llm_overrides,
                    "reason": "The selected NVIDIA report model provides bound native NIM role configs.",
                },
            )
        if nvidia_parameter_adjustments:
            adjustment_level, adjustment_summary = _aiq_adjustment_rollup(nvidia_parameter_adjustments)
            await self._write_aiq_log(
                log_writer,
                level=adjustment_level,
                event_type="aiq_nvidia_parameter_adjustments",
                message=f"AI-Q NVIDIA parameter adjustments: {adjustment_summary}",
                payload={
                    "task_id": task_id,
                    "adjustments": nvidia_parameter_adjustments,
                },
            )
        try:
            submit_response = await self._request(
                "POST",
                "/v1/jobs/async/submit",
                json_body=submit_payload,
                timeout=30.0,
            )
            job_id = str(submit_response.get("job_id") or "")
            if not job_id:
                raise ValueError("AI-Q submit response did not include a job_id")
            self._task_to_job[task_id] = job_id
            aiq_stream_summary = _new_aiq_stream_summary(task_id=task_id, job_id=job_id)
            await self._write_aiq_log(
                log_writer,
                level="INFO",
                event_type="aiq_submit_complete",
                message=f"AI-Q job submitted: {job_id}",
                payload={
                    "task_id": task_id,
                    "job_id": job_id,
                    "submit_response": submit_response,
                    "soft_timeout_seconds": timeout_seconds,
                    "job_expiry_seconds": expiry_seconds,
                },
            )
            if log_writer is not None:
                stream_task = asyncio.create_task(
                    self._capture_aiq_stream(
                        job_id=job_id,
                        task_id=task_id,
                        log_writer=log_writer,
                        stop_event=stream_stop_event,
                        terminal_failures=stream_terminal_failure,
                        usage_totals=aiq_usage_totals,
                        stream_summary=aiq_stream_summary,
                    )
                )
            if bool(extra.get("preserve_debug_artifacts", True)):
                artifact_path = _write_aiq_debug_artifact(
                    user_uuid=user_uuid,
                    extra=extra,
                    filename=f"{task_id}_aiq_job.json",
                    payload={
                        "job_id": job_id,
                        "task_id": task_id,
                        "profile": profile,
                        "submitted_at": datetime.utcnow().isoformat(),
                        "submit_response": submit_response,
                    },
                )
                if artifact_path:
                    debug_artifact_paths["job"] = artifact_path
            await self._emit_progress(
                progress_callback,
                "submitted",
                0.15,
                (
                    f"AI-Q job submitted: {job_id}; soft_timeout={timeout_seconds}s, "
                    f"job_expiry={expiry_seconds}s"
                ),
            )

            loop = asyncio.get_running_loop()
            soft_deadline = loop.time() + timeout_seconds
            hard_deadline = loop.time() + max(timeout_seconds, expiry_seconds) + AIQ_HARD_WAIT_GRACE_SECONDS
            status_payload: dict[str, Any] = submit_response
            timeout_warning_count = 0
            while True:
                if task_id in self._cancel_requested:
                    await self.cancel(task_id)
                    raise RuntimeError("AI-Q job cancelled")

                status_payload = await self._request(
                    "GET",
                    f"/v1/jobs/async/job/{job_id}",
                    timeout=30.0,
                )
                raw_status = str(status_payload.get("status") or "").lower()
                if stream_terminal_failure:
                    raise RuntimeError(
                        stream_terminal_failure.get("message")
                        or "AI-Q stream reported a terminal failure"
                    )
                if raw_status in {"completed", "succeeded", "success"}:
                    break
                if raw_status in {"failed", "failure", "error"}:
                    raise RuntimeError(
                        status_payload.get("error")
                        or status_payload.get("message")
                        or "AI-Q job failed"
                    )
                if raw_status in {"cancelled", "canceled", "interrupted"}:
                    raise RuntimeError(
                        status_payload.get("reason")
                        or status_payload.get("message")
                        or "AI-Q job cancelled"
                    )

                progress = 0.45 if raw_status == "running" else 0.2
                message = f"AI-Q job {raw_status or 'submitted'}"
                now = loop.time()
                if now > hard_deadline:
                    raise TimeoutError(
                        "AI-Q job exceeded APICostX hard wait limit "
                        f"({max(timeout_seconds, expiry_seconds) + AIQ_HARD_WAIT_GRACE_SECONDS}s); "
                        "job was not cancelled automatically"
                    )
                if now > soft_deadline:
                    timeout_warning_count += 1
                    message = (
                        f"{message}; still running after configured soft timeout "
                        f"({timeout_seconds}s), continuing without cancelling"
                    )
                    logger.warning(
                        "AI-Q job still running after soft timeout: task=%s job=%s "
                        "status=%s soft_timeout=%ss job_expiry=%ss warning_count=%s",
                        task_id,
                        job_id,
                        raw_status or "submitted",
                        timeout_seconds,
                        expiry_seconds,
                        timeout_warning_count,
                    )
                    await self._write_aiq_log(
                        log_writer,
                        level="WARNING",
                        event_type="aiq_soft_timeout",
                        message=message,
                        payload={
                            "task_id": task_id,
                            "job_id": job_id,
                            "status": raw_status or "submitted",
                            "soft_timeout_seconds": timeout_seconds,
                            "job_expiry_seconds": expiry_seconds,
                            "warning_count": timeout_warning_count,
                        },
                    )
                    soft_deadline = now + timeout_seconds
                await self._emit_progress(progress_callback, raw_status or "submitted", progress, message)
                await asyncio.sleep(self.poll_interval_seconds)

            await self._emit_progress(progress_callback, "fetching_report", 0.9, "Fetching AI-Q report")
            await self._write_aiq_log(
                log_writer,
                level="INFO",
                event_type="aiq_fetch_report",
                message=f"Fetching AI-Q report: job_id={job_id}",
                payload={"task_id": task_id, "job_id": job_id, "status_payload": status_payload},
            )
            report_payload = await self._request(
                "GET",
                f"/v1/jobs/async/job/{job_id}/report",
                timeout=30.0,
            )
            content = str(report_payload.get("report") or "")
            if report_payload.get("has_report") is False:
                raise RuntimeError("AI-Q completed but report endpoint says no report was produced")
            if not content.strip():
                raise RuntimeError("AI-Q completed but did not return a report")

            completed_at = datetime.utcnow()
            await self._emit_progress(progress_callback, "completed", 1.0, "AI-Q report ready")
            if stream_task is not None and not stream_task.done():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stream_task, timeout=5.0)
            await self._write_aiq_log(
                log_writer,
                level="INFO",
                event_type="aiq_report_ready",
                message=f"AI-Q report ready: job_id={job_id}",
                payload={
                    "task_id": task_id,
                    "job_id": job_id,
                    "has_report": report_payload.get("has_report"),
                    "report_chars": len(content),
                },
            )
            return GenerationResult(
                generator=GeneratorType.AIQ,
                task_id=task_id,
                content=content,
                content_type="markdown",
                model=config.model,
                provider=config.provider,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=(completed_at - started_at).total_seconds(),
                input_tokens=int(aiq_usage_totals.get("input_tokens", 0) or 0),
                output_tokens=int(aiq_usage_totals.get("output_tokens", 0) or 0),
                total_tokens=int(aiq_usage_totals.get("total_tokens", 0) or 0),
                metadata={
                    "aiq_job_id": job_id,
                    "aiq_status": status_payload.get("status"),
                    "aiq_agent_type": agent_type,
                    "aiq_profile": profile,
                    "aiq_big_model": big_model_key,
                    "aiq_small_model": small_model,
                    "aiq_llm_bindings": llm_bindings,
                    "aiq_data_sources": data_sources,
                    "aiq_timeout_seconds": timeout_seconds,
                    "aiq_job_expiry_seconds": expiry_seconds,
                    "aiq_config_overrides": _sanitize_config_overrides(runtime_config_overrides),
                    "aiq_advanced_yaml_overrides": advanced_yaml_overrides,
                    "aiq_debug_artifacts": debug_artifact_paths,
                    "aiq_openai_parameter_adjustments": openai_parameter_adjustments,
                    "aiq_nvidia_parameter_adjustments": nvidia_parameter_adjustments,
                    "aiq_pruned_nvidia_llm_overrides": pruned_nvidia_llm_overrides,
                    "aiq_openai_only": openai_only_run,
                    "aiq_web_only": bool(extra.get("web_only", True)),
                    "aiq_preserve_debug_artifacts": bool(extra.get("preserve_debug_artifacts", True)),
                    "aiq_has_report": bool(report_payload.get("has_report")),
                    "aiq_stream_summary": _aiq_stream_summary_snapshot(aiq_stream_summary),
                },
            )
        except Exception as exc:
            await self._write_aiq_log(
                log_writer,
                level="ERROR",
                event_type="aiq_adapter_error",
                message=f"AI-Q adapter failed: {type(exc).__name__}",
                payload={
                    "task_id": task_id,
                    "job_id": self._task_to_job.get(task_id),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "stream_terminal_failure": stream_terminal_failure,
                    "aiq_stream_summary": _aiq_stream_summary_snapshot(aiq_stream_summary),
                },
            )
            raise
        finally:
            stream_stop_event.set()
            if stream_task is not None and not stream_task.done():
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
            self._task_to_job.pop(task_id, None)
            self._cancel_requested.discard(task_id)
