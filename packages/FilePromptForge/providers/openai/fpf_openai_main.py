"""
OpenAI provider adapter for FPF (enforced web_search + reasoning).

This adapter enforces provider-side web_search and a high-level reasoning request
for every outgoing payload. The adapter provides:

- build_payload(prompt: str, cfg: dict) -> (dict, dict|None)
- parse_response(raw: dict) -> str
- extract_reasoning(raw: dict) -> Optional[str]
- validate_model(model_id: str) -> bool

Key guarantees implemented here:
- Web-search tooling (tools=[{"type": "web_search", ...}]) is always attached to the payload
  (for supported OpenAI models). The adapter will ignore any config flag intended to
  disable web_search.
- A reasoning object is always included in the payload (defaults to a "high" reasoning
  level if the config does not supply one).
- parse_response remains responsible for extracting readable text. extract_reasoning
  provides a deterministic way for callers to assert whether reasoning was returned.
"""

from __future__ import annotations
from typing import Dict, Tuple, Optional, Any, List, Callable
import threading
import time

def _normalize_model(model: str) -> str:
    if not model:
        raise RuntimeError("OpenAI provider requires 'model' and will not fallback")
    return model.split(":")[0]


def _translate_sampling(cfg: Dict, model: str) -> Dict:
    out: Dict[str, Any] = {}
    if "max_output_tokens" in cfg and cfg["max_output_tokens"] is not None:
        out["max_output_tokens"] = int(cfg["max_output_tokens"])
    elif "max_completion_tokens" in cfg and cfg["max_completion_tokens"] is not None:
        out["max_output_tokens"] = int(cfg["max_completion_tokens"])
    elif "tokens" in cfg and cfg["tokens"] is not None:
        out["max_output_tokens"] = int(cfg["tokens"])

    if "top_p" in cfg and cfg["top_p"] is not None:
        out["top_p"] = float(cfg["top_p"])


    return out

