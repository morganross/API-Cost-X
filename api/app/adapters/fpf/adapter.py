"""
FPF (FilePromptForge) Adapter for APICostX.

Refactored from subprocess to direct in-process call to file_handler.run().
Uses the ContextVar-backed os.environ proxy (ENV_OVERRIDES) for local
API key isolation — same technique as the GPTR adapter.

FPF core code (file_handler.py, grounding_enforcer.py, providers/, etc.)
is NOT modified. This adapter calls file_handler.run() directly instead
of spawning a subprocess to run fpf_main.py.
"""
import asyncio
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.infra.env_context import ENV_OVERRIDES
from ..base import (
    BaseAdapter,
    GenerationConfig,
    GenerationResult,
    GeneratorType,
    ProgressCallback,
    TaskStatus,
)
from .errors import FpfExecutionError, FpfTimeoutError
from app.config import get_settings
from app.services.log_writer import SidecarLogHandler

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Ensure FilePromptForge is importable. FPF is vendored in this public
# self-hosted repo at packages/FilePromptForge, not installed separately.
# ---------------------------------------------------------------------------
def _find_vendorized_fpf_dir() -> Path:
    for candidate in Path(__file__).resolve().parents:
        fpf_dir = candidate / "packages" / "FilePromptForge"
        if (fpf_dir / "file_handler.py").exists():
            return fpf_dir
    raise RuntimeError("Vendored FilePromptForge not found at packages/FilePromptForge")


_fpf_dir_path = _find_vendorized_fpf_dir()
_fpf_dir = str(_fpf_dir_path)
if _fpf_dir not in sys.path:
    sys.path.insert(0, _fpf_dir)

from file_handler import _load_provider_module, run as fpf_run  # noqa: E402  # FPF's single entry point
from grounding_enforcer import ValidationError as GroundingValidationError  # noqa: E402


def _resolve_validation_requirement_flags(provider_name: Optional[str]) -> tuple[bool, bool]:
    """Best-effort lookup of provider-side invariant requirements."""
    if not provider_name:
        return True, True
    try:
        provider_module = _load_provider_module(provider_name)
    except Exception:
        logger.debug(
            "FPF adapter: failed to load provider module for invariant flags: provider=%s",
            provider_name,
            exc_info=True,
        )
        return True, True
    return (
        bool(getattr(provider_module, "REQUIRES_GROUNDING", True)),
        bool(getattr(provider_module, "REQUIRES_REASONING", True)),
    )


def _classify_invariant_failure(exc: GroundingValidationError) -> str:
    if getattr(exc, "missing_grounding", False) and getattr(exc, "missing_reasoning", False):
        return "both"
    if getattr(exc, "missing_grounding", False):
        return "grounding"
    if getattr(exc, "missing_reasoning", False):
        return "reasoning"
    return "unknown"


def _build_invariant_failure_payload(
    *,
    exc: GroundingValidationError,
    provider_name: Optional[str],
    model_name: str,
    task_id: str,
) -> dict[str, Any]:
    requires_grounding, requires_reasoning = _resolve_validation_requirement_flags(provider_name)
    return {
        "failure_type": _classify_invariant_failure(exc),
        "source": "fpf_validation",
        "message": str(exc),
        "provider": provider_name or "",
        "model": model_name,
        "task_id": task_id,
        "requires_grounding": requires_grounding,
        "requires_reasoning": requires_reasoning,
        "grounding_detected": None if not requires_grounding else not bool(getattr(exc, "missing_grounding", False)),
        "reasoning_detected": None if not requires_reasoning else not bool(getattr(exc, "missing_reasoning", False)),
    }


