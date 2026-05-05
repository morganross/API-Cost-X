"""
FPF-specific error classes.
"""

from typing import Any, Optional


class FpfError(Exception):
    """Base exception for FPF adapter errors."""
    pass


class FpfExecutionError(FpfError):
    """Raised when FPF execution fails."""

    def __init__(
        self,
        message: str,
        *,
        invariant_failure: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.invariant_failure = dict(invariant_failure) if invariant_failure else None


class FpfTimeoutError(FpfError):
    """Raised when FPF execution times out."""
    pass


class FpfConfigError(FpfError):
    """Raised when FPF configuration is invalid."""
    pass
