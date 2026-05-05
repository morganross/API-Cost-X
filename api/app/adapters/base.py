"""
Base adapter interface for all generators (FPF, GPTR, etc.)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional


class GeneratorType(str, Enum):
    """Types of content generators."""
    FPF = "fpf"      # FilePromptForge
    GPTR = "gptr"    # GPT-Researcher
    DR = "dr"        # Deep Research (future)
    MA = "ma"        # Multi-Agent (future)
    AIQ = "aiq"      # NVIDIA AI-Q


class TaskStatus(str, Enum):
    """Status of a generation task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GenerationResult:
    """Standardized result from any generator."""
    # Identity
    generator: GeneratorType
    task_id: str

    # Content
    content: str
    content_type: str = "markdown"  # markdown, html, json

    # Metadata
    model: str = ""
    provider: str = ""

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    # Token counters
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Generator-specific data
    sources: list[dict[str, Any]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Status
    status: TaskStatus = TaskStatus.COMPLETED
    error_message: Optional[str] = None


@dataclass
class GenerationConfig:
    """Base configuration for all generators."""
    # LLM settings - REQUIRED, must be provided by caller
    provider: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4000

    # Extra settings (generator-specific)
    extra: dict[str, Any] = field(default_factory=dict)


# Type alias for progress callbacks
ProgressCallback = Callable[[str, float, Optional[str]], None]
# callback(stage: str, progress: float 0-1, message: Optional[str])


class BaseAdapter(ABC):
    """
    Abstract base class for all content generators.

    All adapters (FPF, GPTR, etc.) implement this interface to provide
    consistent behavior across different generation providers.
    """

    @property
    @abstractmethod
    def name(self) -> GeneratorType:
        """Return the generator type identifier."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        pass

    @abstractmethod
    async def generate(
        self,
        query: str,
        config: GenerationConfig,
        *,
        user_uuid: str,
        document_content: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> GenerationResult:
        """
        Execute a generation task.

        Args:
            query: The research question or prompt
            config: Generator configuration
            user_uuid: User UUID for fetching provider API keys from the root .env
            document_content: Optional source document content (for FPF)
            progress_callback: Optional callback for progress updates

        Returns:
            GenerationResult with content and metadata
        """
        pass

    @abstractmethod
    async def cancel(self, task_id: str) -> bool:
        """
        Cancel a running generation task.

        Args:
            task_id: The task to cancel

        Returns:
            True if cancellation was successful
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the adapter is properly configured and ready.

        Returns:
            True if ready to generate
        """
        pass

    def validate_config(self, config: GenerationConfig) -> list[str]:
        """
        Validate configuration before running.

        Args:
            config: The configuration to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        if not config.provider:
            errors.append("Provider is required")
        if not config.model:
            errors.append("Model is required")
        if config.temperature < 0 or config.temperature > 2:
            errors.append("Temperature must be between 0 and 2")
        return errors
