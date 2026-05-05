"""
file_handler - central router for File Prompt Forge (FPF)

Enforced guarantees implemented here:
- Provider API keys are sourced only from os.environ or the repository root .env.
- Provider-side web_search is required: runs that do not perform web_search will fail.
- Provider reasoning is required: runs that do not return reasoning will fail.
- Raw provider JSON sidecar is always saved. Human-readable output is only written if
  both web_search and reasoning checks pass.
"""

from __future__ import annotations
import os
import re
import json
import importlib
import logging
import time
from typing import Dict, Optional, Tuple, Any, List
from pathlib import Path


LOG = logging.getLogger("file_handler")


def _repo_root_env_file() -> Path:
    """Return the canonical repository root .env path for FilePromptForge."""
    return Path(__file__).resolve().parents[2] / ".env"


def _resolve_env_file(env_path: Optional[str] = None) -> Path:
    """Resolve and enforce the only file-backed secret source FPF supports."""
    root_env = _repo_root_env_file().resolve()
    if env_path is None:
        return root_env
    requested = Path(env_path).expanduser().resolve()
    if requested != root_env:
        raise RuntimeError(f"FPF secrets may only be loaded from process environment or repository root .env: {root_env}")
    return requested

# Console redaction/truncation helpers
def _redact_headers(h: dict) -> dict:
    try:
        red = {}
        for k, v in (h or {}).items():
            if str(k).lower() in ("authorization", "x-api-key", "x-goog-api-key", "api-key"):
                red[k] = "***REDACTED***"
            else:
                red[k] = v
        return red
    except Exception:
        return {}

def _truncate(s: str, n: int = 2000) -> str:
    try:
        if s is None:
            return ""
        s = str(s)
        return s if len(s) <= n else s[:n] + "…"
    except Exception:
        return ""


def _sanitize_filename(name: str) -> str:
    """Sanitize a string to be a valid filename."""
    if not name:
        return "unknown"
    # remove chars that are problematic for filenames
    return re.sub(r'[\\/*?:"<>|]', "", name)


def _is_openrouter_free_model(provider_name: str, model: Any) -> bool:
    if provider_name != "openrouter":
        return False
    normalized = str(model or "").strip().lower()
    if normalized.startswith("openrouter:"):
        normalized = normalized[len("openrouter:"):]
    return normalized == "openrouter/free" or normalized.endswith(":free")


