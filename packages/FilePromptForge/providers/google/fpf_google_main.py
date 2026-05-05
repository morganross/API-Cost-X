"""
Google Gemini provider adapter for FPF.

This adapter enforces provider-side web_search for every outgoing payload.

- build_payload(prompt: str, cfg: dict) -> (dict, dict|None)
- parse_response(raw: dict) -> str
- extract_reasoning(raw: dict) -> Optional[str]
- validate_model(model_id: str) -> bool
"""

from __future__ import annotations
from typing import Dict, Tuple, Optional, Any, List
import logging
import random
import time
import sys
import os

LOG = logging.getLogger("fpf_google_main")

def _dump_bits(label: str, data: bytes):
    """Log binary payload metadata (size, label). Full hex dumps removed — Phase 6B."""
    LOG.debug("Binary payload [%s]: %d bytes", label, len(data))

def _log_response_details(data: dict):
    """Log detailed structure of the response."""
    try:
        LOG.debug("--- RESPONSE DETAILS ---")
        if not isinstance(data, dict):
            LOG.debug("Response is not a dict: %s", type(data))
            return

        # Usage Metadata
        usage = data.get("usageMetadata", {})
        LOG.debug("Usage: prompt=%s, candidates=%s, total=%s",
              usage.get("promptTokenCount"),
              usage.get("candidatesTokenCount"),
              usage.get("totalTokenCount"))

        # Candidates
        candidates = data.get("candidates", [])
        LOG.debug("Candidate Count: %d", len(candidates))

        for i, cand in enumerate(candidates):
            LOG.debug("Candidate %d:", i)
            LOG.debug("  Finish Reason: %s", cand.get("finishReason"))
            LOG.debug("  Safety Ratings: %s", len(cand.get("safetyRatings", [])))

            # Content
            content = cand.get("content", {})
            parts = content.get("parts", [])
            LOG.debug("  Content Parts: %d", len(parts))
            for j, part in enumerate(parts):
                txt = part.get("text", "")
                LOG.debug("    Part %d Text Length: %d", j, len(txt))
                if len(txt) < 100:
                    LOG.debug("    Part %d Text Preview: %s", j, txt)

            # Grounding
            gm = cand.get("groundingMetadata", {})
            if gm:
                LOG.debug("  Grounding Metadata Present")
                LOG.debug("    Web Search Queries: %s", gm.get("webSearchQueries"))
                LOG.debug("    Search Entry Point: %s", gm.get("searchEntryPoint"))
                LOG.debug("    Retrieval Queries: %s", gm.get("retrievalQueries"))
            else:
                LOG.debug("  NO Grounding Metadata")

            # Citation
            cm = cand.get("citationMetadata", {})
            if cm:
                LOG.debug("  Citation Metadata Present: %d sources", len(cm.get("citationSources", [])))

        # Prompt Feedback
        pf = data.get("promptFeedback", {})
        if pf:
            LOG.debug("Prompt Feedback: %s", pf)

        LOG.debug("------------------------")
    except Exception as e:
        LOG.debug("Error logging response details: %s", e)
# -------------------------------------


def _is_transient_error(exc: Exception) -> bool:
    """Check if an error is transient and should be retried."""
    msg = str(exc).lower()
    transient_indicators = [
        "429", "rate limit", "quota",  # Rate limiting
        "timeout", "timed out",         # Timeouts
        "502", "503", "504",            # Server errors
        "connection", "network",        # Network issues
        "grounding", "validation",      # Grounding/validation failures (retry same request)
        "temporarily unavailable",
        "service unavailable",
        "internal server error",
    ]
    return any(tok in msg for tok in transient_indicators)


def _normalize_model(model: str) -> str:
    if not model:
        return ""
    return model.split(":")[0]


