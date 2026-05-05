"""
error_classifier.py - Intelligent error classification for retry strategies

Classifies errors into categories with different retry behaviors:
- Validation errors: Retry with prompt enhancement
- Transient errors: Retry with exponential backoff
- Permanent errors: No retry
"""

from __future__ import annotations
from typing import Optional, Dict, Any
from enum import Enum
import logging

LOG = logging.getLogger("error_classifier")


class ErrorCategory(Enum):
    """Error categories with different retry strategies."""
    VALIDATION_GROUNDING = "validation_grounding"  # Missing grounding/citations
    VALIDATION_REASONING = "validation_reasoning"  # Missing reasoning/rationale
    VALIDATION_BOTH = "validation_both"  # Missing both
    TRANSIENT_NETWORK = "transient_network"  # Network timeouts, connection errors
    TRANSIENT_RATE_LIMIT = "transient_rate_limit"  # Rate limiting (429, quota)
    TRANSIENT_SERVER = "transient_server"  # Server errors (502, 503, 504)
    PERMANENT_AUTH = "permanent_auth"  # Authentication failures
    PERMANENT_INVALID_REQUEST = "permanent_invalid_request"  # Bad request (400)
    PERMANENT_NOT_FOUND = "permanent_not_found"  # Resource not found (404)
    PERMANENT_FORBIDDEN = "permanent_forbidden"  # Permission denied (403)
    PERMANENT_OTHER = "permanent_other"  # Unknown permanent errors
    UNKNOWN = "unknown"  # Cannot classify


class RetryStrategy:
    """Retry strategy configuration for each error category."""

    def __init__(
        self,
        max_retries: int,
        base_delay_ms: int,
        max_delay_ms: int,
        backoff_multiplier: float = 2.0,
        jitter: bool = True,
        prompt_enhancement: bool = False,
    ):
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_delay_ms = max_delay_ms
        self.backoff_multiplier = backoff_multiplier
        self.jitter = jitter
        self.prompt_enhancement = prompt_enhancement


# Default retry strategies per error category
DEFAULT_RETRY_STRATEGIES: Dict[ErrorCategory, RetryStrategy] = {
    # Validation errors: 2 retries with prompt enrichment
    ErrorCategory.VALIDATION_GROUNDING: RetryStrategy(
        max_retries=2,
        base_delay_ms=1000,
        max_delay_ms=5000,
        prompt_enhancement=True,
    ),
    ErrorCategory.VALIDATION_REASONING: RetryStrategy(
        max_retries=2,
        base_delay_ms=1000,
        max_delay_ms=5000,
        prompt_enhancement=True,
    ),
    ErrorCategory.VALIDATION_BOTH: RetryStrategy(
        max_retries=2,
        base_delay_ms=1000,
        max_delay_ms=5000,
        prompt_enhancement=True,
    ),
    # Transient errors: 3-5 retries with exponential backoff
    ErrorCategory.TRANSIENT_NETWORK: RetryStrategy(
        max_retries=3,
        base_delay_ms=500,
        max_delay_ms=30000,
        backoff_multiplier=2.0,
    ),
    ErrorCategory.TRANSIENT_RATE_LIMIT: RetryStrategy(
        max_retries=5,
        base_delay_ms=2000,
        max_delay_ms=120000,
        backoff_multiplier=3.0,  # Aggressive backoff for rate limits
    ),
    ErrorCategory.TRANSIENT_SERVER: RetryStrategy(
        max_retries=4,
        base_delay_ms=1000,
        max_delay_ms=60000,
        backoff_multiplier=2.0,
    ),
    # Permanent errors: 0 retries
    ErrorCategory.PERMANENT_AUTH: RetryStrategy(
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    ),
    ErrorCategory.PERMANENT_INVALID_REQUEST: RetryStrategy(
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    ),
    ErrorCategory.PERMANENT_NOT_FOUND: RetryStrategy(
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    ),
    ErrorCategory.PERMANENT_FORBIDDEN: RetryStrategy(
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    ),
    ErrorCategory.PERMANENT_OTHER: RetryStrategy(
        max_retries=0,
        base_delay_ms=0,
        max_delay_ms=0,
    ),
    # Unknown: conservative retry
    ErrorCategory.UNKNOWN: RetryStrategy(
        max_retries=1,
        base_delay_ms=2000,
        max_delay_ms=10000,
    ),
}