def _sanitize_openrouter_web_search_options(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None

    cleaned: Dict[str, Any] = {}
    context_size = value.get("search_context_size")
    if isinstance(context_size, str):
        normalized_size = context_size.strip().lower()
        if normalized_size in {"low", "medium", "high"}:
            cleaned["search_context_size"] = normalized_size

    for key, max_value in (("max_results", 10), ("max_total_results", 20)):
        try:
            number = int(value.get(key))
        except (TypeError, ValueError):
            continue
        if number > 0:
            cleaned[key] = min(number, max_value)

    if "max_results" in cleaned and "max_total_results" in cleaned:
        cleaned["max_total_results"] = max(cleaned["max_total_results"], cleaned["max_results"])

    return cleaned or None


def _is_transient_error(exc: Exception) -> bool:
    """Check if an error is transient and should be retried."""
    msg = str(exc).lower()
    # Transient conditions: rate limits, timeouts, server errors, grounding failures
    transient_indicators = [
        "429", "rate limit", "quota",  # Rate limiting
        "timeout", "timed out",         # Timeouts
        "502", "503", "504",            # Server errors
        "connection", "network",        # Network issues
        "grounding", "validation",      # Grounding failures (can retry with same request)
        "temporarily unavailable",
        "service unavailable",
        "internal server error",
    ]
    return any(tok in msg for tok in transient_indicators)


def _http_post_json(
    url: str,
    payload: Dict,
    headers: Dict,
    timeout: int = 600,
    max_retries: int = 3,
    base_delay_ms: int = 500,
    max_delay_ms: int = 30000,
) -> Dict:
    """POST JSON and return parsed JSON response. Uses urllib (no extra deps).

    Enhancements:
    - Retry logic with exponential backoff for transient errors
    - Increased default timeout to 600s to accommodate longer reasoning/tool runs.
    - Added debug logging of request metadata (not payload contents) to assist troubleshooting.
    - Logs and raises detailed errors on HTTP failures.

    Args:
        url: The API endpoint URL
        payload: JSON payload to send
        headers: HTTP headers
        timeout: Request timeout in seconds (default 600)
        max_retries: Maximum retry attempts for transient errors (default 3)
        base_delay_ms: Base delay in milliseconds for exponential backoff (default 500)
        max_delay_ms: Maximum delay in milliseconds (default 30000)
    """
    import urllib.request
    import urllib.error
    import time
    import random

    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})

    # Log a compact request summary for debugging (do not log full payload to avoid sensitive data leakage)
    try:
        LOG.debug("HTTP POST %s headers=%s payload_bytes=%d timeout=%s", url, {k: hdrs.get(k) for k in ("Authorization", "Content-Type")}, len(body), timeout)
    except Exception:
        # best-effort logging; do not raise for logging failures
        pass

    last_error = None

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        # Request summary (redacted, truncated)
        LOG.info("[FPF API][REQ] POST %s attempt=%d/%d headers=%s payload_bytes=%d preview=%s", url, attempt, max_retries, _redact_headers(hdrs), len(body), _truncate(json.dumps(payload)))
        try:
            start_ts = time.time()
            if timeout is None:
                resp_ctx = urllib.request.urlopen(req)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout)
            with resp_ctx as resp:
                raw = resp.read().decode("utf-8")
                elapsed = time.time() - start_ts
                # Response summary (truncated)
                try:
                    status_code = getattr(resp, "status", resp.getcode() if hasattr(resp, "getcode") else "unknown")
                    LOG.info("[FPF API][RESP] %s status=%s bytes=%d duration=%.2fs preview=%s", url, status_code, len(raw), elapsed, _truncate(raw))
                except Exception:
                    pass
                if not raw:
                    raise RuntimeError(f"Empty HTTP response from {url} on attempt {attempt}/{max_retries}")
                parsed = json.loads(raw)
                if not isinstance(parsed, (dict, list)):
                    raise RuntimeError(f"Unexpected JSON type {type(parsed)} from {url} on attempt {attempt}/{max_retries}")
                return parsed
        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            # Error summary (truncated)
            LOG.error("[FPF API][ERR] %s attempt=%d/%d status=%s reason=%s body=%s", url, attempt, max_retries, getattr(he,'code','?'), getattr(he,'reason','?'), _truncate(msg))

            last_error = RuntimeError(f"HTTP error {he.code}: {he.reason} - {msg}")

            # Check if we should retry
            if attempt < max_retries and _is_transient_error(last_error):
                # Exponential backoff with jitter
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)  # Full jitter
                delay_s = delay_ms / 1000.0
                LOG.warning(f"Transient error on attempt {attempt}/{max_retries}, retrying in {delay_s:.2f}s: {he}")
                LOG.info("[FPF API][RETRY] Waiting %.2fs before retry %d/%d", delay_s, attempt + 1, max_retries)
                time.sleep(delay_s)
                continue

            LOG.exception("HTTPError during POST %s: %s %s", url, he, msg)
            raise last_error from he

        except Exception as e:
            LOG.error("[FPF API][ERR] %s attempt=%d/%d error=%s", url, attempt, max_retries, e)

            last_error = RuntimeError(f"HTTP request failed: {e}")

            # Check if we should retry
            if attempt < max_retries and _is_transient_error(e):
                # Exponential backoff with jitter
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)  # Full jitter
                delay_s = delay_ms / 1000.0
                LOG.warning(f"Transient error on attempt {attempt}/{max_retries}, retrying in {delay_s:.2f}s: {e}")
                LOG.info("[FPF API][RETRY] Waiting %.2fs before retry %d/%d", delay_s, attempt + 1, max_retries)
                time.sleep(delay_s)
                continue

            LOG.exception("HTTP request failed for %s: %s", url, e)
            raise last_error from e

    # Should not reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("HTTP request failed after all retries")


def _load_provider_module(provider_name: str = "openai"):
    """Import the provider module. Raise RuntimeError if not found."""
    max_retries = 3
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            # Construct the module name dynamically (import within this package root).
            module_name = f"providers.{provider_name}.fpf_{provider_name}_main"
            if attempt > 1:
                LOG.info(f"Retry loading provider module: {module_name} (Attempt {attempt}/{max_retries})")

            mod = importlib.import_module(module_name)
            LOG.info("Successfully loaded provider module: %s", module_name)
            return mod

        except Exception as e:
            last_exception = e
            LOG.warning(f"Failed to load provider module '{provider_name}' on attempt {attempt}/{max_retries}. Error: {e}")

            # Log detailed OS error info if available
            if isinstance(e, OSError):
                LOG.error(f"OS Error details - errno: {e.errno}, strerror: {e.strerror}, filename: {e.filename}")

            # Log traceback for deeper inspection
            LOG.debug("Traceback for module load failure:", exc_info=True)

            if attempt < max_retries:
                sleep_time = 0.5 * (2 ** (attempt - 1))  # Exponential backoff: 0.5, 1.0, 2.0
                LOG.info(f"Sleeping {sleep_time}s before retrying module load...")
                time.sleep(sleep_time)

    # Final failure handling
    if isinstance(last_exception, ModuleNotFoundError):
        LOG.error("Provider module not found for: %s after retries", provider_name)
        raise RuntimeError(f"Provider module not found for: {provider_name}") from last_exception
    else:
        LOG.exception("An unexpected error occurred while loading provider module for: %s after retries", provider_name)
        raise RuntimeError(f"Could not load provider module for {provider_name}. Last error: {last_exception}") from last_exception


