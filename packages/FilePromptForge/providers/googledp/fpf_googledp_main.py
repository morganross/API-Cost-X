"""
Google Deep Research provider adapter for FPF.

Uses the Gemini Interactions API (POST /v1beta/interactions) with background
execution and polling — Gemini Deep Research does NOT support generateContent.

Model supported:
- deep-research-pro-preview-12-2025

API docs: https://ai.google.dev/gemini-api/docs/deep-research

Key differences from normal Google provider:
- Endpoint: /v1beta/interactions (not /v1beta/models/{model}:generateContent)
- Uses 'agent' field instead of 'model'
- Requires background=True; returns immediately with job ID
- Must poll GET /v1beta/interactions/{id} until status == "completed"
- Response shape: outputs[-1].text (not candidates[0].content.parts[-1].text)
- No groundingMetadata in response — grounding validation must be skipped
- Tasks take 2-60 minutes (typical: 5-20 min)

Guarantees:
- Grounding and reasoning validation are SKIPPED (provider sets both to False)
  because the Interactions API doesn't surface groundingMetadata even though
  the agent uses Google Search internally.
- parse_response extracts text from outputs array.
- extract_reasoning extracts thought summaries if present.
"""

from __future__ import annotations
from typing import Dict, Tuple, Optional, Any, List
import logging
import sys

LOG = logging.getLogger("fpf_googledp_main")

# ---- Grounding enforcer flags ----
# Deep Research uses Google Search internally but the Interactions API response
# doesn't include groundingMetadata or structured reasoning blocks.
# Setting these to False prevents the grounding enforcer from rejecting every response.
REQUIRES_GROUNDING = False
REQUIRES_REASONING = False


def _normalize_model(model: str) -> str:
    """Strip any suffix after ':' for consistency."""
    if not model:
        return ""
    return model.split(":", 1)[0]


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    """
    Build a request payload for the Gemini Interactions API (Deep Research agent).

    The Interactions API uses a different shape than generateContent:
    - 'agent' instead of 'model'
    - 'input' is a plain string (or structured content list)
    - 'background' must be True for Deep Research
    """
    model_cfg = cfg.get("model")
    if not model_cfg:
        raise RuntimeError("Google Deep Research provider requires 'model' in config")
    agent_name = _normalize_model(model_cfg)

    # When JSON is requested, prepend a strict JSON instruction
    request_json = bool(cfg.get("json")) if cfg.get("json") is not None else False
    if request_json:
        json_instr = (
            "Return only a single valid JSON object. "
            "Do not include any prose or Markdown fences. "
            "Output must be strictly valid JSON."
        )
        final_prompt = f"{json_instr}\n\n{prompt}"
    else:
        final_prompt = prompt

    payload: Dict[str, Any] = {
        "agent": agent_name,
        "input": final_prompt,
        "background": True,
    }

    # No tools, temperature, reasoning, or sampling params — the Interactions API
    # manages all of that internally for the Deep Research agent.

    return payload, None


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    """
    Extract reasoning / thought summaries from a Deep Research Interactions API response.

    The Interactions API may include thought blocks in outputs with type == "thought".
    """
    if not isinstance(raw_json, dict):
        return None

    outputs = raw_json.get("outputs")
    if not isinstance(outputs, list):
        return None

    thought_parts: List[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")

        # Thought blocks have type "thought" and may have a "summary" or "text" field
        if item_type == "thought":
            summary = item.get("summary") or item.get("text")
            if isinstance(summary, str) and summary.strip():
                thought_parts.append(summary.strip())
            # Also check nested content
            content = item.get("content")
            if isinstance(content, dict):
                txt = content.get("text")
                if isinstance(txt, str) and txt.strip():
                    thought_parts.append(txt.strip())

    if thought_parts:
        return "\n\n".join(thought_parts)

    return None


def parse_response(raw_json: Dict) -> str:
    """
    Extract readable text from a completed Deep Research Interactions API response.

    Strategy:
    - Find the last output with type == "text" and return its .text field.
    - If not found, join all text-type outputs.
    - Fallback: render a markdown summary.
    """
    try:
        if not isinstance(raw_json, dict):
            return "No response data received."

        outputs = raw_json.get("outputs")
        if isinstance(outputs, list) and outputs:
            # Collect all text outputs
            text_parts: List[str] = []
            for item in outputs:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                if item_type == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])

            if text_parts:
                # Return the last text output (the final report), not intermediate ones
                return text_parts[-1]

        # Markdown fallback
        status = raw_json.get("status", "unknown")
        agent = raw_json.get("agent", "unknown")
        usage = raw_json.get("usage") or {}

        reasoning_text = extract_reasoning(raw_json)

        lines: List[str] = ["# Deep Research Response"]
        lines.append(f"- Agent: {agent}")
        lines.append(f"- Status: {status}")

        if usage:
            lines.append("## Usage")
            for k, v in usage.items():
                if isinstance(v, (int, float)):
                    lines.append(f"- {k}: {v}")

        if reasoning_text:
            lines.append("## Reasoning")
            lines.append(reasoning_text)

        if len(lines) <= 3:
            return "No text output was returned from Deep Research. See logs for details."
        return "\n".join(lines)

    except Exception as e:
        LOG.exception("Exception while parsing Deep Research response: %s", e)
        raise RuntimeError("Failed to parse Deep Research response") from e