class FpfAdapter(BaseAdapter):
    """
    Adapter for FilePromptForge (FPF).

    Calls file_handler.run() directly (in-process) instead of spawning
    a subprocess per request.  Per-user API key isolation is provided by
    the ContextVar-backed os.environ proxy installed at app startup.

    Example:
        adapter = FpfAdapter()
        config = GenerationConfig(provider="openai", model="gpt-5")
        result = await adapter.generate(
            query="What are the latest developments in quantum computing?",
            config=config,
        )
        print(result.content)
    """

    def __init__(self):
        # task_id → asyncio.Task for cancellation
        self._active_tasks: Dict[str, asyncio.Task] = {}

    @property
    def name(self) -> GeneratorType:
        return GeneratorType.FPF

    @property
    def display_name(self) -> str:
        return "FilePromptForge"

    async def generate(
        self,
        query: str,
        config: GenerationConfig,
        *,
        user_uuid: str,
        document_content: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        log_writer=None,
    ) -> GenerationResult:
        """
        Run FilePromptForge on a query.

        Args:
            query: The research question/instructions
            config: Generation configuration (model, provider, etc.)
            user_uuid: User UUID for fetching provider API keys from the root .env
            document_content: Optional document content for file_a
            progress_callback: Optional callback for progress updates

        Returns:
            GenerationResult with report and sources
        """
        extra = config.extra or {}
        task_id = str(extra.get("task_id", str(uuid.uuid4())[:8]))
        started_at = datetime.utcnow()

        # Create temporary files for FPF
        # FPF compose_input expects: file_b (instructions) FIRST, then file_a (document)
        # So: file_a = document content, file_b = instructions/query
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # file_a = document content (appended after instructions in final prompt)
            file_a_path = tmp_path / "content.txt"
            file_a_content = document_content or ""
            file_a_path.write_text(file_a_content, encoding="utf-8")

            # file_b = instructions/query (placed first in final prompt)
            file_b_path = tmp_path / "instructions.txt"
            file_b_path.write_text(query, encoding="utf-8")

            output_path = tmp_path / "output.md"

            # ---- Parse provider / model (same logic as old _build_fpf_command) ----
            model = config.model
            provider = config.provider
            # Only parse provider:model format if provider is not already set.
            # Avoids double-parsing models like "meta-llama/llama-3.1-405b:free"
            # which have a colon for the :free suffix, not provider:model format.
            if not provider and ":" in model:
                parts = model.split(":", 1)
                provider = parts[0]
                model = parts[1]
                logger.info(f"FPF adapter: parsed model string -> provider='{provider}', model='{model}'")

            # ---- Build per-request env overrides (ContextVar, no os.environ mutation) ----
            env_overrides: Dict[str, str] = {}

            from app.security.key_injection import inject_provider_keys_for_user_auto, PROVIDER_TO_ENV_VAR
            try:
                _key_mode = extra.get('key_mode', 'system')
                env_overrides = await inject_provider_keys_for_user_auto(user_uuid, env_overrides, key_mode=_key_mode)
                logger.debug(f"FPF: Injected API keys from the root .env for user_uuid={user_uuid}")
            except Exception as e:
                logger.warning(f"FPF: Failed to inject provider keys for user {user_uuid}: {e}")

            # Validate provider key is present
            env_var = PROVIDER_TO_ENV_VAR.get(provider or config.provider)
            if not env_var:
                raise FpfExecutionError(f"Provider '{provider or config.provider}' has no API key mapping")
            # Check overrides first, then fall through to base environ (proxy handles this,
            # but we validate early to give a clear error message)
            if not env_overrides.get(env_var) and not os.environ.get(env_var):
                raise FpfExecutionError(
                    f"Missing API key for provider '{provider or config.provider}'. "
                    "Set it in Settings > API Keys."
                )

            # FPF-specific env vars (previously set on subprocess env dict)
            run_id = extra.get("run_id")
            if run_id:
                env_overrides["FPF_RUN_GROUP_ID"] = run_id
                # Point FPF logs to APICostX's local logs directory
                logs_dir = self._get_run_root(user_uuid, run_id) / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                env_overrides["FPF_LOG_DIR"] = str(logs_dir.resolve())

            actual_timeout = extra.get("timeout") or 1200  # 20 min default

            # Deep research providers need much longer timeouts (up to 60 min)
            if provider in ("openaidp", "googledp", "perplexity"):
                actual_timeout = max(actual_timeout, 3600)  # at least 1 hour

            # FPF paths
            fpf_dir = Path(self._get_fpf_directory())
            config_path = str(fpf_dir / "fpf_config.yaml")
            env_path = str(fpf_dir.parents[1] / ".env")

            # Build kwargs for file_handler.run()
            fpf_kwargs: Dict[str, Any] = dict(
                file_a=str(file_a_path),
                file_b=str(file_b_path),
                out_path=str(output_path),
                config_path=config_path,
                env_path=env_path,
                provider=provider,
                model=model,
                reasoning_effort=extra.get("reasoning_effort"),
                max_completion_tokens=extra.get("max_completion_tokens"),
                thinking_budget_tokens=extra.get("thinking_budget_tokens"),
                timeout=actual_timeout,
                fpf_max_retries=extra.get("fpf_max_retries"),
                fpf_retry_delay=extra.get("fpf_retry_delay"),
                request_json=bool(extra.get("json_output")) if extra.get("json_output") is not None else None,
            )
            if (provider or "").lower() == "openrouter" and isinstance(extra.get("web_search"), dict):
                fpf_kwargs["web_search"] = extra["web_search"]
                logger.info(
                    "FPF OpenRouter web search config attached: task_id=%s model=%s web_search=%s",
                    task_id,
                    model,
                    extra["web_search"],
                )

            logger.info(
                f"FPF in-process run starting: task_id={task_id}, provider={provider}, "
                f"model={model}, timeout={actual_timeout}s ({actual_timeout // 60}min)"
            )

            # ---- Set ContextVar and run ----
            _fpf_sidecar_handler = None
            _fpf_logger_names = [
                "file_handler",
                "fpf_google_main",
                "fpf_perplexity_main",
                "fpf_openrouter_main",
                "grounding_enforcer",
                "fpf_scheduler",
            ]
            token = ENV_OVERRIDES.set(env_overrides)
            if log_writer is not None and getattr(log_writer, "save_to_sidecar", False):
                _fpf_sidecar_handler = SidecarLogHandler(log_writer, source="fpf")
                for _ln in _fpf_logger_names:
                    logging.getLogger(_ln).addHandler(_fpf_sidecar_handler)
            try:
                fpf_coro = asyncio.to_thread(fpf_run, **fpf_kwargs)
                fpf_task = asyncio.ensure_future(fpf_coro)
                self._active_tasks[task_id] = fpf_task

                try:
                    out_file = await asyncio.wait_for(
                        asyncio.shield(fpf_task), timeout=actual_timeout
                    )
                except asyncio.TimeoutError:
                    _grace = min(actual_timeout, 300)
                    logger.warning(
                        f"FPF task {task_id} exceeded {actual_timeout}s timeout. "
                        f"Granting {_grace}s grace period for in-flight response..."
                    )
                    try:
                        out_file = await asyncio.wait_for(
                            fpf_task, timeout=_grace
                        )
                        logger.info(
                            f"FPF task {task_id} completed during "
                            f"{_grace}s grace period - output salvaged"
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        fpf_task.cancel()
                        try:
                            await fpf_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        raise FpfTimeoutError(
                            f"FPF task {task_id} exceeded timeout "
                            f"{actual_timeout}s + {_grace}s grace"
                        )
                except asyncio.CancelledError:
                    raise FpfExecutionError(f"FPF task {task_id} was cancelled")
                finally:
                    self._active_tasks.pop(task_id, None)

                result_content = Path(out_file).read_text(encoding="utf-8", errors="replace")
                logger.info(f"FPF run completed: task_id={task_id}, output={out_file}")

            except (FpfTimeoutError, FpfExecutionError):
                raise
            except GroundingValidationError as e:
                invariant_failure = _build_invariant_failure_payload(
                    exc=e,
                    provider_name=provider or config.provider,
                    model_name=model,
                    task_id=task_id,
                )
                logger.warning(
                    "FPF invariant validation failed: task_id=%s provider=%s model=%s failure_type=%s",
                    task_id,
                    invariant_failure.get("provider"),
                    invariant_failure.get("model"),
                    invariant_failure.get("failure_type"),
                )
                raise FpfExecutionError(
                    str(e),
                    invariant_failure=invariant_failure,
                ) from e
            except SystemExit as e:
                logger.error(f"FPF raised SystemExit({e.code}) - suppressed to protect server. task_id={task_id}")
                raise FpfExecutionError(
                    f"FPF task {task_id} called sys.exit({e.code}). "
                    f"This is a bug in the provider code."
                ) from e
            except Exception as e:
                logger.error("FPF execution failed: %s (task_id=%s)", type(e).__name__, task_id)
                raise FpfExecutionError(f"FPF execution failed: {e}") from e
            finally:
                if _fpf_sidecar_handler is not None:
                    for _ln in _fpf_logger_names:
                        logging.getLogger(_ln).removeHandler(_fpf_sidecar_handler)
                ENV_OVERRIDES.reset(token)

            completed_at = datetime.utcnow()
            duration = completed_at - started_at

            input_tokens = 0
            output_tokens = 0
            total_tokens = 0

            return GenerationResult(
                generator=GeneratorType.FPF,
                task_id=task_id,
                content=result_content,
                content_type="markdown",
                model=config.model,
                provider=config.provider,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration.total_seconds(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                sources=[],
                metadata={},
                status=TaskStatus.COMPLETED,
            )

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running FPF task."""
        task = self._active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancelled FPF task {task_id}")
            return True
        return False

    def _get_run_root(self, user_uuid: str, run_id: str) -> Path:
        settings = get_settings()
        return settings.data_dir / f"user_{user_uuid}" / "runs" / run_id

    def _get_fpf_directory(self) -> str:
        """Get the FilePromptForge directory path."""
        return str(_fpf_dir_path)

    async def health_check(self) -> bool:
        """Check if FPF is importable and config exists."""
        try:
            fpf_dir = Path(self._get_fpf_directory())
            return (
                (fpf_dir / "file_handler.py").exists()
                and (fpf_dir / "fpf_config.yaml").exists()
            )
        except Exception:
            return False
