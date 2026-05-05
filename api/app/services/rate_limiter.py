"""
Provider-Aware Rate Limiter.

Implements per-provider delay enforcement to prevent hammering LLM APIs.
Each provider can have different min_delay_seconds settings.

Concurrency is controlled globally by the preset's generation_concurrency
setting (in run_executor.py), NOT here. This module only handles:
- min_delay_seconds: Minimum delay between requests per provider

This module provides a centralized way to add delays between API calls,
ensuring we don't spam providers even when running many concurrent tasks.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a single provider's rate limits."""

    name: str
    min_delay_seconds: float = 0.0  # Min seconds between requests

    # Tracking state (not config, but easier to keep here)
    last_request_time: float = 0.0  # Unix timestamp of last request start

    def __post_init__(self):
        if self.min_delay_seconds < 0:
            raise ValueError(f"min_delay_seconds must be >= 0, got {self.min_delay_seconds}")


@dataclass
class ProviderRateLimiter:
    """
    Rate limiter for a single provider.

    Uses timestamp tracking for delay enforcement between requests.
    Concurrency is handled globally by the preset's generation_concurrency.
    """

    config: ProviderConfig
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        """
        Acquire permission to make a request.

        This will:
        1. Wait for min_delay since last request (rate limiting)
        2. Update tracking state
        """
        # Enforce minimum delay between requests
        async with self._lock:
            now = time.time()
            elapsed = now - self.config.last_request_time

            if elapsed < self.config.min_delay_seconds:
                wait_time = self.config.min_delay_seconds - elapsed
                logger.info(
                    f"[RATE-LIMIT] Provider {self.config.name}: "
                    f"Waiting {wait_time:.1f}s (min_delay={self.config.min_delay_seconds}s, "
                    f"elapsed={elapsed:.1f}s)"
                )

            # Calculate wait time needed
            wait_time_needed = max(0, self.config.min_delay_seconds - elapsed)

        # Sleep outside lock if needed
        if wait_time_needed > 0:
            await asyncio.sleep(wait_time_needed)

        # Update tracking state
        async with self._lock:
            self.config.last_request_time = time.time()
            logger.info(
                f"[RATE-LIMIT] Provider {self.config.name}: ACQUIRED (delay enforced)"
            )

    def release(self) -> None:
        """Release after request completion (no-op, kept for API compatibility)."""
        logger.debug(
            f"[RATE-LIMIT] Provider {self.config.name}: RELEASED"
        )