def classify_error(exc: Exception, stderr_text: Optional[str] = None) -> ErrorCategory:
    """
    Classify an error into a category for intelligent retry logic.

    Args:
        exc: The exception raised
        stderr_text: Optional stderr output from subprocess (for validation errors)

    Returns:
        ErrorCategory enum value
    """
    error_msg = str(exc).lower()
    stderr = (stderr_text or "").lower()
    combined = f"{error_msg} {stderr}"

    # Validation errors (highest priority - most specific)
    if any(keyword in combined for keyword in [
        "missing grounding",
        "no provider-side grounding detected",
        "refusing to write output",
    ]):
        if any(keyword in combined for keyword in ["missing reasoning", "missing rationale"]):
            LOG.info("Classified error as VALIDATION_BOTH")
            return ErrorCategory.VALIDATION_BOTH
        LOG.info("Classified error as VALIDATION_GROUNDING")
        return ErrorCategory.VALIDATION_GROUNDING

    if any(keyword in combined for keyword in [
        "missing reasoning",
        "missing rationale",
        "no reasoning detected",
    ]):
        LOG.info("Classified error as VALIDATION_REASONING")
        return ErrorCategory.VALIDATION_REASONING

    # Rate limiting
    if any(keyword in combined for keyword in [
        "429",
        "rate limit",
        "quota exceeded",
        "too many requests",
        "throttled",
    ]):
        LOG.info("Classified error as TRANSIENT_RATE_LIMIT")
        return ErrorCategory.TRANSIENT_RATE_LIMIT

    # Network timeouts
    if any(keyword in combined for keyword in [
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "network error",
        "socket",
    ]):
        LOG.info("Classified error as TRANSIENT_NETWORK")
        return ErrorCategory.TRANSIENT_NETWORK

    # Server errors
    if any(keyword in combined for keyword in [
        "502",
        "503",
        "504",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "server error",
        "internal server error",
        "500",
    ]):
        LOG.info("Classified error as TRANSIENT_SERVER")
        return ErrorCategory.TRANSIENT_SERVER

    # Authentication errors
    if any(keyword in combined for keyword in [
        "401",
        "unauthorized",
        "authentication failed",
        "invalid api key",
        "api key not found",
        "invalid token",
    ]):
        LOG.info("Classified error as PERMANENT_AUTH")
        return ErrorCategory.PERMANENT_AUTH

    # Bad request
    if any(keyword in combined for keyword in [
        "400",
        "bad request",
        "invalid request",
        "malformed",
    ]):
        LOG.info("Classified error as PERMANENT_INVALID_REQUEST")
        return ErrorCategory.PERMANENT_INVALID_REQUEST

    # Not found
    if any(keyword in combined for keyword in [
        "404",
        "not found",
        "does not exist",
    ]):
        LOG.info("Classified error as PERMANENT_NOT_FOUND")
        return ErrorCategory.PERMANENT_NOT_FOUND

    # Forbidden
    if any(keyword in combined for keyword in [
        "403",
        "forbidden",
        "access denied",
        "permission denied",
    ]):
        LOG.info("Classified error as PERMANENT_FORBIDDEN")
        return ErrorCategory.PERMANENT_FORBIDDEN

    # Unknown - could be permanent or transient
    LOG.warning("Could not classify error, defaulting to UNKNOWN: %s", error_msg[:200])
    return ErrorCategory.UNKNOWN


def get_retry_strategy(category: ErrorCategory) -> RetryStrategy:
    """Get the retry strategy for a given error category."""
    return DEFAULT_RETRY_STRATEGIES.get(category, DEFAULT_RETRY_STRATEGIES[ErrorCategory.UNKNOWN])


def should_retry(category: ErrorCategory, attempt: int) -> bool:
    """
    Determine if a retry should be attempted based on error category and attempt number.

    Args:
        category: The error category
        attempt: The current attempt number (1-indexed)

    Returns:
        True if retry should be attempted, False otherwise
    """
    strategy = get_retry_strategy(category)
    should = attempt <= strategy.max_retries
    LOG.debug("should_retry(category=%s, attempt=%d) = %s (max_retries=%d)",
              category, attempt, should, strategy.max_retries)
    return should


def calculate_backoff_delay(category: ErrorCategory, attempt: int) -> int:
    """
    Calculate the delay in milliseconds for the current retry attempt.

    Args:
        category: The error category
        attempt: The current attempt number (1-indexed)

    Returns:
        Delay in milliseconds
    """
    import random

    strategy = get_retry_strategy(category)

    # Exponential backoff: base * (multiplier ^ (attempt - 1))
    delay = strategy.base_delay_ms * (strategy.backoff_multiplier ** (attempt - 1))

    # Add jitter if enabled (±25% random variance) BEFORE capping
    if strategy.jitter:
        jitter_range = delay * 0.25
        delay += random.uniform(-jitter_range, jitter_range)

    # Cap at max delay (after jitter to ensure we don't exceed max)
    delay = min(delay, strategy.max_delay_ms)

    return int(max(0, delay))