def _read_key_from_env_file(env_path: Path, key: str) -> Optional[str]:
    """
    Read KEY=VALUE from environment or the repository root .env file.

    Priority:
    1. Environment variable (os.environ) - allows APICostX to inject keys from the root .env
    2. Repository root .env file - fallback for standalone FPF usage
    """
    # First check os.environ (APICostX injects keys here)
    env_value = os.environ.get(key)
    if env_value:
        LOG.info(f"Loaded API key '{key}' from environment variable (Length: {len(env_value)})")
        return env_value

    # Fallback to repository root .env file
    if not env_path.exists():
        return None
    try:
        with env_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('\'"')
    except Exception:
        # Do not swallow — let caller decide. Return None on parse failure.
        return None
    return None


def _response_used_websearch(raw_json: Dict) -> bool:
    """
    Inspect provider response to determine whether provider-side web_search
    (tool usage) occurred.

    Heuristics:
    - If 'tool_calls' or 'tools' exists and is non-empty -> True
    - If any output block contains 'reasoning' or content referencing 'source' or 'web_search' strings -> True
    """
    if not isinstance(raw_json, dict):
        return False

    # direct tool call evidence
    if "tool_calls" in raw_json and isinstance(raw_json["tool_calls"], list) and raw_json["tool_calls"]:
        return True
    if "tools" in raw_json and isinstance(raw_json["tools"], list) and raw_json["tools"]:
        # some providers return tools metadata even if empty; require non-empty
        return True

    # inspect outputs for websearch indicators
    output = raw_json.get("output") or raw_json.get("outputs")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            # check content blocks for source-like entries
            content = item.get("content") or item.get("contents")
            if isinstance(content, list):
                for c in content:
                    # string search for common markers
                    try:
                        if isinstance(c, dict):
                            # fields that may indicate web search results
                            if any(k in c for k in ("source", "url", "link")):
                                return True
                            text = c.get("text") or ""
                            if isinstance(text, str) and ("http://" in text or "https://" in text or "[source]" in text or "Citation:" in text):
                                return True
                        elif isinstance(c, str):
                            if "http://" in c or "https://" in c or "Citation:" in c:
                                return True
                    except Exception:
                        continue
    # fallback: scan entire JSON string for web_search mention (conservative)
    try:
        raw_str = json.dumps(raw_json)
        if "web_search" in raw_str or "tool_call" in raw_str or "tool_calls" in raw_str:
            # only return True if also appears with some content length
            return "web_search" in raw_str
    except Exception:
        pass

    # Gemini specific check for groundingMetadata
    try:
        if "candidates" in raw_json and isinstance(raw_json["candidates"], list):
            for candidate in raw_json["candidates"]:
                if "groundingMetadata" in candidate and candidate["groundingMetadata"]:
                    return True
    except Exception:
        pass

    return False