class ProviderRegistry:
    """
    Singleton registry of all provider rate limiters.

    Provides a central place to manage per-provider delay settings.
    Concurrency is controlled by the preset's generation_concurrency.
    """

    _instance: Optional["ProviderRegistry"] = None
    _lock: asyncio.Lock = None

    # All rate limit settings MUST come from preset config - no hardcoded defaults
    # Preset must specify min_delay_seconds for each provider

    def __init__(self):
        self._limiters: Dict[str, ProviderRateLimiter] = {}
        self._registry_lock = asyncio.Lock()
        self._config: Dict[str, Dict] = {}  # Must be set via configure()

    def configure(self, provider_configs: Dict[str, Dict]) -> None:
        """
        Configure rate limits from preset. REQUIRED before use.

        provider_configs format:
        {
            "anthropic": {"min_delay_seconds": 1.0},
            "openai": {"min_delay_seconds": 0.5},
            ...
        }
        """
        if not provider_configs:
            raise ValueError("provider_configs is required - no fallback defaults allowed")
        self._config = provider_configs

    @classmethod
    async def get_instance(cls) -> "ProviderRegistry":
        """Get or create the singleton instance."""
        if cls._instance is None:
            if cls._lock is None:
                cls._lock = asyncio.Lock()
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance._initialize()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing)."""
        cls._instance = None

    async def _initialize(self) -> None:
        """Initialize provider limiters from configured values or defaults."""
        # Use sensible defaults if no config provided
        if not self._config:
            logger.info("[RATE-LIMIT] No config provided, using default rate limits")
            self._config = {
                "anthropic": {"min_delay_seconds": 0.5},
                "openai": {"min_delay_seconds": 0.5},
                "google": {"min_delay_seconds": 0.5},
                "openrouter": {"min_delay_seconds": 0.5},
                "custom": {"min_delay_seconds": 0.5},
            }
        for provider_name, config_dict in self._config.items():
            if "min_delay_seconds" not in config_dict:
                raise ValueError(f"Provider '{provider_name}' missing required 'min_delay_seconds' - no fallback defaults allowed")
            config = ProviderConfig(
                name=provider_name,
                min_delay_seconds=config_dict["min_delay_seconds"],
            )
            self._limiters[provider_name] = ProviderRateLimiter(config)

        logger.info(
            f"[RATE-LIMIT] Initialized ProviderRegistry with {len(self._limiters)} providers: "
            f"{list(self._limiters.keys())} (delay-only mode, concurrency controlled by preset)"
        )

    async def get_limiter(self, provider: str) -> ProviderRateLimiter:
        """
        Get the rate limiter for a provider.

        If provider not configured, creates one with default rate limit.
        """
        provider_lower = provider.lower()

        async with self._registry_lock:
            if provider_lower not in self._limiters:
                # Create a default limiter for unknown provider
                logger.info(f"[RATE-LIMIT] Creating default limiter for unknown provider: {provider}")
                config = ProviderConfig(
                    name=provider_lower,
                    min_delay_seconds=0.5,  # Default: 0.5s between requests
                )
                self._limiters[provider_lower] = ProviderRateLimiter(config)

            return self._limiters[provider_lower]

    def update_limits(self, provider: str, max_concurrent: Optional[int] = None,
                      min_delay_seconds: Optional[float] = None) -> None:
        """
        Update rate limits for a provider dynamically.

        This can be used to adjust limits based on:
        - User settings from the UI
        - Adaptive learning from 429 responses
        """
        provider_lower = provider.lower()

        if provider_lower not in self._limiters:
            logger.warning(f"[RATE-LIMIT] Cannot update limits for unknown provider: {provider}")
            return

        limiter = self._limiters[provider_lower]

        if max_concurrent is not None:
            # Note: Changing semaphore size at runtime is tricky
            # For now, just update the config - will take effect on next acquire
            limiter.config.max_concurrent = max_concurrent
            # Create new semaphore with updated size
            limiter._semaphore = asyncio.Semaphore(max_concurrent)
            logger.info(f"[RATE-LIMIT] Updated {provider} max_concurrent to {max_concurrent}")

        if min_delay_seconds is not None:
            limiter.config.min_delay_seconds = min_delay_seconds
            logger.info(f"[RATE-LIMIT] Updated {provider} min_delay to {min_delay_seconds}s")

    def get_all_stats(self) -> Dict[str, Dict]:
        """Get current stats for all providers."""
        return {
            name: {
                "max_concurrent": limiter.config.max_concurrent,
                "min_delay_seconds": limiter.config.min_delay_seconds,
                "in_flight": limiter.in_flight_count,
                "last_request_time": limiter.config.last_request_time,
            }
            for name, limiter in self._limiters.items()
        }


# Convenience context manager for use in code
class RateLimitedRequest:
    """
    Context manager for rate-limited requests.

    Usage:
        async with RateLimitedRequest("anthropic"):
            # Make your API call here
            result = await fpf_adapter.generate(...)
    """

    def __init__(self, provider: str):
        self.provider = provider
        self._limiter: Optional[ProviderRateLimiter] = None

    async def __aenter__(self):
        registry = await ProviderRegistry.get_instance()
        self._limiter = await registry.get_limiter(self.provider)
        await self._limiter.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._limiter:
            self._limiter.release()
        return False  # Don't suppress exceptions