def _attach_reasoning_for_model(payload: Dict[str, Any], cfg: Dict, model: str) -> None:
    """
    Attach a reasoning configuration to the payload based on the target model's capabilities.

    Strategy (heuristic / explicit mapping):
    - For models known to support `reasoning.effort` (o-series, gpt-5 family, some reasoning models)
      we set: payload["reasoning"] = {"effort": "<level>"} where level is one of low|medium|high.
    - For models that prefer `reasoning.max_tokens` (some adapters/providers), we set:
      payload["reasoning"] = {"max_tokens": <int>}
    - If the model is not known / mapping not available, raise RuntimeError so the caller can
      fail-fast rather than sending unsupported parameters.
    - The cfg may supply a `reasoning` object directly; if present we will use it (but still map/validate).
    """
    # Normalize incoming model string for simple prefix checks
    norm = model.split(":")[0] if model else ""

    # Non-configurable: ignore cfg reasoning; always enforce defaults

    # Default reasoning level selection (can be tuned via cfg.web_search or other fields)
    # Prefer effort when supported.
    effort_level = (cfg.get("reasoning") or {}).get("effort")
    if not effort_level:
        # Map thinking_budget_tokens to effort level if provided
        budget = cfg.get("thinking_budget_tokens")
        if budget is not None:
            if budget < 4000:
                effort_level = "low"
            elif budget > 12000:
                effort_level = "high"
            else:
                effort_level = "medium"
        else:
            effort_level = "medium"

    # Heuristic mapping:
    # - gpt-5* and o1/o3/o4 series generally support `effort`
    # - gpt-4.1/gpt-4o-mini and some older models may not support effort
    # - If the model appears to be GPT-5 or o-series, use `effort`; otherwise prefer max_tokens when possible.
    if norm.startswith("gpt-5") or norm.startswith("o1") or norm.startswith("o3") or norm.startswith("o4"):
        # models that support `effort`
        payload["reasoning"] = {"effort": effort_level}
        return

    # Some providers/models prefer max_tokens-based reasoning (use conservative default)
    if norm.startswith("grok") or norm.startswith("anthropic") or norm.startswith("perplexity"):
        # map to max_tokens (example default 1500)
        payload["reasoning"] = {"max_tokens": 1500}
        return

    # For other gpt families where effort isn't supported (e.g., gpt-4.1), we cannot safely send reasoning
    # parameters without probing provider capabilities. Fail-fast per policy to avoid silent downgrade.
    raise RuntimeError(f"Model '{model}' does not have a known mapping for enforced reasoning parameters.")


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    """
    Build a Responses API payload that enforces web_search and reasoning.

    Returns (payload, optional_provider_headers)
    """
    if "model" not in cfg or not cfg.get("model"):
        raise RuntimeError("OpenAI provider requires 'model' in config - no fallbacks")
    model = cfg["model"]  # No fallback - fail fast
    model_to_use = model.split(":")[0] if ":" in model else model

    payload: Dict[str, Any] = {
        "model": model_to_use,
        "input": [
            {
                "role": "user",
                "content": prompt
            }
        ],
    }

    # Merge sampling / token params
    sampling = _translate_sampling(cfg, model_to_use)
    payload.update(sampling)

    # Enforce provider-side web_search
    web_search_cfg = cfg.get("web_search", {}) or {}
    ws_tool: Dict[str, Any] = {"type": "web_search"}

    if model_to_use.startswith("gpt-5"):
        if "search_context_size" in web_search_cfg:
            ws_tool["search_context_size"] = web_search_cfg["search_context_size"]

    if "user_location" in web_search_cfg:
        ws_tool["user_location"] = web_search_cfg["user_location"]

    payload["tools"] = [ws_tool]
    payload["tool_choice"] = "auto"

    # Enforce reasoning
    _attach_reasoning_for_model(payload, cfg, model_to_use)

    include = cfg.get("include")
    if include:
        payload["include"] = include

    instructions = cfg.get("instructions")
    if instructions:
        payload["instructions"] = instructions

    if cfg.get("json") is True:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "evaluation_result",
                "schema": {
                    "type": "object",
                    "properties": {
                        "evaluations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "criterion": {"type": "string"},
                                    "score": {"type": "integer"},
                                    "reason": {"type": "string"}
                                },
                                "required": ["criterion", "score", "reason"],
                                "additionalProperties": True
                            }
                        },
                        "winner_doc_id": {"type": "string"},
                        "reason": {"type": "string"}
                    },
                    "additionalProperties": True
                },
                "strict": False
            }
        }

    return payload, None


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    """
    Extract reasoning content from a Responses API response object if present.

    Returns a single string containing the reasoning text if found, otherwise None.
    """
    if not isinstance(raw_json, dict):
        return None

    # Top-level reasoning
    reasoning = raw_json.get("reasoning")
    if reasoning:
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
        # if it's a dict, try to stringify relevant fields
        if isinstance(reasoning, dict):
            # attempt to join string fields
            parts: List[str] = []
            for v in reasoning.values():
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            if parts:
                return "\n\n".join(parts)

    # Check outputs list for reasoning-like fields
    output = raw_json.get("output") or raw_json.get("outputs")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            # some items may include a 'reasoning' field
            if "reasoning" in item and isinstance(item["reasoning"], (str, dict)):
                r = item["reasoning"]
                if isinstance(r, str) and r.strip():
                    return r.strip()
                if isinstance(r, dict):
                    parts = []
                    for v in r.values():
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip())
                    if parts:
                        return "\n\n".join(parts)
            # some content blocks may have a type indicating reasoning
            content = item.get("content") or item.get("contents")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict):
                        if c.get("type") in ("reasoning", "explanation") and isinstance(c.get("text"), str):
                            return c.get("text").strip()
    # No reasoning found
    return None


