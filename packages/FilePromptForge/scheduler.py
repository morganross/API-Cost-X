"""
FPF scheduler: concurrent batch execution with global rate limiting and retries.

Implements:
- RunSpec: a single FPF run (provider, model, file_a, file_b, out?, overrides?)
- GlobalRateLimiter: enforces global QPS with no burst; all runs are staggered by at least 1/QPS
- RunExecutor: global concurrency gate, global rate limit, retries/backoff
- run_many(specs, config_path, env_path, concurrency_cfg) -> list[dict]

Notes:
- Thread-based orchestration keeps providers and file_handler synchronous (urllib).
- Does NOT mutate process-wide environment; file_handler reads API keys from os.environ
  or the repository root .env and headers are passed per call.
- Per-provider settings and burst are deprecated and ignored. If legacy settings are present
  in concurrency_cfg, we derive a global qps from them (min positive qps) and ignore burst.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import threading
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# Local imports (file_handler is in the same package directory)
try:
    from .file_handler import run as fpf_run
except Exception:
    from file_handler import run as fpf_run  # type: ignore

log = logging.getLogger("fpf_scheduler")


@dataclass
class RunSpec:
    id: Optional[str]
    provider: str
    model: str
    file_a: str
    file_b: str
    out: Optional[str] = None
    overrides: Dict[str, Any] = field(default_factory=dict)


class GlobalRateLimiter:
    """
    Simple global rate limiter: enforces a minimum interval between starts.
    QPS must be > 0. No burst; spacing is at least 1/QPS.
    """
    def __init__(self, qps: float) -> None:
        if qps is None or qps <= 0.0:
            raise ValueError("GlobalRateLimiter requires qps > 0.0")
        self.interval = 1.0 / float(qps)
        self._lock = threading.Lock()
        self._next_allowed = time.monotonic()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_for = self._next_allowed - now
            if wait_for > 0:
                # Sleep outside lock to avoid blocking other readers too long
                pass
            # Update next_allowed before sleeping to serialize starts
            base = self._next_allowed if self._next_allowed > now else now
            self._next_allowed = base + self.interval
        if wait_for is not None and wait_for > 0:
            time.sleep(wait_for)


def _validate_concurrency_cfg(concurrency_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate concurrency config - ALL values MUST be explicitly set.
    NO DEFAULTS. NO FALLBACKS. Raises ValueError if missing.
    """
    if not concurrency_cfg:
        raise ValueError("concurrency_cfg is required - no defaults allowed")

    if "enabled" not in concurrency_cfg:
        raise ValueError("concurrency_cfg.enabled is required")
    if "max_concurrency" not in concurrency_cfg:
        raise ValueError("concurrency_cfg.max_concurrency is required")
    if "qps" not in concurrency_cfg:
        raise ValueError("concurrency_cfg.qps is required")

    qps = float(concurrency_cfg["qps"])
    if qps <= 0.0:
        raise ValueError(f"concurrency_cfg.qps must be > 0, got {qps}")

    return {
        "enabled": bool(concurrency_cfg["enabled"]),
        "max_concurrency": int(concurrency_cfg["max_concurrency"]),
        "qps": qps
    }


