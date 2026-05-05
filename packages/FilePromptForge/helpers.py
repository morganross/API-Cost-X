"""
Utility helpers shared by FilePromptForge components.

Provides:
- load_env_file(path: str) -> None
- load_config(path: str) -> dict
- compose_input(file_a: str, file_b: str, prompt_template: Optional[str]) -> str
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import yaml


def load_env_file(path: str) -> None:
    """Populate os.environ with KEY=VALUE entries from a .env file without overwriting existing keys.

    WARNING: This mutates the process-global os.environ. When running in-process
    inside uvicorn (not as a subprocess), this can leak environment variables
    across concurrent requests. Only safe for subprocess or startup use.
    """
    # Guard: skip if running inside uvicorn (in-process mode).
    # The ContextEnvironProxy handles per-request env isolation instead.
    try:
        from contextvars import copy_context
        # If ENV_OVERRIDES ContextVar exists, we're in-process — skip os.environ mutation
        import sys
        if 'app.infra.env_context' in sys.modules:
            import logging
            logging.getLogger('helpers').debug(
                "load_env_file: skipping os.environ mutation (running in-process)"
            )
            return
    except Exception:
        pass

    p = Path(path)
    if not p.exists():
        return

    with p.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('\'"')
            if key not in os.environ:
                os.environ[key] = val


def load_config(path: str) -> Dict:
    """Load YAML configuration from the given path. Returns an empty dict if the file is missing or empty."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def compose_input(file_a: str, file_b: str, prompt_template: Optional[str] = None) -> str:
    """
    Build the prompt body from two input files.

    Order: instructions (file_b) FIRST, then input document (file_a).
    No other content is added - the prompt is purely user-provided.

    If prompt_template is provided it may be either a path to a template file or a literal template string
    containing the placeholders {{file_a}} and {{file_b}}.
    """
    def _read(path: str) -> str:
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception:
            return ""

    a_text = _read(file_a)  # input document
    b_text = _read(file_b)  # instructions

    if prompt_template:
        tpl_path = Path(prompt_template)
        if tpl_path.exists():
            template = tpl_path.read_text(encoding="utf-8")
        else:
            template = str(prompt_template)
        return template.replace("{{file_a}}", a_text).replace("{{file_b}}", b_text)

    # Instructions first, then input document
    return b_text + "\n\n" + a_text