def build_payload(prompt: str, cfg: Dict) -> Tuple[Dict, Optional[Dict]]:
    """
    Build a Gemini API payload that enforces web_search.
    """
    model = cfg.get("model")
    if not model:
        raise RuntimeError("Google provider requires 'model' in config - no fallback defaults allowed")
    model_to_use = _normalize_model(model)

    # For JSON requests, prepend a JSON-only instruction; otherwise use prompt as-is
    try:
        request_json = bool(cfg.get("json"))
    except Exception:
        request_json = False

    if request_json:
        json_instr = "Return only a single valid JSON object. Do not include any prose or Markdown fences. Output must be strictly valid JSON."
        final_prompt = f"{json_instr}\n\n{prompt}"
    else:
        # Use the prompt exactly as provided - no additional prefixes
        final_prompt = prompt

    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [
                    {"text": final_prompt}
                ]
            }
        ],
        "tools": [
            {
                "google_search": {}
            }
        ]
    }

    # Note: toolConfig.functionCallingConfig is for custom function_declarations only,
    # NOT for built-in tools like google_search. The model will use google_search
    # when it deems appropriate based on the prompt.
    generation_config = {}
    if "max_completion_tokens" in cfg and cfg["max_completion_tokens"] is not None:
        generation_config["maxOutputTokens"] = int(cfg["max_completion_tokens"])

    if "temperature" in cfg and cfg["temperature"] is not None:
        generation_config["temperature"] = float(cfg["temperature"])

    if "top_p" in cfg and cfg["top_p"] is not None:
        generation_config["topP"] = float(cfg["top_p"])

    # Apply reasoning config based on model version
    if model_to_use.startswith("gemini-3"):
        # Gemini 3: Use thinkingLevel (cannot use thinkingBudget)
        # Default to high reasoning if not specified, and ensure thoughts are included.
        tc = generation_config.get("thinkingConfig", {})

        # Respect configured effort if present (low/high), default to high
        configured_effort = (cfg.get("reasoning") or {}).get("effort")
        if configured_effort and str(configured_effort).lower() in ("low", "high"):
            tc["thinkingLevel"] = str(configured_effort).lower()
        else:
            # Map thinking_budget_tokens to thinkingLevel if provided
            configured_budget = cfg.get("thinking_budget_tokens")
            if configured_budget is not None:
                if configured_budget < 4000:
                    tc["thinkingLevel"] = "low"
                elif configured_budget > 12000:
                    tc["thinkingLevel"] = "high"
                else:
                    tc["thinkingLevel"] = "medium"
            else:
                tc["thinkingLevel"] = "high"

        tc["includeThoughts"] = True
        generation_config["thinkingConfig"] = tc
    elif model_to_use.startswith("gemini-2.5"):
        # Gemini 2.5: Use thinkingBudget (legacy style)
        # Only apply if we want to enforce reasoning defaults for 2.5 as well.
        # Given the user's context implies a migration from an existing setup,
        # we'll add a safe default budget to match the "program that uses gemini 2.5-pro with reasoning" description.
        tc = generation_config.get("thinkingConfig", {})
        if "thinkingBudget" not in tc:
            configured_budget = cfg.get("thinking_budget_tokens")
            tc["thinkingBudget"] = configured_budget if configured_budget else 8000
        tc["includeThoughts"] = True
        generation_config["thinkingConfig"] = tc

    # Never set JSON responseMimeType or responseJsonSchema for Google; JSON is prompt-only when cfg["json"] is True.

    if generation_config:
        payload["generationConfig"] = generation_config

    return payload, None


def extract_reasoning(raw_json: Dict) -> Optional[str]:
    """
    Extract reasoning content from a Gemini API response object if present.

    Accepted indicators (in order of preference):
    - candidates[*].groundingMetadata.webSearchQueries (joined as newline string)
    - candidates[*].groundingMetadata.groundingSupports / confidenceScores (summarized)
    - candidates[*].content.parts[*].text (first 1-2 non-empty text parts joined)
    """
    # --- REASONING EXTRACTION ---
    try:
        import json
        _run_id = os.getenv('FPF_RUN_GROUP_ID', '?')[:8]
        LOG.debug("[FPF GOOGLE] run=%s extract_reasoning input keys: %s", _run_id,
                  list(raw_json.keys()) if isinstance(raw_json, dict) else 'not_dict')
    except Exception:
        pass
    # ---------------------------------------------

    if not isinstance(raw_json, dict):
        return None

    # 1) Grounding metadata: webSearchQueries (strongest signal)
    try:
        cands = raw_json.get("candidates")
        if isinstance(cands, list) and cands:
            gm = cands[0].get("groundingMetadata")
            if isinstance(gm, dict):
                queries = gm.get("webSearchQueries")
                if isinstance(queries, list) and queries:
                    return "\n".join([str(q) for q in queries if isinstance(q, str) and q.strip()])
    except Exception as e:
        LOG.debug("[FPF GOOGLE] extract_reasoning step 1 failed: %s", e)
        pass

    # 2) Grounding metadata: supports / confidence (treat as reasoning summary)
    try:
        cands = raw_json.get("candidates")
        if isinstance(cands, list) and cands:
            gm = cands[0].get("groundingMetadata")
            if isinstance(gm, dict):
                supports = gm.get("groundingSupports") or gm.get("supportingContent") or []
                confs = gm.get("confidenceScores") or []
                if (isinstance(supports, list) and len(supports) > 0) or (isinstance(confs, list) and len(confs) > 0):
                    sup_n = len(supports) if isinstance(supports, list) else 0
                    conf_n = len(confs) if isinstance(confs, list) else 0
                    return f"Gemini grounding metadata present (supports={sup_n}, confidence_scores={conf_n})."
    except Exception as e:
        LOG.debug("[FPF GOOGLE] extract_reasoning step 2 failed: %s", e)
        pass

    # 3) Content parts text (fallback summary)
    try:
        cands = raw_json.get("candidates")
        if isinstance(cands, list) and cands:
            content = cands[0].get("content") or {}
            parts = content.get("parts")
            if isinstance(parts, list):
                texts: List[str] = []
                for p in parts:
                    if isinstance(p, dict):
                        t = p.get("text")
                        if isinstance(t, str) and t.strip():
                            texts.append(t.strip())
                if texts:
                    # If we have multiple parts, the earlier ones are likely reasoning/thoughts
                    if len(texts) > 1:
                        return "\n\n".join(texts[:-1])
                    # If only one part, it's likely just the response, so no reasoning extracted here
                    return None
    except Exception as e:
        LOG.debug("[FPF GOOGLE] extract_reasoning step 3 failed: %s", e)
        pass

    return None


