import logging
from typing import List, Optional

from app.adapters.base import BaseAdapter, GenerationConfig, GenerationResult, GeneratorType
from app.adapters.combine.config import CombineConfig

logger = logging.getLogger(__name__)

class CombineAdapter:
    """
    Adapter for the Combine Phase.

    This adapter orchestrates the combination of multiple reports into a single 'Gold Standard' report.
    It uses an underlying generator (typically FPF) to perform the actual text generation.
    """

    def __init__(self, generator: BaseAdapter):
        """
        Initialize with a generator adapter.

        Args:
            generator: The generator adapter to use (e.g., FpfAdapter)
        """
        self.generator = generator

    async def combine(
        self,
        reports: List[str],
        instructions: str,
        config: GenerationConfig,
        user_uuid: str,
        original_instructions: Optional[str] = None
    ) -> GenerationResult:
        """
        Combine multiple reports into one.

        Args:
            reports: List of report content strings to combine
            instructions: The specific instructions for combination
            config: Generation configuration (model, provider, etc.)
            user_uuid: User UUID for fetching provider API keys from the root .env
            original_instructions: Optional original query/instructions to include in context

        Returns:
            GenerationResult containing the combined report
        """
        if len(reports) < 2:
            logger.warning("Combine requested with fewer than 2 reports.")
            # We could just return the single report, or fail.
            # For now, let's proceed but log it.

        # 1. Construct the Context (Document Content)
        # Format:
        # --- ORIGINAL INSTRUCTIONS ---
        # ...
        # --- REPORT 1 ---
        # ...
        # --- REPORT 2 ---
        # ...

        context_parts = []

        if original_instructions:
            context_parts.append(f"--- ORIGINAL INSTRUCTIONS ---\n{original_instructions}\n")

        for i, report in enumerate(reports):
            context_parts.append(f"--- REPORT {i+1} ---\n{report}\n")

        context_parts.append("--- END OF INPUTS ---")

        full_context = "\n".join(context_parts)

        # 2. Run Generation
        # We use the provided generator (FPF/GPTR) to generate the combined report
        # The 'query' is the combine instructions

        logger.info(f"Starting combination of {len(reports)} reports using {self.generator.name}...")

        result = await self.generator.generate(
            query=instructions,
            config=config,
            user_uuid=user_uuid,
            document_content=full_context
        )

        return result
