"""
ContextVar-backed os.environ proxy for per-request environment overrides.

This module provides a drop-in replacement for os.environ that overlays
per-request key overrides using a ContextVar.  This allows concurrent
async tasks (e.g., multiple GPT-Researcher runs for different users)
to each see their own API keys without cross-contamination.

Usage:
    from app.infra.env_context import ENV_OVERRIDES, install_os_environ_proxy_once

    # At app startup (FastAPI lifespan):
    install_os_environ_proxy_once()

    # In an adapter's generate():
    overrides = {"OPENAI_API_KEY": user_key, "TAVILY_API_KEY": user_tavily}
    token = ENV_OVERRIDES.set(overrides)
    try:
        # ... run gpt-researcher; all os.environ reads see user's keys ...
    finally:
        ENV_OVERRIDES.reset(token)
"""
from __future__ import annotations

import os
import logging
from collections.abc import MutableMapping, Iterator
from contextvars import ContextVar
from typing import Dict

logger = logging.getLogger(__name__)

ENV_OVERRIDES: ContextVar[Dict[str, str]] = ContextVar(
    "apicostx_env_overrides", default={}
)


class ContextEnvironProxy(MutableMapping):
    """
    Drop-in replacement for os.environ that overlays per-request
    key overrides using a ContextVar.  Reads check the ContextVar
    first, writes go through to the real environment.
    """

    def __init__(self, base: MutableMapping) -> None:
        self._base = base

    def _overrides(self) -> Dict[str, str]:
        return ENV_OVERRIDES.get() or {}

    # --- reads: check overrides first ---

    def __getitem__(self, key: str) -> str:
        o = self._overrides()
        if key in o:
            return str(o[key])
        return self._base[key]

    def get(self, key: str, default=None):
        o = self._overrides()
        if key in o:
            return str(o[key])
        return self._base.get(key, default)

    def __contains__(self, key: object) -> bool:
        """Explicit __contains__ — don't rely on MutableMapping's
        __getitem__-based fallback.  gpt-researcher does
        '"OPENROUTER_LIMIT_RPS" in os.environ' and similar checks."""
        if not isinstance(key, str):
            return False
        return key in self._overrides() or key in self._base

    # --- writes: always go to base (no request-scoped mutation) ---

    def __setitem__(self, key: str, value: str) -> None:
        self._base[key] = value

    def __delitem__(self, key: str) -> None:
        del self._base[key]

    # --- iteration: merge base + overrides ---

    def __iter__(self) -> Iterator[str]:
        o = self._overrides()
        seen: set[str] = set()
        for k in o:
            seen.add(k)
            yield k
        for k in self._base:
            if k not in seen:
                yield k

    def __len__(self) -> int:
        return len(set(self._base.keys()) | set(self._overrides().keys()))

    # --- CRITICAL: copy() must return a plain dict ---
    # MutableMapping does NOT provide copy(). Without this, any call to
    # os.environ.copy() after proxy installation crashes with AttributeError.

    def copy(self) -> Dict[str, str]:
        """Return a plain dict snapshot (base merged with overrides)."""
        d = dict(self._base)
        d.update(self._overrides())
        return d

    # --- items()/keys()/values() for code that iterates os.environ ---
    # gpt-researcher's pubmed_central and custom retrievers do:
    #   for key, value in os.environ.items()

    def items(self):
        o = self._overrides()
        merged = dict(self._base)
        merged.update(o)
        return merged.items()

    def keys(self):
        o = self._overrides()
        merged = dict(self._base)
        merged.update(o)
        return merged.keys()

    def values(self):
        o = self._overrides()
        merged = dict(self._base)
        merged.update(o)
        return merged.values()

    def __repr__(self) -> str:
        n_overrides = len(self._overrides())
        return f"<ContextEnvironProxy base_keys={len(self._base)} overrides={n_overrides}>"


_installed = False


def install_os_environ_proxy_once() -> None:
    """Replace os.environ with a ContextVar-backed proxy (idempotent)."""
    global _installed
    if _installed:
        return
    os.environ = ContextEnvironProxy(os.environ)  # type: ignore[assignment]
    _installed = True
    logger.info("Installed ContextVar-backed os.environ proxy for per-request env overrides")