def parse_response(raw_json: Dict) -> str:
    """
    Extract readable text from a Gemini API response object.
    """
    if not isinstance(raw_json, dict):
        return str(raw_json)

    try:
        parts = raw_json["candidates"][0]["content"]["parts"]
        # Filter for parts that have text
        text_parts = [p for p in parts if "text" in p]

        if not text_parts:
            return ""

        # If multiple text parts exist, the last one is the final response.
        # The earlier ones are likely thoughts/reasoning when includeThoughts=True.
        return text_parts[-1]["text"]
    except (IndexError, KeyError, TypeError):
        import json
        return json.dumps(raw_json, indent=2)

def execute_and_verify(provider_url: str, payload: Dict, headers: Optional[Dict], verify_helpers, timeout: Optional[int] = None, max_retries: int = 3, retry_delay: float = 1.0) -> Dict:
    """
    Execute the Google Gemini request and verify both grounding and reasoning are present.
    Enforces mandatory grounding (google_search) and reasoning at the lowest level.

    Includes retry logic with exponential backoff for transient errors (502, 503, 504, etc.)

    Args:
        retry_delay: Base delay in seconds between retry attempts (default 1.0)
    """
    import urllib.request
    import urllib.error
    import json as _json

    LOG.debug("ENTER execute_and_verify timeout=%s", timeout)
    LOG.debug("Preparing payload serialization...")

    try:
        data = _json.dumps(payload).encode("utf-8")
    except Exception as e:
        LOG.debug("Serialization failed: %s", e)
        raise

    LOG.debug("Payload serialized. Size: %d bytes", len(data))
    _dump_bits("request_payload", data)

    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    LOG.debug("Headers prepared: %s", list(hdrs.keys()))

    # Check for API Key in headers
    if "x-goog-api-key" in hdrs:
        key_val = hdrs["x-goog-api-key"]
        masked_key = f"{key_val[:4]}...{key_val[-4:]}" if key_val and len(key_val) > 8 else "INVALID"
        LOG.debug("API Key found in headers: x-goog-api-key=%s (Length: %d)", masked_key, len(key_val) if key_val else 0)
    else:
        LOG.debug("CRITICAL: x-goog-api-key NOT found in headers!")
        LOG.error("x-goog-api-key missing from Google request headers")

    base_delay_ms = int(retry_delay * 1000)  # Convert retry_delay seconds to milliseconds
    max_delay_ms = 30000
    last_error = None

    for attempt in range(1, max_retries + 1):
        LOG.debug("Starting attempt %d/%d", attempt, max_retries)

        req = urllib.request.Request(provider_url, data=data, headers=hdrs, method="POST")
        LOG.debug("Request object created: %s", req)
        LOG.debug("Request method: %s", req.get_method())
        LOG.debug("Request full url: %s", req.get_full_url())

        try:
            LOG.debug("Google request attempt %d/%d to %s", attempt, max_retries, provider_url)
            LOG.debug("Attempt %d: Calling urlopen with timeout=%s", attempt, timeout)

            start_time = time.time()

            def logged_urlopen(r, t):
                if t is None:
                    return urllib.request.urlopen(r)
                return urllib.request.urlopen(r, timeout=t)

            with logged_urlopen(req, timeout) as resp:
                elapsed = time.time() - start_time
                LOG.debug("Attempt %d: urlopen returned after %.4fs", attempt, elapsed)
                LOG.debug("Response code: %s", resp.getcode())
                LOG.debug("Response headers: %s", resp.info())

                LOG.debug("Reading response body...")
                raw_bytes = resp.read()
                LOG.debug("Read %d bytes from response", len(raw_bytes))
                _dump_bits(f"response_body_attempt_{attempt}", raw_bytes)

                LOG.debug("Decoding response...")
                raw = raw_bytes.decode("utf-8")
                LOG.debug("Response decoded. Length: %d chars", len(raw))

                LOG.debug("Parsing JSON...")
                raw_json = _json.loads(raw)
                LOG.debug("JSON parsed successfully. Keys: %s", list(raw_json.keys()) if isinstance(raw_json, dict) else "not_dict")

                _log_response_details(raw_json)

            # Enforce grounding + reasoning using shared helpers; pass this module for provider-specific extraction
            try:
                verify_helpers.assert_grounding_and_reasoning(raw_json, provider=__import__(__name__))
            except verify_helpers.ValidationError as ve:
                # Re-raise ValidationError so file_handler/adapter can handle it
                # (retry logic, error reporting, etc.) instead of killing the process.
                # NOTE: Previously this called sys.exit() which killed the entire
                # uvicorn server when running in-process. See 911refractor.
                LOG.error("[VALIDATION FAILED] %s (grounding=%s reasoning=%s)",
                          ve, ve.missing_grounding, ve.missing_reasoning)

                # Minimal failure artifact — logged instead of written to disk (Phase 6B)
                try:
                    cands = raw_json.get("candidates")
                    gm_present = False
                    if isinstance(cands, list) and cands:
                        gm = cands[0].get("groundingMetadata")
                        gm_present = isinstance(gm, dict) and len(gm) > 0
                    LOG.warning(
                        "Grounding validation failed: %s | grounding=%s reasoning=%s candidates=%s gm=%s",
                        ve, ve.missing_grounding, ve.missing_reasoning,
                        isinstance(cands, list) and len(cands) > 0, gm_present,
                    )
                except Exception:
                    pass

                raise  # Re-raise the ValidationError for upstream handling
            return raw_json

        except urllib.error.HTTPError as he:
            try:
                msg = he.read().decode("utf-8", errors="ignore")
            except Exception:
                msg = ""
            last_error = RuntimeError(f"HTTP error {getattr(he, 'code', '?')}: {getattr(he, 'reason', '?')} - {msg}")

            # Check if we should retry
            if attempt < max_retries and _is_transient_error(last_error):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)  # Full jitter
                delay_s = delay_ms / 1000.0

                LOG.debug("TRANSIENT ERROR DETECTED: %s", last_error)
                LOG.debug("Retry Decision: YES (Attempt %d < %d)", attempt, max_retries)
                LOG.debug("Backoff Strategy: Exponential with Full Jitter")
                LOG.debug("Base Delay: %dms, Max Delay: %dms", base_delay_ms, max_delay_ms)
                LOG.debug("Calculated Delay: %.4fs", delay_s)

                LOG.warning("Transient error on attempt %d/%d, retrying in %.2fs: %s", attempt, max_retries, delay_s, he)

                LOG.debug("Sleeping for %.4fs...", delay_s)
                time.sleep(delay_s)
                LOG.debug("Woke up from sleep. Proceeding to next attempt.")
                continue

            LOG.debug("FATAL ERROR or MAX RETRIES REACHED")
            LOG.debug("Is Transient: %s", _is_transient_error(last_error))
            LOG.debug("Attempts Exhausted: %s", attempt >= max_retries)
            raise last_error from he

        except Exception as e:
            # --- EXCEPTION DEBUG ---
            LOG.error("[FPF GOOGLE] Exception on attempt %d: %s: %s", attempt, type(e).__name__, e)
            LOG.debug("EXCEPTION CAUGHT: %s: %s", type(e).__name__, e)
            # ----------------------------------------
            last_error = RuntimeError(f"HTTP request failed: {e}")

            # Check if we should retry
            if attempt < max_retries and _is_transient_error(e):
                delay_ms = min(base_delay_ms * (2 ** (attempt - 1)), max_delay_ms)
                delay_ms = random.uniform(0, delay_ms)  # Full jitter
                delay_s = delay_ms / 1000.0

                LOG.debug("TRANSIENT EXCEPTION DETECTED: %s", e)
                LOG.debug("Retry Decision: YES (Attempt %d < %d)", attempt, max_retries)
                LOG.debug("Calculated Delay: %.4fs", delay_s)

                LOG.warning("Transient error on attempt %d/%d, retrying in %.2fs: %s", attempt, max_retries, delay_s, e)

                LOG.debug("Sleeping for %.4fs...", delay_s)
                time.sleep(delay_s)
                LOG.debug("Woke up from sleep. Proceeding to next attempt.")
                continue

            LOG.debug("FATAL EXCEPTION or MAX RETRIES REACHED")
            raise last_error from e
    # Should not reach here, but just in case
    if last_error:
        LOG.debug("Loop finished with error: %s", last_error)
        raise last_error
    LOG.debug("Loop finished without explicit error but no return. Raising generic RuntimeError.")
    raise RuntimeError("HTTP request failed after all retries")
