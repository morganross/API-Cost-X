import asyncio
import logging
import uuid
from typing import Optional, Dict, Any
from datetime import datetime

from app.adapters.base import BaseAdapter, GenerationConfig, GenerationResult, GeneratorType, TaskStatus, ProgressCallback
from app.adapters.gptr.config import GptrConfig
from app.infra.env_context import ENV_OVERRIDES, install_os_environ_proxy_once
from app.services.log_writer import SidecarLogHandler

logger = logging.getLogger(__name__)


def _safe_gptr_event_message(event_type: str, event_data) -> str:
    if event_type == "planning_research":
        return "Research planning started"
    if event_type == "starting_research":
        return "Research execution started"
    if event_type == "finishing_research":
        return "Research execution finished"
    if event_type in ("fetching_sources", "scraping"):
        count = len(event_data) if isinstance(event_data, (list, dict)) else "n/a"
        return f"source_count={count}"
    return "GPTR event"

# Map APICostX provider names to GPT-Researcher provider names
GPTR_PROVIDER_MAP = {
    "google": "google_genai",
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
}

def _map_model_for_gptr(model_str: str) -> str:
    """Convert APICostX model string (provider:model) to GPT-Researcher format."""
    if ":" not in model_str:
        return model_str
    provider, model = model_str.split(":", 1)
    gptr_provider = GPTR_PROVIDER_MAP.get(provider, provider)
    return f"{gptr_provider}:{model}"


