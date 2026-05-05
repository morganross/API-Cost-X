"""
Deep Research (DR) Adapter.

This adapter reuses the GPT-Researcher infrastructure but with:
- report_type="deep" (multi-level deep research)
- Additional DR-specific parameters (breadth, depth, etc.)
"""
import logging
from typing import Optional

from app.adapters.base import GenerationConfig, GenerationResult, GeneratorType, ProgressCallback
from app.adapters.gptr.adapter import GptrAdapter

logger = logging.getLogger(__name__)


class DrAdapter(GptrAdapter):
    """
    Deep Research adapter - extends GptrAdapter with deep research report type.

    DR uses the same GPT-Researcher engine but with report_type="deep" which
    enables multi-level research with configurable breadth and depth.
    """

    @property
    def name(self) -> GeneratorType:
        return GeneratorType.DR

    @property
    def display_name(self) -> str:
        return "Deep Research"

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
        Run Deep Research generation.

        Modifies config to use deep report type before delegating to parent.
        """
        # Ensure extra dict exists
        if config.extra is None:
            config.extra = {}

        # Force deep report type for DR
        config.extra["report_type"] = "deep"

        # Map DR-specific parameters if present
        # These come from the dr_config in the run configuration
        if "breadth" in config.extra:
            # GPT-Researcher uses BREADTH env var for deep research
            config.extra.setdefault("env_overrides", {})["BREADTH"] = str(config.extra["breadth"])
        if "depth" in config.extra:
            config.extra.setdefault("env_overrides", {})["DEPTH"] = str(config.extra["depth"])

        # Pass through DR timeout/retry settings to GPTR adapter
        # These are handled by the parent GptrAdapter's GptrConfig
        if "subprocess_timeout_minutes" not in config.extra:
            config.extra["subprocess_timeout_minutes"] = 20  # Default 20 min for DR
        if "subprocess_retries" not in config.extra:
            config.extra["subprocess_retries"] = 1  # Default 1 retry for DR

        logger.info(f"DR adapter: using deep report type with timeout={config.extra.get('subprocess_timeout_minutes')}min, retries={config.extra.get('subprocess_retries')}")

        # Delegate to parent GPTR adapter
        result = await super().generate(
            query=query,
            config=config,
            user_uuid=user_uuid,
            document_content=document_content,
            progress_callback=progress_callback,
            log_writer=log_writer,
        )

        # Update generator type in result to DR
        result.generator = GeneratorType.DR

        return result
