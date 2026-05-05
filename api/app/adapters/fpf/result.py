"""
FPF-specific result types.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class FpfExecutionResult:
    """Result from FPF execution."""

    success: bool
    content: str
    raw_response: Optional[Dict[str, Any]] = None
    reasoning: Optional[str] = None
    sources: List[Dict[str, Any]] = None
    execution_time: float = 0.0
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.sources is None:
            self.sources = []
