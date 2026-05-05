"""
Run log writer — writes EVENT and DETAIL entries to the local sidecar logs.db.

EVENT entries are also mirrored to the admin-safe server log. DETAIL entries
are only persisted when user run-log saving is enabled for the run.

Usage:
    log_writer = RunLogWriter(user_uuid, run_id, save_to_sidecar=True)
    await log_writer.event("apicostx", "INFO", "run_start", "Run started: model=gemini-2.0")
    await log_writer.detail("fpf", "DEBUG", "FPF response", payload={"tokens": 1234})
    await log_writer.close()
"""
import asyncio
import json
import logging
from contextvars import ContextVar
from concurrent.futures import Future as ConcurrentFuture
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.infra.db.log_session import (
    _get_or_create_user_log_engine,
    _user_log_session_factories,
)

logger = logging.getLogger(__name__)

# Admin-visible event mirror — propagates to root → server log / journalctl
_admin_logger = logging.getLogger("apicostx.events")

# Tag the current execution context with a run_id so each SidecarLogHandler
# only captures log records that belong to its own run.
_current_capture_id: ContextVar[Optional[str]] = ContextVar("_current_capture_id", default=None)


class RunLogWriter:
    """Writes log entries to a user's sidecar logs.db with buffering."""

    def __init__(self, user_uuid: str, run_id: str, save_to_sidecar: bool = True):
        self.user_uuid = user_uuid
        self.run_id = run_id
        self.save_to_sidecar = bool(save_to_sidecar)
        self._buffer: list[dict[str, Any]] = []
        self._buffer_limit = 20
        self._flush_lock = asyncio.Lock()
        self._pending_futures: set[ConcurrentFuture[Any]] = set()
        self._closed = False

    async def _ensure_engine(self):
        await _get_or_create_user_log_engine(self.user_uuid)

    def _normalize_event_message(self, message: str) -> str:
        normalized = " ".join(str(message).split())
        if len(normalized) > 500:
            normalized = normalized[:500] + "...[truncated]"
        return normalized

    def _serialize_payload(self, payload: Any) -> Optional[str]:
        if payload is None:
            return None
        try:
            return json.dumps(payload, default=str)
        except Exception:
            logger.warning(
                "Failed to serialize DETAIL payload for run %s; falling back to repr",
                self.run_id[:8],
            )
            fallback = {
                "payload_type": type(payload).__name__,
                "payload_repr": repr(payload)[:2000],
            }
            return json.dumps(fallback, default=str)

    def track_future(self, future: ConcurrentFuture[Any]) -> None:
        self._pending_futures.add(future)
        future.add_done_callback(self._pending_futures.discard)

    async def _await_pending_futures(self) -> None:
        while self._pending_futures:
            pending = [asyncio.wrap_future(future) for future in list(self._pending_futures)]
            results = await asyncio.gather(*pending, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "Scheduled sidecar log write failed for run %s: %s",
                        self.run_id[:8],
                        type(result).__name__,
                    )

    async def event(
        self,
        source: str,
        level: str,
        event_type: str,
        message: str,
    ):
        """
        Write an EVENT entry (admin-safe, no user content).

        Also mirrors to the admin server log via apicostx.events logger.
        """
        if self._closed:
            return
        safe_message = self._normalize_event_message(message)
        if self.save_to_sidecar:
            await self._write("EVENT", source, level, event_type, safe_message, payload=None)
        # Mirror to admin server log
        log_level = getattr(logging, level.upper(), logging.INFO)
        _admin_logger.log(
            log_level, "[%s:%s] %s", source.upper(), event_type, safe_message
        )

    async def detail(
        self,
        source: str,
        level: str,
        message: str,
        payload: Optional[dict] = None,
        event_type: Optional[str] = None,
    ):
        """Write a DETAIL entry (user-only, may contain payloads)."""
        if self._closed or not self.save_to_sidecar:
            return
        payload_json = self._serialize_payload(payload)
        await self._write("DETAIL", source, level, event_type, message, payload_json)

    async def _write(self, classification, source, level, event_type, message, payload):
        if self._closed or not self.save_to_sidecar:
            return
        self._buffer.append({
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "classification": classification,
            "source": source,
            "level": level,
            "event_type": event_type,
            "message": message,
            "payload": payload,
        })
        if len(self._buffer) >= self._buffer_limit:
            await self.flush()

    async def flush(self):
        """Write buffered entries to DB."""
        async with self._flush_lock:
            if not self.save_to_sidecar or not self._buffer:
                return
            await self._ensure_engine()
            factory = _user_log_session_factories[self.user_uuid]
            while self._buffer:
                entries = list(self._buffer)
                try:
                    async with factory() as session:
                        for entry in entries:
                            await session.execute(
                                text(
                                    "INSERT INTO log_entries"
                                    " (run_id, timestamp, classification, source, level,"
                                    "  event_type, message, payload)"
                                    " VALUES (:run_id, :timestamp, :classification, :source,"
                                    "  :level, :event_type, :message, :payload)"
                                ),
                                entry,
                            )
                        await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to flush %d log entries for run %s",
                        len(entries),
                        self.run_id[:8],
                    )
                    return
                del self._buffer[:len(entries)]

    async def close(self):
        """Flush remaining buffer. Call at run completion."""
        if self._closed:
            return
        if self.save_to_sidecar:
            await self._await_pending_futures()
            await self.flush()
        self._closed = True


class SidecarLogHandler(logging.Handler):
    """
    Bridges Python logging → sidecar DB via RunLogWriter.

    Attaches to third-party loggers (GPTR, FPF, etc.) during adapter
    execution to capture their output as DETAIL entries.

    Thread-safe: uses ``asyncio.run_coroutine_threadsafe`` so it works from
    both the event-loop thread and worker threads (FPF runs via
    ``asyncio.to_thread``).
    """

    def __init__(self, log_writer: "RunLogWriter", source: str = "ext"):
        super().__init__()
        self.log_writer = log_writer
        self.source = source
        self._capture_id = log_writer.run_id
        self._loop = asyncio.get_running_loop()

    def emit(self, record: logging.LogRecord):
        try:
            # Only capture records that belong to this run
            if _current_capture_id.get() != self._capture_id:
                return
            msg = self.format(record)
            # Truncate excessively long messages (e.g. scraped HTML)
            if len(msg) > 2000:
                msg = msg[:2000] + "...[truncated]"
            level = record.levelname
            coro = self.log_writer.detail(self.source, level, msg)
            if self._loop.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self.log_writer.track_future(future)
        except Exception:
            # Never let handler errors crash the caller
            self.handleError(record)


class SidecarDBHandler(logging.Handler):
    """
    Bridges the per-run logger (``run.<run_id>``) → sidecar DB via RunLogWriter.

    Unlike SidecarLogHandler (which attaches to third-party loggers),
    this handler attaches to the APICostX run logger so that all
    ``self.logger.info/debug/error`` calls in RunExecutor automatically
    appear as DETAIL entries in the sidecar DB.
    """

    def __init__(self, log_writer: "RunLogWriter", source: str = "apicostx"):
        super().__init__()
        self.log_writer = log_writer
        self.source = source
        self._loop = asyncio.get_running_loop()

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            if len(msg) > 2000:
                msg = msg[:2000] + "...[truncated]"
            level = record.levelname
            coro = self.log_writer.detail(self.source, level, msg)
            if self._loop.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self.log_writer.track_future(future)
        except Exception:
            self.handleError(record)
