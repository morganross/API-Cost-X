"""
FPF top-level runner script.
"""

from __future__ import annotations
import argparse
import logging
import sys
import os
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
REPO_ROOT = SCRIPT_DIR.parents[1]

if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))

from file_handler import run as run_handler

LOG_FILENAME = SCRIPT_DIR / "logs" / f"fpf_run_{os.getpid()}.log"
if not LOG_FILENAME.parent.exists():
    LOG_FILENAME.parent.mkdir(parents=True, exist_ok=True)

import yaml


def setup_logging(level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    """Configure root logger. Only safe to call at process startup (CLI mode).

    WARNING: This mutates the root logger by removing all existing handlers.
    Never call this from in-process (uvicorn) mode — it would disrupt all
    concurrent log output.
    """
    # Safety check: skip if we're running inside uvicorn (in-process mode)
    import sys as _sys
    if 'uvicorn' in _sys.modules:
        logging.getLogger('fpf_main').debug(
            "setup_logging: skipping root logger mutation (running inside uvicorn)"
        )
        return

    logger = logging.getLogger()
    logger.setLevel(level)

    for h in list(logger.handlers):
        logger.removeHandler(h)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(ch)

    target_log_file = log_file or LOG_FILENAME
    if not target_log_file.parent.exists():
        target_log_file.parent.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(str(target_log_file), encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(fh)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main(argv: Optional[list[str]] = None) -> int:
    setup_logging(logging.INFO)
    log = logging.getLogger("fpf_main")

    parser = argparse.ArgumentParser(prog="fpf_main", description="File Prompt Forge runner")
    parser.add_argument("--file-a", required=True, dest="file_a")
    parser.add_argument("--file-b", required=True, dest="file_b")
    parser.add_argument("--out", required=True, dest="out")
    parser.add_argument("--config", dest="config")
    parser.add_argument("--env", dest="env")
    parser.add_argument("--provider", required=True, dest="provider")
    parser.add_argument("--model", required=True, dest="model")
    parser.add_argument("--reasoning-effort", dest="reasoning_effort")
    parser.add_argument("--max-completion-tokens", dest="max_completion_tokens", type=int)
    parser.add_argument("--timeout", required=False, dest="timeout", type=int)
    parser.add_argument("--fpf-max-retries", dest="fpf_max_retries", type=int, default=3)
    parser.add_argument("--fpf-retry-delay", dest="fpf_retry_delay", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--log-file", dest="log_file")

    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_file_path = Path(args.log_file) if args.log_file else None
    setup_logging(log_level, log_file_path)

    log.info("Starting FPF runner")

    config_path = args.config or str(PROJECT_ROOT / "fpf_config.yaml")
    cfg = load_config(config_path)

    file_a = args.file_a
    file_b = args.file_b
    config = config_path
    env = args.env or str(REPO_ROOT / ".env")
    out = args.out

    model = args.model
    reasoning_effort = args.reasoning_effort
    max_completion_tokens = args.max_completion_tokens
    timeout = args.timeout
    fpf_max_retries = args.fpf_max_retries
    fpf_retry_delay = args.fpf_retry_delay
    provider = args.provider
    request_json = args.json

    log.info("Starting FPF execution...")

    result_path = run_handler(
        file_a=file_a,
        file_b=file_b,
        out_path=out,
        config_path=config,
        env_path=env,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        max_completion_tokens=max_completion_tokens,
        timeout=timeout,
        fpf_max_retries=fpf_max_retries,
        fpf_retry_delay=fpf_retry_delay,
        request_json=request_json,
    )

    log.info("Run completed. Output written to %s", result_path)
    print(result_path)
    sys.stdout.flush()
    sys.stderr.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