def parse_response(raw_json: Dict) -> str:
    """
    Extract readable text from a Responses API response object.

    This function prioritizes aggregated output_text and falls back to composing
    output parts. It does NOT decide whether reasoning was present (use extract_reasoning).
    """
    try:
        if isinstance(raw_json, dict):
            if "output_text" in raw_json and isinstance(raw_json["output_text"], str):
                return raw_json["output_text"]

            output = raw_json.get("output") or raw_json.get("outputs")
            if isinstance(output, list) and output:
                parts: List[str] = []
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content") or item.get("contents")
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict):
                                if c.get("type") in ("output_text", "text") and isinstance(c.get("text"), str):
                                    parts.append(c.get("text"))
                                elif "text" in c and isinstance(c["text"], str):
                                    parts.append(c["text"])
                            elif isinstance(c, str):
                                parts.append(c)
                    if "text" in item and isinstance(item["text"], str):
                        parts.append(item["text"])
                if parts:
                    return "\n\n".join(parts)

            if "choices" in raw_json and isinstance(raw_json["choices"], list) and raw_json["choices"]:
                ch = raw_json["choices"][0]
                if isinstance(ch, dict):
                    if "message" in ch and isinstance(ch["message"], dict):
                        msg = ch["message"]
                        content = msg.get("content")
                        if isinstance(content, str):
                            return content
                        if isinstance(content, list) and content and isinstance(content[0], str):
                            return content[0]
                    if "text" in ch and isinstance(ch["text"], str):
                        return ch["text"]

        # Fallback to Markdown summary instead of dumping raw JSON
        status = raw_json.get("status") if isinstance(raw_json, dict) else None
        incomplete_reason = ((raw_json.get("incomplete_details") or {}).get("reason")) if isinstance(raw_json, dict) else None
        r_model = raw_json.get("model") if isinstance(raw_json, dict) else None
        u = (raw_json.get("usage") or {}) if isinstance(raw_json, dict) else {}
        it = u.get("input_tokens") or u.get("prompt_tokens")
        ot = u.get("output_tokens") or u.get("completion_tokens")
        tt = u.get("total_tokens")

        # Try to include any available reasoning text
        try:
            reasoning_text = extract_reasoning(raw_json)
        except Exception:
            reasoning_text = None

        lines: List[str] = []
        lines.append("# Response")
        if r_model:
            lines.append(f"- Model: {r_model}")
        if status:
            lines.append(f"- Status: {status}")
        if incomplete_reason:
            lines.append(f"- Incomplete: {incomplete_reason}")

        if any(isinstance(x, int) for x in (it, ot, tt)):
            lines.append("## Usage")
            if isinstance(it, int):
                lines.append(f"- Input tokens: {it}")
            if isinstance(ot, int):
                lines.append(f"- Output tokens: {ot}")
            if isinstance(tt, int):
                lines.append(f"- Total tokens: {tt}")

        if reasoning_text and isinstance(reasoning_text, str) and reasoning_text.strip():
            lines.append("## Reasoning")
            lines.append(reasoning_text.strip())

        # If no content was extractable, emit a minimal stub
        if len(lines) <= 1:
            return "No assistant text was returned. See logs for details."
        return "\n".join(lines)
    except Exception as e:
        import logging, json as _json
        logging.getLogger("fpf_openai_main").exception("Exception while parsing provider response: %s", e)
        try:
            logging.getLogger("fpf_openai_main").debug("Raw provider response: %s", _json.dumps(raw_json, indent=2, ensure_ascii=False))
        except Exception:
            logging.getLogger("fpf_openai_main").debug("Failed to serialize raw provider response for logging")
        raise RuntimeError("Failed to parse provider response") from e

def _is_transient_error(exc: Exception) -> bool:
    """Check if an error is transient and should be retried.

    Note: 'insufficient_quota' is NOT transient - it's a billing issue.
    Only true rate limits (429 with 'rate limit' message) should be retried.
    """
    msg = str(exc).lower()

    # Check for NON-transient errors first (do not retry these)
    permanent_indicators = [
        "insufficient_quota",   # Billing/quota exhausted - won't resolve with retry
        "invalid_api_key",      # Authentication issues
        "invalid_request",      # Bad request format
    ]
    if any(tok in msg for tok in permanent_indicators):
        return False

    # Transient errors that can be retried
    transient_indicators = [
        "429", "rate limit",            # Rate limiting (but not quota exhaustion)
        "timeout", "timed out",         # Timeouts
        "502", "503", "504",            # Server errors
        "connection", "network",        # Network issues
        "temporarily unavailable",
        "service unavailable",
        "internal server error",
    ]
    return any(tok in msg for tok in transient_indicators)


