"""
Run log reader — reads EVENT and DETAIL entries from the local sidecar logs.db.

Used by the log-serving API endpoint and the "serve run logs" feature.
"""
from typing import Optional

from sqlalchemy import text

from app.infra.db.log_session import (
    _get_or_create_user_log_engine,
    _user_log_session_factories,
)


class RunLogReader:
    """Reads log entries from a user's sidecar logs.db."""

    def __init__(self, user_uuid: str):
        self.user_uuid = user_uuid

    async def get_run_logs(
        self,
        run_id: str,
        classification: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        after_id: Optional[int] = None,
        include_payload: bool = False,
        payload_preview_chars: int = 240,
    ) -> dict:
        """
        Read log entries for a run, with pagination and optional classification filter.

        Args:
            run_id: The run UUID.
            classification: "EVENT", "DETAIL", or None for all.
            offset: Pagination offset. Ignored when after_id is provided.
            limit: Max entries per page.
            after_id: Optional cursor; return rows with id greater than this value.
            include_payload: Include full payload bodies. Keep false for hot timeline polling.
            payload_preview_chars: Max payload preview chars when include_payload is false.

        Returns:
            Dict with run_id, total count, offset, and entries list.
        """
        await _get_or_create_user_log_engine(self.user_uuid)
        factory = _user_log_session_factories[self.user_uuid]

        async with factory() as session:
            # Count total matching rows
            count_sql = "SELECT COUNT(*) FROM log_entries WHERE run_id = :run_id"
            cursor_id = max(0, int(after_id or 0))
            params: dict = {"run_id": run_id, "offset": offset, "limit": limit}
            if classification:
                count_sql += " AND classification = :classification"
                params["classification"] = classification

            total = (await session.execute(text(count_sql), params)).scalar()

            # Fetch page
            if include_payload:
                fetch_sql = (
                    "SELECT id, run_id, timestamp, classification, source, level,"
                    "       event_type, message, payload,"
                    "       CASE WHEN payload IS NULL OR payload = '' THEN 0 ELSE 1 END AS has_payload,"
                    "       LENGTH(payload) AS payload_size,"
                    "       NULL AS payload_preview"
                    " FROM log_entries"
                    " WHERE run_id = :run_id"
                )
            else:
                fetch_sql = (
                    "SELECT id, run_id, timestamp, classification, source, level,"
                    "       event_type, message,"
                    "       NULL AS payload,"
                    "       CASE WHEN payload IS NULL OR payload = '' THEN 0 ELSE 1 END AS has_payload,"
                    "       LENGTH(payload) AS payload_size,"
                    "       SUBSTR(payload, 1, :payload_preview_chars) AS payload_preview"
                    " FROM log_entries"
                    " WHERE run_id = :run_id"
                )
                params["payload_preview_chars"] = max(0, int(payload_preview_chars))
            if classification:
                fetch_sql += " AND classification = :classification"
            if cursor_id > 0:
                fetch_sql += " AND id > :after_id"
                params["after_id"] = cursor_id
                params["offset"] = 0
            fetch_sql += " ORDER BY id ASC LIMIT :limit OFFSET :offset"

            rows = (await session.execute(text(fetch_sql), params)).fetchall()

            return {
                "run_id": run_id,
                "total": total,
                "offset": offset,
                "after_id": cursor_id or None,
                "entries": [
                    {
                        "id": row.id,
                        "timestamp": row.timestamp,
                        "classification": row.classification,
                        "source": row.source,
                        "level": row.level,
                        "event_type": row.event_type,
                        "message": row.message,
                        "payload": row.payload,
                        "has_payload": bool(row.has_payload),
                        "payload_size": int(row.payload_size or 0),
                        "payload_preview": row.payload_preview,
                    }
                    for row in rows
                ],
            }

    async def get_log_entry(
        self,
        run_id: str,
        entry_id: int,
    ) -> Optional[dict]:
        """Read one full log entry, including payload, for lazy detail display."""
        await _get_or_create_user_log_engine(self.user_uuid)
        factory = _user_log_session_factories[self.user_uuid]

        async with factory() as session:
            sql = (
                "SELECT id, run_id, timestamp, classification, source, level,"
                "       event_type, message, payload,"
                "       CASE WHEN payload IS NULL OR payload = '' THEN 0 ELSE 1 END AS has_payload,"
                "       LENGTH(payload) AS payload_size"
                " FROM log_entries"
                " WHERE run_id = :run_id AND id = :entry_id"
                " LIMIT 1"
            )
            row = (
                await session.execute(
                    text(sql),
                    {"run_id": run_id, "entry_id": int(entry_id)},
                )
            ).fetchone()
            if not row:
                return None
            return {
                "id": row.id,
                "run_id": row.run_id,
                "timestamp": row.timestamp,
                "classification": row.classification,
                "source": row.source,
                "level": row.level,
                "event_type": row.event_type,
                "message": row.message,
                "payload": row.payload,
                "has_payload": bool(row.has_payload),
                "payload_size": int(row.payload_size or 0),
                "payload_preview": None,
            }

    async def get_total_entries(
        self,
        run_id: str,
        classification: Optional[str] = None,
    ) -> int:
        """Get count of entries (useful for polling: how many new since last fetch)."""
        await _get_or_create_user_log_engine(self.user_uuid)
        factory = _user_log_session_factories[self.user_uuid]
        async with factory() as session:
            sql = "SELECT COUNT(*) FROM log_entries WHERE run_id = :run_id"
            params: dict = {"run_id": run_id}
            if classification:
                sql += " AND classification = :classification"
                params["classification"] = classification
            return (await session.execute(text(sql), params)).scalar()

    async def get_all_run_logs(
        self,
        run_id: str,
        classification: Optional[str] = None,
        batch_size: int = 1000,
    ) -> list[dict]:
        """Read all log entries for a run by paging through the sidecar DB."""
        entries: list[dict] = []
        offset = 0

        while True:
            page = await self.get_run_logs(
                run_id,
                classification=classification,
                offset=offset,
                limit=batch_size,
                include_payload=True,
            )
            page_entries = page.get("entries", [])
            if not page_entries:
                break

            entries.extend(page_entries)
            offset += len(page_entries)

            total = int(page.get("total") or 0)
            if offset >= total:
                break

        return entries
