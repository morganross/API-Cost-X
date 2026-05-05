"""
Anthropic provider adapter for FPF.

Guarantees (non-configurable):
- Server-side web search is always requested via web_search_20250305.
- Thinking is always enabled; only the budget may be tuned.
- Requests fail fast if the model is not whitelisted.
"""

from __future__ import annotations
from typing import Dict, Tuple, Optional, Any, List
import sys
import json
import logging
import random
import time
import urllib.request
import urllib.error

LOG = logging.getLogger("fpf_anthropic_main")

ALLOWED_PREFIXES = ("claude-",)
# NO DEFAULT_MODEL - GUI is the only source of truth
DEFAULT_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = "web_search_20250305"


def _normalize_model(model: str) -> str:
    raw = model or ""
    return raw.split(":", 1)[0]


def _translate_sampling(cfg: Dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    if cfg.get("max_completion_tokens") is not None:
        out["max_tokens"] = int(cfg["max_completion_tokens"])
    elif cfg.get("max_tokens") is not None:
        out["max_tokens"] = int(cfg["max_tokens"])
    else:
        raise RuntimeError("Anthropic requests must set max_tokens; none provided")

    if cfg.get("temperature") is not None:
        out["temperature"] = float(cfg["temperature"])
    if cfg.get("top_p") is not None:
        out["top_p"] = float(cfg["top_p"])
    if cfg.get("top_k") is not None:
        out["top_k"] = int(cfg["top_k"])

    return out


def _build_thinking(cfg: Dict, max_tokens: int) -> Dict[str, Any]:
    budget = None
    try:
        budget = (cfg.get("thinking") or {}).get("budget_tokens")
    except Exception:
        budget = None
    if budget is None:
        try:
            budget = (cfg.get("reasoning") or {}).get("budget_tokens")
        except Exception:
            budget = None
    if budget is None:
        budget = cfg.get("thinking_budget_tokens")

    if budget is None:
        # Derive a budget from the requested max_tokens rather than a fixed placeholder
        derived = max_tokens - 256 if max_tokens and max_tokens > 512 else max_tokens // 2 if max_tokens else 0
        budget = derived

    budget_int = int(budget)

    # Keep thinking budget within the token envelope
    if max_tokens and budget_int >= max_tokens:
        budget_int = max_tokens - 256 if max_tokens > 512 else max_tokens // 2 or 256
    if budget_int < 256:
        budget_int = 256

    return {"type": "enabled", "budget_tokens": budget_int}


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    model_cfg = cfg.get("model")
    if not model_cfg:
        raise RuntimeError("Anthropic provider requires 'model' in config - no fallback defaults allowed")
    model_to_use = _normalize_model(model_cfg)

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
    thinking = _build_thinking(cfg, sampling["max_tokens"])

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": final_prompt}
            ],
        }
    ]

    payload: Dict[str, Any] = {
        "model": model_to_use,
        "messages": messages,
        "max_tokens": sampling["max_tokens"],
        "tools": [
            {
                # Anthropic server-side web search tool requires name="web_search"
                "type": WEB_SEARCH_TOOL,
                "name": "web_search",
            }
        ],
        # Do not force tool_choice when thinking is enabled (API rejects that combo); rely on validation to enforce search usage
        "tool_choice": {"type": "auto"},
        # Hard-enforce thinking
        "thinking": thinking,
    }

    # Optional sampler knobs
    for k in ("temperature", "top_p", "top_k"):
        if k in sampling:
            payload[k] = sampling[k]

    # Allow system prompt passthrough when provided
    if cfg.get("system"):
        payload["system"] = cfg.get("system")

    headers = {
        "anthropic-version": cfg.get("anthropic_version") or DEFAULT_VERSION
    }
    beta = cfg.get("anthropic_beta")
    if beta:
        headers["anthropic-beta"] = beta

    return payload, headers


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
        "api_error",
    ]
    return any(tok in msg for tok in transient)


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    if not isinstance(raw_json, dict):
        return None

    # Explicit reasoning or thinking fields
    thinking = raw_json.get("thinking") or raw_json.get("reasoning")
    if isinstance(thinking, str) and thinking.strip():
        return thinking.strip()

    content = raw_json.get("content")
    if isinstance(content, list):
        collected: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("thinking", "reasoning", "analysis", "redacted_thinking"):
                t = block.get("thinking") or block.get("text") or block.get("content") or block.get("reason")
                if isinstance(t, str) and t.strip():
                    collected.append(t.strip())
            elif btype == "text":
                # Treat early text blocks (before tool results) as possible rationale
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    collected.append(t.strip())
        if collected:
            return "\n\n".join(collected)

    return None


