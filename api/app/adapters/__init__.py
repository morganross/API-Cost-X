"""
APICostX Adapters - Wrappers for content generators.

Each adapter provides a standardized interface for generating research content:
- FPF (FilePromptForge) - Template-based document generation
- GPTR (GPT-Researcher) - Autonomous web research
- AIQ (NVIDIA AI-Q) - Service-backed deep research
- DR (Deep Research) - Future
- MA (Multi-Agent) - Future
"""
from .base import (
    BaseAdapter,
    GenerationConfig,
    GenerationResult,
    GeneratorType,
    ProgressCallback,
    TaskStatus,
)
from .fpf import FpfAdapter, FpfConfig
from .gptr import GptrAdapter, GptrConfig
from .aiq import AiqAdapter
from .combine import CombineAdapter, CombineConfig

__all__ = [
    # Base
    "BaseAdapter",
    "GenerationConfig",
    "GenerationResult",
    "GeneratorType",
    "ProgressCallback",
    "TaskStatus",
    # FPF
    "FpfAdapter",
    "FpfConfig",
    # GPTR
    "GptrAdapter",
    "GptrConfig",
    "AiqAdapter",
    "ReportType",
    "ReportSource",
    "Tone",
]


def get_adapter(generator_type: GeneratorType) -> BaseAdapter:
    """
    Factory to get the appropriate adapter for a generator type.

    Args:
        generator_type: The type of generator needed

    Returns:
        An instance of the appropriate adapter

    Raises:
        ValueError: If generator type is not supported
    """
    adapters = {
        GeneratorType.FPF: FpfAdapter,
        GeneratorType.GPTR: GptrAdapter,
        GeneratorType.AIQ: AiqAdapter,
    }

    adapter_class = adapters.get(generator_type)
    if not adapter_class:
        raise ValueError(f"Unsupported generator type: {generator_type}")

    return adapter_class()
