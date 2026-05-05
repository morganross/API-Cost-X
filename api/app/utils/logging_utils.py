import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.log_writer import RunLogWriter

# Register VERBOSE as a real Python logging level (between DEBUG=10 and INFO=20)
VERBOSE = 15
logging.addLevelName(VERBOSE, "VERBOSE")


def get_run_logger(
    run_id: str,
    log_writer: Optional["RunLogWriter"] = None,
    capture_details: bool = True,
) -> logging.Logger:
    """
    Creates a private, non-propagating logger for a specific run.

    When detail capture is enabled, output routes through the sidecar DB via
    SidecarDBHandler. Otherwise the logger remains private and silent.

    Args:
        run_id: The unique identifier for the run.
        log_writer: RunLogWriter for sidecar DB output.
        capture_details: Whether DETAIL entries should be persisted for this run.

    Returns:
        A configured logging.Logger instance that does not bubble up to root.
    """
    # 1. Create a unique logger name. Using 'run.' prefix helps identify them,
    #    but the key is that we will detach it from the parent.
    logger_name = f"run.{run_id}"
    logger = logging.getLogger(logger_name)

    # 2. CRITICAL: Prevent logs from propagating to the root logger (and thus console/other files)
    logger.propagate = False

    # 3. Capture the full internal detail stream when a sidecar handler is attached.
    logger.setLevel(logging.DEBUG)

    # 4. Clear existing handlers to prevent duplicate logging if get_run_logger is called twice
    for h in logger.handlers[:]:
        logger.removeHandler(h)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Sidecar DB handler — routes all per-run log output to logs.db as DETAIL
    if log_writer and capture_details and getattr(log_writer, "save_to_sidecar", True):
        from app.services.log_writer import SidecarDBHandler
        db_handler = SidecarDBHandler(log_writer, source="apicostx")
        db_handler.setLevel(logging.DEBUG)
        db_handler.setFormatter(formatter)
        logger.addHandler(db_handler)

    return logger