def parse_response(raw_json: Dict) -> str:
    if not isinstance(raw_json, dict):
        return str(raw_json)

    content = raw_json.get("content")
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    text_parts.append(t.strip())
        if text_parts:
            return "\n\n".join(text_parts)

    try:
        return json.dumps(raw_json, indent=2, ensure_ascii=False)
    except Exception:
        return str(raw_json)


def execute_and_verify(provider_url: str, payload: Dict, headers: Optional[Dict], verify_helpers, timeout: Optional[int] = None, max_retries: int = 3, retry_delay: float = 1.0) -> Dict:
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if "anthropic-version" not in hdrs:
        hdrs["anthropic-version"] = DEFAULT_VERSION

    # Use provided retry_delay as base, converted to ms
    base_delay_ms = retry_delay * 1000
    max_delay_ms = max(base_delay_ms * 4, 120000)  # Max 2 minutes or 4x base delay
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(provider_url, data=data, headers=hdrs, method="POST")
        try:
            LOG.debug("Anthropic request attempt %d/%d to %s", attempt, max_retries, provider_url)
            if timeout is None:
                resp_ctx = urllib.request.urlopen(req)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read().decode("utf-8")
                raw_json = json.loads(raw)
                # Use the actual module object so validation can see extract_reasoning
                provider_mod = sys.modules.get(__name__) or __import__(__name__)
                verify_helpers.assert_grounding_and_reasoning(raw_json, provider=provider_mod)
                return raw_json
        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            last_error = RuntimeError(f"HTTP error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}")

            if attempt < max_retries and _is_transient_error(last_error):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                time.sleep(delay_ms / 1000.0)
                continue
            raise last_error from he
        except Exception as e:
            last_error = RuntimeError(f"HTTP request failed: {e}")
            if attempt < max_retries and _is_transient_error(e):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)
                time.sleep(delay_ms / 1000.0)
                continue
            raise last_error from e

    if last_error:
        raise last_error
    raise RuntimeError("HTTP request failed after all retries")


def list_available_models(api_key: str, api_base: str = "https://api.anthropic.com", version: str = DEFAULT_VERSION) -> List[str]:
    url = api_base.rstrip("/") + "/v1/models"
    hdrs = {
        "x-api-key": api_key,
        "anthropic-version": version,
    }
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    return models


if __name__ == "__main__":
    import argparse
    import os

    # --- ARGPARSE DEBUG START ---
    try:
        import sys
        print(f"[FPF ANTHROPIC DEBUG] PID={os.getpid()} sys.argv: {sys.argv}", file=sys.stderr, flush=True)
    except Exception:
        pass
    # ---------------------------------------------

    parser = argparse.ArgumentParser(description="Anthropic provider utilities for FPF")
    parser.add_argument("--list-models", action="store_true", help="List available models using ANTHROPIC_API_KEY")
    parser.add_argument("--api-base", default="https://api.anthropic.com", help="Override API base URL")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Anthropic API version header")

    # --- ARGPARSE TRAP ---
    try:
        args = parser.parse_args()
    except SystemExit as e:
        print(f"[FPF ANTHROPIC DEBUG] PID={os.getpid()} argparse raised SystemExit: {e}", file=sys.stderr, flush=True)
        if str(e) == "2":
            print(f"[FPF ANTHROPIC DEBUG] PID={os.getpid()} EXIT CODE 2 DETECTED FROM ARGPARSE! Missing arguments or invalid flags.", file=sys.stderr, flush=True)
        raise
    except Exception as e:
        print(f"[FPF ANTHROPIC DEBUG] PID={os.getpid()} argparse raised Exception: {e}", file=sys.stderr, flush=True)
        raise
    # --------------------------------------

    if args.list_models:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in environment")
        try:
            models = list_available_models(api_key, api_base=args.api_base, version=args.version)
            print(json.dumps(models, indent=2))
        except Exception as exc:
            raise SystemExit(f"Failed to list models: {exc}")
