"""
Native Perplexity Sonar provider for FPF.

Implements async polling for sonar-deep-research while still accepting the
normal synchronous chat-completions response shape as a fallback.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import random
import sys
import time
import urllib.error
import urllib.request

LOG = logging.getLogger("fpf_perplexity_main")

REQUIRES_GROUNDING = True
REQUIRES_REASONING = True


def _normalize_model(model: str) -> str:
    raw = model or ""
    base = raw.split(":", 1)[0]
    if not base:
        raise RuntimeError("Perplexity provider requires 'model' in config - no fallback defaults allowed")
    return base


def _is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    transient = [
        "429",
        "rate limit",
        "timeout",
        "timed out",
        "502",
        "503",
        "504",
        "connection",
        "network",
        "temporarily unavailable",
        "service unavailable",
        "overloaded",
    ]
    return any(tok in msg for tok in transient)


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    model_cfg = cfg.get("model")
    model_to_use = _normalize_model(model_cfg)

    request_json = bool(cfg.get("json")) if cfg.get("json") is not None else False
    if request_json:
        json_instr = (
            "Return only a single valid JSON object. "
            "Do not include prose or Markdown fences. "
            "Output must be strictly valid JSON."
        )
        final_prompt = f"{json_instr}\n\n{prompt}"
    else:
        final_prompt = prompt

    payload: Dict[str, Any] = {
        "model": model_to_use,
        "messages": [
            {
                "role": "user",
                "content": final_prompt,
            }
        ],
        "stream": False,
    }

    if cfg.get("max_completion_tokens") is not None:
        payload["max_tokens"] = int(cfg["max_completion_tokens"])

    if cfg.get("temperature") is not None:
        payload["temperature"] = float(cfg["temperature"])

    if cfg.get("top_p") is not None:
        payload["top_p"] = float(cfg["top_p"])

    reasoning_effort = (
        cfg.get("reasoning_effort")
        or (cfg.get("reasoning") or {}).get("effort")
        or "high"
    )
    payload["reasoning_effort"] = reasoning_effort

    payload["disable_search"] = False
    payload["return_related_questions"] = False

    web_search_options = {"search_context_size": "high"}
    if isinstance(cfg.get("web_search_options"), dict):
        web_search_options.update(cfg["web_search_options"])
    payload["web_search_options"] = web_search_options

    if cfg.get("language_preference"):
        payload["language_preference"] = cfg["language_preference"]

    return payload, None


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    # Perplexity exposes reasoning usage metadata but not raw chain-of-thought text.
    return None


def parse_response(raw_json: Dict) -> str:
    if not isinstance(raw_json, dict):
        return str(raw_json)

    choices = raw_json.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"].strip())
                    elif isinstance(item, str):
                        parts.append(item.strip())
                if parts:
                    return "\n\n".join([p for p in parts if p])

    try:
        return json.dumps(raw_json, ensure_ascii=False, indent=2)
    except Exception:
        return str(raw_json)


def _http_get_json(url: str, headers: Dict[str, str], timeout: Optional[int]) -> Dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    if timeout is None:
        resp_ctx = urllib.request.urlopen(req)
    else:
        resp_ctx = urllib.request.urlopen(req, timeout=timeout)
    with resp_ctx as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def execute_and_verify(
    provider_url: str,
    payload: Dict,
    headers: Dict,
    verify_helpers,
    timeout: Optional[int] = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> Dict:
    """
    Submit a native Perplexity Sonar request and validate the final response.

    For sonar-deep-research we prefer the async endpoint and poll until the
    wrapper status reaches COMPLETED, then return the inner completion object.
    If the configured endpoint is synchronous, we accept the direct completion.
    """
    body = json.dumps({"request": payload}).encode("utf-8")
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Type", "application/json")

    base_delay_ms = int(retry_delay * 1000)
    max_delay_ms = 30000
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(provider_url, data=body, headers=hdrs, method="POST")
            if timeout is None:
                resp_ctx = urllib.request.urlopen(req)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read().decode("utf-8")
                first_json = json.loads(raw)

            # Sync fallback: direct chat completion shape.
            if isinstance(first_json, dict) and "choices" in first_json and "usage" in first_json:
                verify_helpers.assert_grounding_and_reasoning(first_json, provider=sys.modules[__name__])
                return first_json

            request_id = first_json.get("id")
            status = str(first_json.get("status") or "").upper()

            if not request_id:
                raise RuntimeError("Perplexity async request did not return an id")

            deadline = None if timeout is None else time.time() + timeout
            poll_interval = 3.0
            poll_url = provider_url.rstrip("/") + f"/{request_id}"
            final_json = first_json

            while True:
                if status == "COMPLETED":
                    final_json = first_json if "response" in first_json else final_json
                    break
                if status in {"FAILED", "ERROR", "CANCELLED"}:
                    raise RuntimeError(first_json.get("error_message") or f"Perplexity async request failed with status={status}")
                if deadline is not None and time.time() >= deadline:
                    raise RuntimeError("Perplexity async request did not complete before timeout")

                time.sleep(poll_interval)
                polled = _http_get_json(poll_url, hdrs, timeout=timeout)
                status = str(polled.get("status") or "").upper()
                first_json = polled
                final_json = polled

            response_json = final_json.get("response") if isinstance(final_json, dict) else None
            if not isinstance(response_json, dict):
                raise RuntimeError("Perplexity async request completed without a response payload")

            verify_helpers.assert_grounding_and_reasoning(response_json, provider=sys.modules[__name__])
            return response_json

        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            last_error = RuntimeError(f"HTTP error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}")
        except Exception as exc:
            last_error = exc

        if attempt < max_retries and last_error is not None and _is_transient_error(last_error):
            delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
            delay_ms = random.uniform(0, delay_ms)
            LOG.warning("Transient Perplexity error on attempt %d/%d, retrying in %.2fs: %s", attempt, max_retries, delay_ms / 1000.0, last_error)
            time.sleep(delay_ms / 1000.0)
            continue

        if last_error is not None:
            raise RuntimeError(f"Perplexity request failed: {last_error}") from last_error

    raise RuntimeError("Perplexity request failed after all retries")