def _start_heartbeat(log, fpf_log: Optional[Callable[[str], None]], label: str, interval: float = 15.0):
    stop_event = threading.Event()

    def _emit(msg: str):
        try:
            log.info(msg)
        except Exception:
            pass
        if fpf_log:
            try:
                fpf_log(msg)
            except Exception:
                pass

    def _beat():
        tick = 0
        while not stop_event.wait(interval):
            tick += 1
            _emit(f"{label} still waiting ({tick * interval:.0f}s elapsed)")

    thread = threading.Thread(target=_beat, name=f"fpf-openai-heartbeat-{int(time.time())}", daemon=True)
    thread.start()
    return stop_event, thread, _emit


def execute_and_verify(provider_url: str, payload: Dict, headers: Optional[Dict], verify_helpers, timeout: Optional[int] = None, max_retries: int = 3, retry_delay: float = 1.0) -> Dict:
    """
    Execute the OpenAI request and verify both grounding and reasoning are present.
    This is a lowest-level enforcement hook that ensures FPF only succeeds when
    the model actually used web search and produced reasoning.

    Includes retry logic with exponential backoff for transient errors (502, 503, 504, etc.)

    Args:
        retry_delay: Base delay in seconds between retry attempts (default 1.0)
    """
    import urllib.request
    import urllib.error
    import json as _json
    import time
    import random
    import logging

    log = logging.getLogger("fpf_openai_main")

    data = _json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    last_error = None
    base_delay_ms = int(retry_delay * 1000)  # Convert retry_delay seconds to milliseconds
    max_delay_ms = 30000

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(provider_url, data=data, headers=hdrs, method="POST")
        heartbeat_label = f"OpenAI attempt {attempt}/{max_retries}"
        hb_stop, hb_thread, hb_emit = _start_heartbeat(log, None, heartbeat_label)
        attempt_start = time.time()
        attempt_status = "inflight"
        try:
            log.debug("OpenAI request attempt %d/%d to %s with timeout=%s", attempt, max_retries, provider_url, timeout)
            log.debug("Payload: %s", _json.dumps(payload, indent=2, ensure_ascii=False))
            if timeout is None:
                resp_ctx = urllib.request.urlopen(req)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read().decode("utf-8")
                elapsed = time.time() - attempt_start
                if not raw:
                    attempt_status = "empty_response"
                    raise RuntimeError(f"Empty HTTP response from OpenAI on attempt {attempt}/{max_retries}")
                raw_json = _json.loads(raw)
                if not isinstance(raw_json, dict):
                    attempt_status = "non_dict"
                    raise RuntimeError(f"Non-dict JSON returned from OpenAI on attempt {attempt}/{max_retries}")
                log.info("OpenAI response attempt %d/%d completed in %.2fs", attempt, max_retries, elapsed)
                attempt_status = "ok"
                verify_helpers.assert_grounding_and_reasoning(raw_json, provider=__import__(__name__))
                return raw_json
        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            attempt_status = f"http_error_{getattr(he, 'code', '?')}"
            last_error = RuntimeError(f"HTTP error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}")
            if attempt < max_retries and _is_transient_error(last_error):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                delay_s = delay_ms / 1000.0
                log.warning("Transient OpenAI error on attempt %d/%d, retrying in %.2fs: %s", attempt, max_retries, delay_s, he)
                time.sleep(delay_s)
                continue
            log.exception("HTTPError during OpenAI POST %s: %s %s", provider_url, he, msg)
            raise last_error from he
        except Exception as e:
            attempt_status = f"error_{type(e).__name__}"
            last_error = RuntimeError(f"OpenAI request failed: {e}")
            if attempt < max_retries and _is_transient_error(e):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                delay_s = delay_ms / 1000.0
                log.warning("Transient OpenAI failure on attempt %d/%d, retrying in %.2fs: %s", attempt, max_retries, delay_s, e)
                time.sleep(delay_s)
                continue
            log.exception("OpenAI request failed during POST to %s: %s", provider_url, e)
            raise last_error from e
        finally:
            hb_stop.set()
            hb_thread.join(timeout=1.0)
            elapsed = time.time() - attempt_start
            msg = f"{heartbeat_label} finished state={attempt_status} elapsed={elapsed:.2f}s"
            try:
                log.info(msg)
            except Exception:
                pass

    raise RuntimeError(f"OpenAI request failed after {max_retries} attempts: {last_error}")