class RunExecutor:
    def __init__(self, config_path: str, env_path: str, concurrency_cfg: Dict[str, Any]) -> None:
        """
        concurrency_cfg schema (global-only; legacy keys tolerated):
        {
          "enabled": true,
          "max_concurrency": 12,
          "qps": 2.0,
          "retry": {
            "max_retries": 5,
            "base_delay_ms": 500,
            "max_delay_ms": 60000,
            "jitter": "full" | "none"
          },
          "timeout_seconds": 7200
        }
        Legacy keys 'per_provider' and 'burst' are ignored. If legacy per-provider qps are provided,
        we derive a global qps as the minimum positive among them.
        """
        self.config_path = config_path
        self.env_path = env_path

        eff = _validate_concurrency_cfg(concurrency_cfg)
        self.enabled = eff["enabled"]
        self.global_max = eff["max_concurrency"]
        self.global_qps = eff["qps"]
        self.rate_limiter = GlobalRateLimiter(self.global_qps)

        # Retry config - ALL values required, no defaults
        if "retry" not in concurrency_cfg:
            raise ValueError("concurrency_cfg.retry is required")
        r = concurrency_cfg["retry"]
        if "max_retries" not in r:
            raise ValueError("concurrency_cfg.retry.max_retries is required")
        if "base_delay_ms" not in r:
            raise ValueError("concurrency_cfg.retry.base_delay_ms is required")
        if "max_delay_ms" not in r:
            raise ValueError("concurrency_cfg.retry.max_delay_ms is required")
        if "jitter" not in r:
            raise ValueError("concurrency_cfg.retry.jitter is required")
        self.max_retries = int(r["max_retries"])
        self.base_delay = int(r["base_delay_ms"]) / 1000.0
        self.max_delay = int(r["max_delay_ms"]) / 1000.0
        self.jitter_mode = str(r["jitter"]).lower()

        # Global semaphore
        self.global_sem = threading.Semaphore(self.global_max)

        log.info(
            "FPF concurrency: enabled=%s, max_concurrency=%s, qps=%.3f (min interval %.3fs). "
            "Per-provider settings and burst are deprecated and ignored.",
            self.enabled, self.global_max, self.global_qps, (1.0 / self.global_qps),
        )

    def _with_jitter(self, base: float) -> float:
        if self.jitter_mode == "none":
            return base
        # full jitter
        return random.uniform(0, base)

    def _is_transient(self, exc: Exception) -> bool:
        # Heuristic classification based on message content
        msg = str(exc).lower()
        return any(tok in msg for tok in ["429", "timeout", "timed out", "502", "503", "504", "rate limit", "grounding", "validation"])

    def _backoff_sleep(self, attempt: int) -> None:
        # attempt starts at 1
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        delay = self._with_jitter(delay)
        if delay > 0:
            time.sleep(delay)

    def run_one(self, spec: RunSpec) -> Dict[str, Any]:
        """
        Execute a single run with retries.
        Emits uniform RUN_START / RUN_COMPLETE signals at INFO to both console and file logs.
        Returns a result dict:
          {"id": ..., "provider": ..., "model": ..., "path": str|None, "error": str|None}
        """
        provider_key = (spec.provider or "").lower()
        model_key = (spec.model or "").lower()
        # Classify run kind once for logging
        kind = "deep" if (provider_key == "openaidp" or ("deep-research" in model_key)) else "rest"
        import time as _time
        start_ts_overall = None

        def _attempt() -> str:
            # Check if this is a deep research model that should bypass concurrency/rate limits
            is_deep_research = (provider_key in ("openaidp", "googledp")) or ("deep-research" in model_key)

            if is_deep_research:
                log.info("Bypassing global concurrency/rate limit for deep research model: %s:%s", spec.provider, spec.model)
                return fpf_run(
                    file_a=spec.file_a,
                    file_b=spec.file_b,
                    out_path=spec.out,
                    config_path=self.config_path,
                    env_path=self.env_path,
                    provider=spec.provider,
                    model=spec.model,
                    reasoning_effort=(spec.overrides or {}).get("reasoning_effort"),
                    max_completion_tokens=(spec.overrides or {}).get("max_completion_tokens"),
                    request_json=(spec.overrides or {}).get("request_json"),
                )
            else:
                # Global staggering: enforce minimum interval before starting this run
                self.rate_limiter.wait()
                # Global concurrency gate
                with self.global_sem:
                    # Execute underlying run (file_handler.run)
                    return fpf_run(
                        file_a=spec.file_a,
                        file_b=spec.file_b,
                        out_path=spec.out,
                        config_path=self.config_path,
                        env_path=self.env_path,
                        provider=spec.provider,
                        model=spec.model,
                        reasoning_effort=(spec.overrides or {}).get("reasoning_effort"),
                        max_completion_tokens=(spec.overrides or {}).get("max_completion_tokens"),
                        request_json=(spec.overrides or {}).get("request_json"),
                    )

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            # Emit RUN_START for this attempt
            try:
                if start_ts_overall is None:
                    start_ts_overall = _time.time()
                log.info(
                    "[FPF RUN_START] id=%s kind=%s provider=%s model=%s file_b=%s out=%s attempt=%s",
                    spec.id, kind, spec.provider, spec.model, spec.file_b, (spec.out or "na"),
                    f"{attempt}/{self.max_retries}"
                )
            except Exception:
                pass
            try:
                path = _attempt()
                # Success: emit RUN_COMPLETE ok=true
                try:
                    elapsed = (_time.time() - (start_ts_overall or _time.time()))
                    log.info(
                        "[FPF RUN_COMPLETE] id=%s kind=%s provider=%s model=%s ok=true elapsed=%.2fs status=%s path=%s error=%s",
                        spec.id, kind, spec.provider, spec.model, elapsed, "na", (path or "na"), "na"
                    )
                except Exception:
                    pass
                return {"id": spec.id, "provider": spec.provider, "model": spec.model, "path": path, "error": None}
            except Exception as e:
                last_err = e
                log.warning("Run failed (attempt %s/%s) id=%s provider=%s model=%s err=%s",
                            attempt, self.max_retries, spec.id, spec.provider, spec.model, e)
                if attempt >= self.max_retries or not self._is_transient(e):
                    # Final failure: emit RUN_COMPLETE ok=false
                    try:
                        elapsed = (_time.time() - (start_ts_overall or _time.time()))
                        log.info(
                            "[FPF RUN_COMPLETE] id=%s kind=%s provider=%s model=%s ok=false elapsed=%.2fs status=%s path=%s error=%s",
                            spec.id, kind, spec.provider, spec.model, elapsed, "na", "na", str(e)
                        )
                    except Exception:
                        pass
                    break
                self._backoff_sleep(attempt)

        return {
            "id": spec.id,
            "provider": spec.provider,
            "model": spec.model,
            "path": None,
            "error": str(last_err) if last_err else "unknown_error",
        }

    def run_many(self, specs: List[RunSpec]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        # Executor size equals global_max to throttle active threads, queue handles the rest
        workers = max(self.global_max, 1)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fpf_run") as pool:
            fut_map = {pool.submit(self.run_one, s): s for s in specs}
            for fut in as_completed(fut_map):
                try:
                    res = fut.result()
                except Exception as e:
                    s = fut_map[fut]
                    res = {"id": s.id, "provider": s.provider, "model": s.model, "path": None, "error": str(e)}
                results.append(res)
        return results


def _parse_specs_from_config(runs_cfg: Any) -> List[RunSpec]:
    specs: List[RunSpec] = []
    if not isinstance(runs_cfg, list):
        return specs
    for idx, item in enumerate(runs_cfg, start=1):
        if not isinstance(item, dict):
            continue
        specs.append(
            RunSpec(
                id=str(item.get("id") or f"run{idx}"),
                provider=str(item.get("provider") or "").strip(),
                model=str(item.get("model") or "").strip(),
                file_a=str(item.get("file_a") or "").strip(),
                file_b=str(item.get("file_b") or "").strip(),
                out=item.get("out"),
                overrides=item.get("overrides") or {},
            )
        )
    return specs


def run_many(specs: List[RunSpec] | Any, config_path: str, env_path: str, concurrency_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Entry point used by fpf_main.
    Accepts either a list[RunSpec] or a raw runs config list and constructs specs.
    Returns list of result dicts: [{"id","provider","model","path","error"}, ...]
    """
    # Coerce specs if caller passed raw runs config
    _specs = specs if (isinstance(specs, list) and (not specs or isinstance(specs[0], RunSpec))) else _parse_specs_from_config(specs)
    exe = RunExecutor(config_path=config_path, env_path=env_path, concurrency_cfg=concurrency_cfg or {})
    return exe.run_many(_specs)