class GptrAdapter(BaseAdapter):
    """
    Adapter for GPT-Researcher (gptr).
    Runs gpt-researcher as a direct in-process instance with root `.env` key
    isolation via ContextVar-backed os.environ proxy.
    Supports configurable timeout and retry on timeout.
    """

    def __init__(self):
        # task_id → asyncio.Task for cancellation support
        self._active_tasks: Dict[str, asyncio.Task] = {}

    @property
    def name(self) -> GeneratorType:
        return GeneratorType.GPTR

    @property
    def display_name(self) -> str:
        return "GPT-Researcher"

    async def health_check(self) -> bool:
        """Check if gpt-researcher is importable."""
        try:
            import gpt_researcher  # noqa: F401
            return True
        except ImportError:
            return False

    async def _run_research(
        self,
        query: str,
        report_type: str,
        tone: Optional[str],
        source_urls: Optional[list[str]],
        log_handler=None,
    ) -> Dict[str, Any]:
        """
        Create a GPTResearcher instance, run research, and return results.
        Env vars (keys, model config) must already be set via ENV_OVERRIDES
        before calling this method.
        """
        from gpt_researcher import GPTResearcher
        from gpt_researcher.utils.enum import Tone

        researcher_kwargs: Dict[str, Any] = {
            "query": query,
            "report_type": report_type,
            "report_source": "web",
        }
        if source_urls:
            researcher_kwargs["source_urls"] = source_urls
        if tone:
            # GPTResearcher requires a Tone enum, not a plain string
            tone_enum = None
            for member in Tone:
                if member.name.lower() == tone.lower():
                    tone_enum = member
                    break
            if tone_enum:
                researcher_kwargs["tone"] = tone_enum
            else:
                logger.warning(f"Unknown tone '{tone}', falling back to Objective")

        if log_handler is not None:
            researcher_kwargs["log_handler"] = log_handler

        researcher = GPTResearcher(**researcher_kwargs)

        await researcher.conduct_research()
        report = await researcher.write_report()

        context = researcher.get_research_context()
        visited = researcher.get_source_urls() if hasattr(researcher, "get_source_urls") else []

        return {
            "content": report or "",
            "context": context,
            "visited_urls": list(visited) if visited else [],
        }

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
        Run GPT-Researcher generation with timeout and retry support.
        Uses direct in-process GPTResearcher instance with ContextVar-backed
        env isolation for root `.env` API keys.
        """
        # Ensure env proxy is installed (idempotent)
        install_os_environ_proxy_once()

        extra = config.extra or {}
        task_id = str(extra.get("task_id", str(uuid.uuid4())[:8]))
        started_at = datetime.utcnow()

        # 1. Prepare Configuration
        gptr_config = GptrConfig(**extra)

        # Get timeout and retry settings
        # (field names kept as subprocess_* for backward compat with DrAdapter / callers)
        timeout_minutes = gptr_config.subprocess_timeout_minutes
        max_retries = gptr_config.subprocess_retries
        timeout_seconds = timeout_minutes * 60

        logger.info(f"GPT-R generation starting: task_id={task_id}, timeout={timeout_minutes}min, retries={max_retries}")

        # 2. Build per-request env overrides (ContextVar, not os.environ mutation)
        env_overrides: Dict[str, str] = {}

        # Inject provider API keys from the root .env for this user
        from app.security.key_injection import inject_provider_keys_for_user_auto
        try:
            _key_mode = extra.get('key_mode', 'system')
            env_overrides = await inject_provider_keys_for_user_auto(user_uuid, env_overrides, key_mode=_key_mode)
            logger.debug(f"GPT-R: Injected API keys from the root .env for user_uuid={user_uuid}")
        except Exception as e:
            logger.warning(f"GPT-R: Failed to inject provider keys for user {user_uuid}: {e}")

        # Handle Model Selection
        full_model = f"{config.provider}:{config.model}" if config.provider and config.model else config.model
        logger.info(f"GPT-R config: provider={config.provider!r}, model={config.model!r}, full_model={full_model!r}")
        model_str = _map_model_for_gptr(full_model)
        logger.info(f"GPT-R model after mapping: {model_str!r}")

        # Set model env vars
        env_overrides["SMART_LLM"] = model_str
        env_overrides["FAST_LLM"] = model_str
        env_overrides["STRATEGIC_LLM"] = model_str

        # Token caps per model to avoid provider max_tokens errors
        MODEL_OUTPUT_LIMITS = {
            "openai:gpt-4.1-mini": 4096,
            "openai:gpt-4-turbo": 4096,
            "openai:gpt-4o": 4096,
            "openai:gpt-5": 8192,
            "openai:gpt-5.1": 8192,
            "openai:gpt-5.4-mini": 8192,
            "openai:gpt-5.4-nano": 8192,
            # OpenRouter Gemini models
            "openrouter:google/gemini-2.5-pro": 65536,
            "openrouter:google/gemini-2.5-flash": 65536,
            "openrouter:google/gemini-2.5-flash-lite": 32768,
            "openrouter:google/gemini-3-pro-preview": 64000,
            "openrouter:google/gemini-3-flash-preview": 64000,
            # Direct Google Gemini models (also need limits)
            "google_genai:gemini-2.5-pro": 65536,
            "google_genai:gemini-2.5-flash": 65536,
            "google_genai:gemini-3-pro-preview": 64000,
            "google_genai:gemini-3-flash-preview": 64000,
        }
        safe_max_tokens = int(config.max_tokens or 4096)
        provider_limit = MODEL_OUTPUT_LIMITS.get(model_str, 4096)
        max_tokens = min(safe_max_tokens, provider_limit)

        env_overrides["SMART_LLM_TOKEN_LIMIT"] = str(max_tokens)
        env_overrides["FAST_LLM_TOKEN_LIMIT"] = str(max_tokens)
        env_overrides["STRATEGIC_LLM_TOKEN_LIMIT"] = str(max_tokens)
        env_overrides["SUMMARY_TOKEN_LIMIT"] = str(max(512, min(2048, max_tokens)))
        env_overrides["GPTR_TEMPERATURE"] = str(config.temperature)

        # Run parameters
        if gptr_config.retriever:
            env_overrides["RETRIEVER"] = gptr_config.retriever

        # Merge any extra env_overrides from config (e.g., BREADTH/DEPTH from DR)
        env_overrides.update(gptr_config.env_overrides)

        # 3. Set ContextVar overrides for this request
        _gptr_log_handler = None
        _sidecar_handler = None
        _gptr_logger_names = ["gpt_researcher", "research", "scraper"]

        token = ENV_OVERRIDES.set(env_overrides)
        if log_writer is not None and getattr(log_writer, "save_to_sidecar", False):
            async def _gptr_log_handler(event_data, event_type: str):
                """Classify GPTR streaming events and route to sidecar DB."""
                msg = str(event_data)[:500] if event_data else ""
                if event_type in (
                    "planning_research",
                    "starting_research",
                    "finishing_research",
                    "fetching_sources",
                    "scraping",
                ):
                    await log_writer.event(
                        "gptr",
                        "INFO",
                        f"gptr_{event_type}",
                        _safe_gptr_event_message(event_type, event_data),
                    )
                else:
                    await log_writer.detail("gptr", "DEBUG", f"GPTR: {event_type}",
                                            payload={"data": msg})

            _sidecar_handler = SidecarLogHandler(log_writer, source="gptr")
            for _ln in _gptr_logger_names:
                logging.getLogger(_ln).addHandler(_sidecar_handler)

        try:
            attempt = 0
            last_error: Optional[str] = None

            while attempt <= max_retries:
                attempt += 1
                attempt_task_id = f"{task_id}-attempt{attempt}"

                if progress_callback:
                    msg = f"Starting GPT-Researcher (attempt {attempt}/{max_retries + 1})..."
                    if asyncio.iscoroutinefunction(progress_callback):
                        await progress_callback("starting", 0.0, msg)
                    else:
                        progress_callback("starting", 0.0, msg)

                logger.info(f"GPT-R attempt {attempt}/{max_retries + 1} for task {task_id}")

                try:
                    research_coro = self._run_research(
                        query=query,
                        report_type=gptr_config.report_type,
                        tone=gptr_config.tone,
                        source_urls=gptr_config.source_urls,
                        log_handler=_gptr_log_handler,
                    )
                    research_task = asyncio.get_running_loop().create_task(research_coro)
                    self._active_tasks[attempt_task_id] = research_task

                    try:
                        result_data = await asyncio.wait_for(research_task, timeout=timeout_seconds)
                    finally:
                        self._active_tasks.pop(attempt_task_id, None)

                    if progress_callback:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback("completed", 1.0, "Research complete")
                        else:
                            progress_callback("completed", 1.0, "Research complete")

                    return GenerationResult(
                        generator=self.name,
                        task_id=task_id,
                        content=result_data.get("content", ""),
                                input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        metadata={
                            "context": result_data.get("context"),
                            "visited_urls": result_data.get("visited_urls"),
                            "report_type": gptr_config.report_type,
                            "attempts": attempt,
                        },
                        status=TaskStatus.COMPLETED,
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                    )
                except asyncio.TimeoutError:
                    last_error = f"Timed out after {timeout_minutes} minutes"

                    if attempt <= max_retries:
                        logger.warning(f"GPT-R attempt {attempt} timed out, retrying ({max_retries - attempt + 1} retries left)...")
                        if progress_callback:
                            msg = f"Attempt {attempt} timed out, retrying..."
                            if asyncio.iscoroutinefunction(progress_callback):
                                await progress_callback("retrying", 0.0, msg)
                            else:
                                progress_callback("retrying", 0.0, msg)
                        continue

                    logger.error(f"GPT-R all {max_retries + 1} attempts timed out for task {task_id}")
                    break

                except asyncio.CancelledError:
                    logger.info(f"GPT-R task {task_id} was cancelled")
                    if progress_callback:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback("failed", 1.0, "Cancelled")
                        else:
                            progress_callback("failed", 1.0, "Cancelled")
                    return GenerationResult(
                        generator=self.name,
                        task_id=task_id,
                        content="",
                                metadata={"attempts": attempt},
                        status=TaskStatus.CANCELLED,
                        error_message="Task was cancelled",
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                    )

                except Exception as e:
                    last_error = str(e)
                    logger.error("GPT-R attempt %d failed: %s", attempt, type(e).__name__)

                    if progress_callback:
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback("failed", 1.0, f"Error: {last_error[:200]}")
                        else:
                            progress_callback("failed", 1.0, f"Error: {last_error[:200]}")
                    break

            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback("failed", 1.0, last_error or "Unknown error")
                else:
                    progress_callback("failed", 1.0, last_error or "Unknown error")

            return GenerationResult(
                generator=self.name,
                task_id=task_id,
                content="",
                metadata={
                    "timeout_minutes": timeout_minutes,
                    "attempts": attempt,
                },
                status=TaskStatus.FAILED,
                error_message=last_error or f"GPT-R failed after {attempt} attempt(s)",
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        finally:
            if _sidecar_handler is not None:
                for _ln in _gptr_logger_names:
                    logging.getLogger(_ln).removeHandler(_sidecar_handler)
            ENV_OVERRIDES.reset(token)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running research task by cancelling its asyncio.Task."""
        # Check for both direct task_id and attempt variants
        task_ids_to_check = [task_id] + [f"{task_id}-attempt{i}" for i in range(1, 5)]

        cancelled = False
        for tid in task_ids_to_check:
            task = self._active_tasks.pop(tid, None)
            if task and not task.done():
                task.cancel()
                logger.info(f"Cancelled asyncio task {tid}")
                cancelled = True

        return cancelled