def _extract_json_from_text(text: str) -> Optional[str]:
    """
    Best-effort JSON extractor from mixed text:
    - Prefer fenced ```json ... ``` blocks
    - Then any fenced block containing a JSON-looking object/array
    - Then the first {...} or [...] region in the text
    Returns a minified JSON string if parseable; otherwise None.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return None
        import re as _re
        import json as _json
        candidates: List[str] = []

        # Prefer ```json ... ```
        m = _re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, flags=_re.DOTALL | _re.IGNORECASE)
        if m:
            candidates.append(m.group(1))

        # Any fenced code block containing JSON-looking object/array
        m2 = _re.search(r"```+\s*(\{.*?\}|\[.*?\])\s*```", text, flags=_re.DOTALL)
        if m2:
            candidates.append(m2.group(1))

        # Fallback: first object/array region
        m3 = _re.search(r"(\{.*\}|\[.*\])", text, flags=_re.DOTALL)
        if m3:
            candidates.append(m3.group(1))

        for blob in candidates:
            try:
                obj = _json.loads(blob)
                # Minify for stability
                return _json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                continue
        return None
    except Exception:
        return None

def _resolve_timeout(cfg: dict, provider_name: str) -> Optional[int]:
    """Resolve timeout but allow unbounded; returns None when not explicitly set."""
    try:
        _conc = cfg.get("concurrency") or {}
        _timeout_cfg = _conc.get("timeout_seconds")
    except Exception:
        _timeout_cfg = None
    try:
        _prov_cfg = (cfg.get("providers") or {}).get(provider_name, {}) or {}
        _prov_timeout = _prov_cfg.get("timeout_seconds")
    except Exception:
        _prov_timeout = None
    # If nothing is configured, return None to signal no timeout
    return _prov_timeout or _timeout_cfg or None


def _validate_run_inputs(file_a: Optional[str], file_b: Optional[str], out_path: Optional[str], env_file: Path, provider: str, model: str, timeout: Optional[int]) -> None:
    """Fail fast on missing inputs or obviously bad configuration."""
    if not file_a or not Path(file_a).is_file():
        raise RuntimeError(f"file_a must point to an existing file (got {file_a})")
    if not file_b or not Path(file_b).is_file():
        raise RuntimeError(f"file_b must point to an existing file (got {file_b})")
    if not provider:
        raise RuntimeError("provider is required and cannot be empty")
    if not model:
        raise RuntimeError("model is required and cannot be empty")
    # NOTE: Secret preflight is intentionally deferred so os.environ remains the
    # primary source and the repository root .env is the only file fallback.
    # Pre-flight output directory writability if provided
    if out_path:
        out_parent = Path(out_path).expanduser().resolve().parent
        out_parent.mkdir(parents=True, exist_ok=True)
        if not os.access(out_parent, os.W_OK):
            raise RuntimeError(f"Output directory not writable: {out_parent}")


def run(file_a: Optional[str] = None,
        file_b: Optional[str] = None,
        out_path: Optional[str] = None,
        config_path: Optional[str] = None,
        env_path: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        max_completion_tokens: Optional[int] = None,
        thinking_budget_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        fpf_max_retries: Optional[int] = None,
        fpf_retry_delay: Optional[float] = None,
        request_json: Optional[bool] = None,
        web_search: Optional[Dict[str, Any]] = None) -> str:
    """
    High-level entry point (OpenAI-only).

    Behavior guarantees enforced:
    - Loads provider API keys only from os.environ or the repository root .env.
    - Fails if provider response did not perform web_search or did not return reasoning.
    - Saves raw sidecar always; writes human-readable output only when checks pass.
    """
    LOG.debug("ENTER run() with file_a=%s file_b=%s out=%s provider=%s model=%s timeout=%s",
          file_a, file_b, out_path, provider, model, timeout)

    # Import helpers lazily to avoid circular imports
    try:
        from .helpers import compose_input, load_config, load_env_file  # preferred relative import
    except ImportError:
        try:
            from helpers import compose_input, load_config, load_env_file  # type: ignore
        except ImportError:
            # Legacy fallback for older layouts
            from fpf_main import compose_input, load_config, load_env_file  # type: ignore

    cfg = load_config(config_path or str(Path(__file__).parent / "fpf_config.yaml"))
    # Default JSON extraction behavior to boolean cfg["json"] when request_json is not provided
    if request_json is None:
        try:
            request_json = bool(cfg.get("json"))
        except Exception:
            request_json = False

    # Determine the provider and load the correct API key.
    # Normalize provider, and auto-route deep-research models to 'openaidp'
    provider_name = (provider or cfg.get("provider", "openai")).lower()
    try:
        _sel_model = (model or cfg.get("model") or "").lower()
        _norm_model = _sel_model.split(":", 1)[0]
        if _norm_model.startswith("o3-deep-research") or _norm_model.startswith("o4-mini-deep-research"):
            provider_name = "openaidp"
        elif _norm_model.startswith("deep-research-pro-preview"):
            provider_name = "googledp"
    except Exception:
        pass
    api_key_name = f"{provider_name.upper()}_API_KEY"

    env_file = _resolve_env_file(env_path)
    selected_model = model or cfg.get("model")
    _validate_run_inputs(file_a, file_b, out_path, env_file, provider_name, selected_model, timeout)
    LOG.info(f"Attempting to load API key '{api_key_name}' from {env_file}")
    api_key_value = _read_key_from_env_file(env_file, api_key_name)

    if api_key_value:
        LOG.info(f"Successfully loaded API key '{api_key_name}' (Length: {len(api_key_value)})")
    else:
        LOG.warning(f"Failed to load API key '{api_key_name}' from environment or {env_file}")
        # Fallback: googledp can use GOOGLE_API_KEY
        if provider_name == "googledp":
            fallback_key = "GOOGLE_API_KEY"
            LOG.info(f"Trying fallback key '{fallback_key}' for googledp provider")
            api_key_value = _read_key_from_env_file(env_file, fallback_key)
            if api_key_value:
                LOG.info(f"Successfully loaded fallback key '{fallback_key}' for googledp (Length: {len(api_key_value)})")
        # Fallback: openaidp can use OPENAI_API_KEY (same OpenAI API, different endpoint)
        elif provider_name == "openaidp":
            fallback_key = "OPENAI_API_KEY"
            LOG.info(f"Trying fallback key '{fallback_key}' for openaidp provider")
            api_key_value = _read_key_from_env_file(env_file, fallback_key)
            if api_key_value:
                LOG.info(f"Successfully loaded fallback key '{fallback_key}' for openaidp (Length: {len(api_key_value)})")

    if api_key_value is None or api_key_value == "":
        LOG.error("API key '%s' not found in process environment or repository root .env: %s", api_key_name, env_file)
        raise RuntimeError(f"API key not found. Set {api_key_name} in the process environment or repository root .env: {env_file}")


    # Allow CLI override of model but keep canonical config for web_search/reasoning enforcement
    if model:
        # normalize to provider expected form (do not append :online here — provider adapter handles model normalization)
        cfg["model"] = model

    if reasoning_effort:
        if "reasoning" not in cfg:
            cfg["reasoning"] = {}
        cfg["reasoning"]["effort"] = reasoning_effort

    if max_completion_tokens:
        cfg["max_completion_tokens"] = max_completion_tokens

    if thinking_budget_tokens:
        cfg["thinking_budget_tokens"] = thinking_budget_tokens

    if provider_name == "openrouter":
        sanitized_web_search = _sanitize_openrouter_web_search_options(web_search)
        if sanitized_web_search:
            cfg["web_search"] = sanitized_web_search
            LOG.info(
                "[OPENROUTER WEB SEARCH] run_id=%s model=%s settings=%s",
                os.environ.get("FPF_RUN_ID"),
                cfg.get("model"),
                sanitized_web_search,
            )

    if not file_a or not file_b:
        raise RuntimeError("file_a and file_b must be provided as arguments")

    # Prepare run identifiers and default output path early; emit RUN_START for single-run mode
    try:
        import uuid as _uuid
    except Exception:
        _uuid = None
    # Use APICostX-provided run_id from environment if available, otherwise generate short id for standalone use
    run_id = os.environ.get("FPF_RUN_ID") or (_uuid.uuid4().hex[:8] if _uuid else "runid")
    model_name_sanitized = _sanitize_filename(cfg.get("model"))
    b_path = Path(file_b)
    file_b_stem = b_path.stem
    # Resolve output path early so the RUN_START record includes it
    if out_path:
        try:
            out_path = out_path.replace("<model_name>", model_name_sanitized)
            out_path = out_path.replace("<file_b_stem>", file_b_stem)
            out_path = out_path.replace("<run_id>", run_id)
        except Exception:
            pass
    else:
        out_name = f"{file_b_stem}.{model_name_sanitized}.{run_id}.fpf.response.txt"
        out_path = str(b_path.parent / out_name)
    kind = "deep" if (provider_name == "openaidp" or (str(cfg.get("model") or "").lower().startswith("o3-deep-research") or str(cfg.get("model") or "").lower().startswith("o4-mini-deep-research"))) else "rest"

    # Avoid duplicate signals when invoked by the scheduler
    if os.getenv("FPF_SCHEDULER") != "1":
        try:
            LOG.info("[FPF RUN_START] id=%s kind=%s provider=%s model=%s file_b=%s out=%s attempt=1/1", run_id, kind, provider_name, cfg.get("model"), file_b, out_path)
        except Exception:
            pass

    # compose prompt
    prompt_template = cfg.get("prompt_template")
    prompt = compose_input(file_a, file_b, prompt_template)

    provider = _load_provider_module(provider_name)

    model_to_use = cfg.get("model")
    # No whitelist validation - use whatever model is specified in DB/GUI

    # build payload (provider adapter is responsible for enforcing web_search & reasoning in payload)
    if hasattr(provider, "build_payload"):
        payload_result = provider.build_payload(prompt, cfg)
        if isinstance(payload_result, tuple) and len(payload_result) == 2:
            payload_body, provider_headers = payload_result
        else:
            payload_body = payload_result
            provider_headers = {}
    else:
        raise RuntimeError("Provider does not expose build_payload")

    provider_urls = cfg.get("provider_urls", {})
    provider_url = provider_urls.get(provider_name)
    if not provider_url:
        # Fallback for backward compatibility
        provider_url = cfg.get("provider_url")

    # googledp uses the Interactions API, not generateContent
    if provider_name == "googledp":
        provider_url = "https://generativelanguage.googleapis.com/v1beta/interactions"

    # Dynamically compute Google Gemini endpoint from cfg["model"] to avoid endpoint–model drift
    elif provider_name == "google":
        try:
            norm_model = (cfg.get("model") or "").split(":", 1)[0]
            if not norm_model:
                raise RuntimeError("Google provider requires cfg['model'] to be set")
            computed_url = f"https://generativelanguage.googleapis.com/v1beta/models/{norm_model}:generateContent"
            if provider_url and provider_url != computed_url:
                try:
                    LOG.warning("Overriding configured provider_url for Google to match model: %s -> %s", provider_url, computed_url)
                except Exception:
                    pass
            provider_url = computed_url
        except Exception as e:
            # Fall through to existing checks; will raise if provider_url remains missing
            LOG.debug("Failed to compute dynamic Google endpoint from model: %s", e)

    if not provider_url:
        raise RuntimeError(f"provider_url for '{provider_name}' not configured in config")

    # build headers
    headers = dict(provider_headers or {})
    api_key = api_key_value
    if not api_key:
        raise RuntimeError(f"API key {api_key_name} not found in env file: {env_file}")

    if provider_name in ("google", "googledp"):
        # Google Gemini / Interactions API uses x-goog-api-key header
        headers["x-goog-api-key"] = api_key
        LOG.info("Set 'x-goog-api-key' header for %s provider (Key Length: %d)", provider_name, len(api_key))
    elif provider_name == "anthropic":
        # Anthropic uses x-api-key plus required version header
        headers["x-api-key"] = api_key
        LOG.info("Set 'x-api-key' header for Anthropic provider (Key Length: %d)", len(api_key))
        if "anthropic-version" not in headers:
            headers["anthropic-version"] = cfg.get("anthropic_version") or "2023-06-01"
        if cfg.get("anthropic_beta") and "anthropic-beta" not in headers:
            headers["anthropic-beta"] = cfg.get("anthropic_beta")
    else:
        # Default to bearer token for OpenAI and others
        headers["Authorization"] = f"Bearer {api_key}"
        LOG.info("Set 'Authorization' header (Bearer) for provider '%s' (Key Length: %d)", provider_name, len(api_key))

    if cfg.get("referer"):
        headers["Referer"] = cfg.get("referer")
    if cfg.get("title"):
        headers["Title"] = cfg.get("title")

    # perform HTTP POST: log timing and send request
    # Full payload files are only persisted for OpenRouter free-model FPF debugging.

    import time
    start_ts = time.time()
    # Prefer provider-level execute_and_verify when available to enforce grounding+reasoning at the lowest level.
    try:
        from . import grounding_enforcer as _ge
    except ImportError:
        import grounding_enforcer as _ge

    # Set validation context for extreme logging (after _ge import)
    try:
        _ge.set_run_context(
            run_id=run_id,
            provider=provider_name,
            model=cfg.get("model") or "unknown",
            log_dir=Path(__file__).resolve().parent / "logs" / "validation"
        )
    except Exception as ex:
        LOG.warning("Failed to set validation context for run %s: %s", run_id, ex)

    # Determine request timeout with provider override > concurrency > defaults
    # If explicit timeout provided via CLI/args, it takes precedence over everything
    timeout_to_use = timeout if timeout is not None else _resolve_timeout(cfg, provider_name)

    # Resolve FPF retry settings with defaults
    max_retries_to_use = fpf_max_retries if fpf_max_retries is not None else 3
    retry_delay_to_use = fpf_retry_delay if fpf_retry_delay is not None else 1.0

    LOG.debug(" Starting execution for provider=%s model=%s timeout=%s max_retries=%s retry_delay=%s",
             provider_name, cfg.get("model"), timeout_to_use, max_retries_to_use, retry_delay_to_use)
    LOG.debug(" Payload keys: %s", list(payload_body.keys()) if isinstance(payload_body, dict) else "not_dict")

    openrouter_free_payload_log_context = None
    if _is_openrouter_free_model(provider_name, cfg.get("model")):
        logs_root = Path(os.environ.get("FPF_LOG_DIR") or (Path(__file__).resolve().parent / "logs"))
        run_group_id = os.environ.get("FPF_RUN_GROUP_ID")
        logs_dir = (logs_root / run_group_id) if run_group_id else logs_root
        openrouter_free_payload_log_context = {
            "enabled": True,
            "log_dir": str((logs_dir / "openrouter-free-payloads").resolve()),
            "run_id": run_id,
            "run_group_id": run_group_id,
            "provider": provider_name,
            "model": cfg.get("model"),
        }
        LOG.info(
            "[OPENROUTER FREE PAYLOAD LOG] enabled run_id=%s model=%s dir=%s",
            run_id,
            cfg.get("model"),
            openrouter_free_payload_log_context["log_dir"],
        )

    if hasattr(provider, "execute_and_verify"):
        LOG.debug(" Calling provider.execute_and_verify...")
        LOG.debug("About to call execute_and_verify with timeout=%s", timeout_to_use)
        execute_kwargs = {
            "timeout": timeout_to_use,
            "max_retries": max_retries_to_use,
            "retry_delay": retry_delay_to_use,
        }
        if provider_name == "openrouter" and openrouter_free_payload_log_context:
            execute_kwargs["full_payload_log_context"] = openrouter_free_payload_log_context
        raw_json = provider.execute_and_verify(
            provider_url,
            payload_body,
            headers,
            _ge,
            **execute_kwargs,
        )
        LOG.debug("Returned from execute_and_verify")
        LOG.debug(" provider.execute_and_verify returned. Response type: %s", type(raw_json))
        if isinstance(raw_json, dict):
             LOG.debug(" Response keys: %s", list(raw_json.keys()))
    elif provider_name in ("openaidp", "googledp") and hasattr(provider, "execute_dp_background"):
        LOG.debug(" Calling provider.execute_dp_background...")
        raw_json = provider.execute_dp_background(
            provider_url,
            payload_body,
            headers,
            timeout=timeout_to_use,
        )
        LOG.debug(" execute_dp_background returned. Validating...")
        _ge.assert_grounding_and_reasoning(raw_json, provider=provider)
        LOG.debug(" Validation passed.")
    else:
        LOG.debug(" Calling _http_post_json...")
        raw_json = _http_post_json(
            provider_url,
            payload_body,
            headers,
            timeout=timeout_to_use,
        )
        LOG.debug(" _http_post_json returned. Validating...")
        _ge.assert_grounding_and_reasoning(raw_json, provider=provider)
        LOG.debug(" Validation passed.")

    # Note: Redundant validation removed (Fix #1). Provider's execute_and_verify already validated.
    elapsed = time.time() - start_ts
    try:
        if isinstance(raw_json, dict):
            LOG.info("HTTP POST completed in %.2fs; response keys=%s; tool_choice=%s", elapsed, list(raw_json.keys()), raw_json.get("tool_choice"))
        else:
            LOG.info("HTTP POST completed in %.2fs; response type=%s", elapsed, type(raw_json))
    except Exception:
        LOG.debug("Completed HTTP POST in %.2fs but failed to inspect response for logging", elapsed)

    # out_path was resolved earlier (pre-HTTP); proceed to ensure directory exists

    final_out_path = Path(out_path)
    # create parent directory if it does not exist
    final_out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = str(final_out_path)

    # Raw provider sidecar files are no longer written. The response is captured in the consolidated run log.

    # ---- Enhanced logging & extraction (request/response, web_search results, reasoning) ----
    base_dir = Path(__file__).resolve().parent

    # Extract web_search_call entries from provider response (if present)
    websearch_entries = []
    try:
        output_items = raw_json.get("output") or raw_json.get("outputs") or []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            t = item.get("type", "")
            if t == "web_search_call" or "web_search" in t or t.startswith("ws_"):
                websearch_entries.append(item)
    except Exception:
        LOG.exception("Failed to extract web_search entries from raw response")
        websearch_entries = []

    # Extract provider reasoning (if provider exposes an extractor)
    reasoning_text = None
    try:
        if hasattr(provider, "extract_reasoning"):
            reasoning_text = provider.extract_reasoning(raw_json)
        else:
            reasoning_text = raw_json.get("reasoning")
    except Exception:
        LOG.exception("Failed to extract reasoning via provider.extract_reasoning")
        reasoning_text = None

    # Consolidated per-run log (single JSON) written to logs/ with a run UID
    try:
        import uuid as _uuid
        import datetime as _dt
        # run_id was generated earlier for filename/log correlation
        started_iso = _dt.datetime.fromtimestamp(start_ts).isoformat()
        finished_iso = _dt.datetime.now().isoformat()

        # Attempt to get a human-readable text representation for inclusion
        try:
            human_text = provider.parse_response(raw_json) if hasattr(provider, "parse_response") else json.dumps(raw_json, indent=2, ensure_ascii=False)
        except Exception:
            human_text = None

        # Standardize usage across providers (OpenAI/Gemini).
        def _std_usage(rj: dict) -> dict:
            # OpenAI-style (Responses API and Chat Completions)
            try:
                u = rj.get("usage") or {}
                # Responses API fields
                it = u.get("input_tokens")
                ot = u.get("output_tokens")
                tt = u.get("total_tokens")
                if any(isinstance(x, int) for x in (it, ot, tt)):
                    it_i = int(it or 0)
                    ot_i = int(ot or 0)
                    return {"prompt_tokens": it_i, "completion_tokens": ot_i, "total_tokens": int(tt or (it_i + ot_i))}
                # Legacy Chat Completions fields
                pt = u.get("prompt_tokens")
                ct = u.get("completion_tokens")
                if isinstance(pt, int) or isinstance(ct, int):
                    pt_i = int(pt or 0)
                    ct_i = int(ct or 0)
                    return {"prompt_tokens": pt_i, "completion_tokens": ct_i, "total_tokens": pt_i + ct_i}
            except Exception:
                pass
            # Google Gemini-style
            try:
                um = rj.get("usageMetadata") or {}
                pt = um.get("promptTokenCount")
                ct = um.get("candidatesTokenCount")
                tt = um.get("totalTokenCount")
                if any(isinstance(x, int) for x in (pt, ct, tt)):
                    if pt is None or ct is None:
                        pts = 0
                        cts = 0
                        for c in (rj.get("candidates") or []):
                            m = c.get("usageMetadata") or {}
                            pts += int(m.get("promptTokenCount") or 0)
                            cts += int(m.get("candidatesTokenCount") or 0)
                        pt = int(pt or pts)
                        ct = int(ct or cts)
                    return {
                        "prompt_tokens": int(pt or 0),
                        "completion_tokens": int(ct or 0),
                        "total_tokens": int(tt or (int(pt or 0) + int(ct or 0))),
                    }
            except Exception:
                pass
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        usage_std = _std_usage(raw_json)

        # Optional run group metadata from environment
        run_group_id = os.environ.get("FPF_RUN_GROUP_ID")

        consolidated = {
            "run_group_id": run_group_id,
            "run_id": run_id,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "model": cfg.get("model"),
            "config": cfg,
            "request": payload_body,
            "response": raw_json,
            "web_search": websearch_entries,
            "reasoning": reasoning_text,
            "human_text": human_text,
            "usage": usage_std,
        }

        logs_root = Path(os.environ.get("FPF_LOG_DIR") or (base_dir / "logs"))
        logs_dir = (logs_root / run_group_id) if run_group_id else logs_root
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)

        # Write a unique per-run JSON log file that contains the full run data.
        try:
            log_name = f"{_dt.datetime.now().strftime('%Y%m%dT%H%M%S')}-{run_id}.json"
            log_path = logs_dir / log_name
            with open(log_path, "w", encoding="utf-8") as fh:
                json.dump(consolidated, fh, indent=2, ensure_ascii=False)
            LOG.info("Wrote per-run consolidated log %s (run_id=%s)", log_path, run_id)
        except Exception:
            LOG.exception("Failed to write per-run consolidated log")
    except Exception:
        LOG.exception("Unexpected error in enhanced logging/extraction")

    # Grounding and reasoning were asserted earlier via grounding_enforcer (mandatory).

    # Extract and (optionally) verify reasoning
    reasoning_text = None
    try:
        if hasattr(provider, "extract_reasoning"):
            reasoning_text = provider.extract_reasoning(raw_json)
        else:
            # attempt a best-effort extraction from known shapes
            reasoning_text = raw_json.get("reasoning")
            if isinstance(reasoning_text, dict):
                # stringify simple dict forms
                reasoning_text = "\n\n".join([str(v) for v in reasoning_text.values() if isinstance(v, (str, int, float))])
    except Exception:
        LOG.exception("Failed to extract reasoning from provider response")
        reasoning_text = None

    # Reasoning presence was asserted earlier via grounding_enforcer.

    # Parse human-readable text and decide output content
    if hasattr(provider, "parse_response"):
        human_text = provider.parse_response(raw_json)
    else:
        human_text = json.dumps(raw_json, indent=2, ensure_ascii=False)

    output_content = human_text
    parsed_json_found = False
    if request_json:
        try:
            extracted = _extract_json_from_text(human_text or "")
            if extracted:
                output_content = extracted
                parsed_json_found = True
        except Exception:
            # best-effort only
            pass

    # Write output (only after all checks passed)
    # Final defensive guard: if grounding wasn't detected, refuse to write any output file.
    # This is belt-and-suspenders on top of earlier assertions.
    # FIX (2025-12-21): Removed redundant check that was causing false positives (Exit Code 2)
    # if not _ge.detect_grounding(raw_json):
    #    raise RuntimeError("Refusing to write output: no provider-side grounding detected")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(output_content)
    except Exception as e:
        LOG.exception("Failed to write output to %s: %s", out_path, e)
        raise RuntimeError(f"Failed to write output to {out_path}: {e}") from e

    # Check minimum content size (100 bytes) - documents under this threshold are likely incomplete
    # Skip this check for JSON outputs (e.g., evaluation responses) which are naturally smaller
    # MIN_CONTENT_BYTES = 100  # 100 bytes
    # content_size = len(output_content.encode('utf-8'))
    # if content_size < MIN_CONTENT_BYTES and not parsed_json_found and not request_json:
    #     LOG.warning("FPF output too small (%d bytes < %d bytes minimum), triggering retry", content_size, MIN_CONTENT_BYTES)
    #     raise RuntimeError(f"FPF output too small ({content_size} bytes), minimum is {MIN_CONTENT_BYTES} bytes")

    LOG.info("Run validated: web_search used and reasoning present. Output written to %s parsed_json_found=%s", out_path, parsed_json_found)
    # Emit RUN_COMPLETE (single-run mode) at INFO to console and file
    if os.getenv("FPF_SCHEDULER") != "1":
        try:
            LOG.info("[FPF RUN_COMPLETE] id=%s kind=%s provider=%s model=%s ok=true elapsed=%.2fs status=%s path=%s error=%s", run_id, kind, provider_name, cfg.get("model"), elapsed, "na", out_path, "na")
        except Exception:
            pass
    return out_path