# ---------------------------------------------------------------------------
# Background execution (submit + poll) for Google Deep Research
# ---------------------------------------------------------------------------

def execute_dp_background(
    provider_url: str,
    payload: Dict,
    headers: Dict,
    timeout: Optional[int] = None,
) -> Dict:
    """
    Submit a Deep Research request to the Interactions API with background=True
    and poll until completion. Returns the final response JSON dict.

    The API key is passed as a query parameter (?key=...) since the Interactions
    API accepts that form directly. The key is extracted from the x-goog-api-key
    header set by file_handler.
    """
    import urllib.request
    import urllib.error
    import json as _json
    import time as _time
    from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

    def _redact_headers(h: dict) -> dict:
        try:
            red = {}
            for k, v in (h or {}).items():
                lk = str(k).lower()
                if lk in ("authorization", "x-api-key", "x-goog-api-key", "api-key"):
                    red[k] = "***REDACTED***"
                else:
                    red[k] = v
            return red
        except Exception:
            return {}

    def _truncate(s, n: int = 500) -> str:
        try:
            if s is None:
                return ""
            s = str(s)
            return s if len(s) <= n else s[:n] + "…"
        except Exception:
            return ""

    def _build_url_with_key(base_url: str, api_key: str) -> str:
        """Append ?key=API_KEY to the URL for Gemini Interactions API auth."""
        parsed = urlparse(base_url)
        qs = parse_qs(parsed.query)
        qs["key"] = [api_key]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # Extract API key from headers (file_handler sets x-goog-api-key)
    api_key = headers.get("x-goog-api-key", "")
    if not api_key:
        raise RuntimeError("Google Deep Research: x-goog-api-key header not set")

    # Build URL with key as query parameter (Interactions API style)
    submit_url = _build_url_with_key(provider_url, api_key)

    # Clean headers — don't send x-goog-api-key as header since we're using query param
    submit_headers = {"Content-Type": "application/json"}
    for k, v in (headers or {}).items():
        if k.lower() != "x-goog-api-key":
            submit_headers[k] = v

    # 1) Submit request
    body = dict(payload or {})
    body["background"] = True
    data = _json.dumps(body).encode("utf-8")

    LOG.info(
        "[FPF GoogleDP][REQ] POST %s payload_bytes=%d preview=%s",
        provider_url, len(data), _truncate(_json.dumps(body)),
    )

    req = urllib.request.Request(submit_url, data=data, headers=submit_headers, method="POST")
    try:
        http_timeout = min(60, timeout) if timeout else 60  # Submit should return quickly
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            raw = resp.read().decode("utf-8")
            status_code = getattr(resp, "status", resp.getcode() if hasattr(resp, "getcode") else "unknown")
            LOG.info("[FPF GoogleDP][RESP] status=%s bytes=%d preview=%s", status_code, len(raw), _truncate(raw))
            submit_json = _json.loads(raw)
    except urllib.error.HTTPError as he:
        try:
            msg = he.read().decode("utf-8", errors="ignore")
        except Exception:
            msg = ""
        LOG.error(
            "[FPF GoogleDP][ERR] POST %s status=%s reason=%s body=%s",
            provider_url, getattr(he, "code", "?"), getattr(he, "reason", "?"), _truncate(msg),
        )
        raise RuntimeError(f"Google Deep Research submit failed: HTTP {getattr(he, 'code', '?')}: {_truncate(msg, 200)}") from he
    except Exception as e:
        LOG.error("[FPF GoogleDP][ERR] POST %s error=%s", provider_url, e)
        raise

    # Extract interaction ID
    interaction_id = submit_json.get("id")
    if not interaction_id:
        raise RuntimeError(
            f"Google Deep Research: submit did not return an 'id'. "
            f"Response: {_truncate(_json.dumps(submit_json), 300)}"
        )

    status = submit_json.get("status", "unknown")
    LOG.info("[FPF GoogleDP][SUBMIT] interaction_id=%s status=%s", interaction_id, status)

    # If already completed (unlikely for Deep Research but handle it)
    if status == "completed":
        LOG.info("[FPF GoogleDP][COMPLETE] Immediate completion for id=%s", interaction_id)
        return submit_json

    # 2) Poll for completion
    poll_base = provider_url.rstrip("/") + "/" + interaction_id
    poll_url = _build_url_with_key(poll_base, api_key)

    start_ts = _time.time()
    polling_interval = 15  # seconds between polls
    effective_timeout = timeout if timeout else 3600  # default 1 hour

    poll_count = 0
    while True:
        elapsed = _time.time() - start_ts
        if elapsed >= effective_timeout:
            raise RuntimeError(
                f"Google Deep Research timed out after {int(elapsed)}s "
                f"(id={interaction_id})"
            )

        _time.sleep(polling_interval)
        poll_count += 1

        try:
            get_req = urllib.request.Request(poll_url, headers=submit_headers, method="GET")
            with urllib.request.urlopen(get_req, timeout=polling_interval + 10) as r:
                raw_status = r.read().decode("utf-8")
                status_json = _json.loads(raw_status)
        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            LOG.error(
                "[FPF GoogleDP][POLL ERR] id=%s poll=%d status=%s body=%s",
                interaction_id, poll_count, getattr(he, "code", "?"), _truncate(msg),
            )
            raise RuntimeError(
                f"Google Deep Research poll failed: HTTP {getattr(he, 'code', '?')}: {_truncate(msg, 200)}"
            ) from he
        except Exception as e:
            LOG.error("[FPF GoogleDP][POLL ERR] id=%s poll=%d error=%s", interaction_id, poll_count, e)
            raise

        status = status_json.get("status", "unknown")
        LOG.info(
            "[FPF GoogleDP][POLL] id=%s poll=%d status=%s elapsed=%.1fs",
            interaction_id, poll_count, status, elapsed,
        )

        if status == "completed":
            LOG.info(
                "[FPF GoogleDP][COMPLETE] id=%s elapsed=%.1fs polls=%d",
                interaction_id, elapsed, poll_count,
            )
            return status_json

        if status in ("failed", "cancelled", "canceled"):
            error_info = status_json.get("error", {})
            error_msg = ""
            if isinstance(error_info, dict):
                error_msg = error_info.get("message", "")
            raise RuntimeError(
                f"Google Deep Research task {status} "
                f"(id={interaction_id}): {error_msg}"
            )

        # Continue polling for in_progress, requires_action, etc.


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
    Execute Google Deep Research (background mode) and run grounding/reasoning
    verification. Since REQUIRES_GROUNDING and REQUIRES_REASONING are both False,
    verification will pass through without checking.

    Args:
        max_retries: Not used for Deep Research (single submit + poll).
        retry_delay: Not used for Deep Research.
    """
    raw_json = execute_dp_background(provider_url, payload, headers, timeout=timeout)

    # Verification will effectively no-op because REQUIRES_GROUNDING=False
    # and REQUIRES_REASONING=False, but we call it for consistency.
    # NOTE: Must use sys.modules[__name__] — __import__ with dotted names
    # returns the top-level package, not this module, so the enforcer
    # would not see our REQUIRES_GROUNDING/REQUIRES_REASONING flags.
    verify_helpers.assert_grounding_and_reasoning(raw_json, provider=sys.modules[__name__])

    return raw_json
