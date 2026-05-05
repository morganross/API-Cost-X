"""
Artifact endpoints for runs.

Endpoints for reports, logs, and generated documents.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional

from app.infra.db.session import get_user_db
from app.infra.db.repositories import RunRepository
from app.auth.middleware import get_current_user
from app.services.log_reader import RunLogReader
from app.services.config_builder import resolve_save_run_logs
from ....config import get_settings
from ....evaluation.reports.generator import ReportGenerator
from .detail_payload import build_full_run_detail

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_owned_run_or_404(db: AsyncSession, user_uuid: str, run_id: str):
    repo = RunRepository(db, user_uuid=user_uuid)
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def _build_run_export_or_500(
    *,
    run_id: str,
    user_uuid: str,
    run_name: str,
    log_prefix: str,
) -> Path:
    from app.services.export_service import build_run_export

    try:
        return await build_run_export(
            run_id=run_id,
            user_uuid=user_uuid,
            run_name=run_name,
        )
    except Exception as exc:
        logger.error("%s for run %s: %s", log_prefix, run_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Export generation failed")


def get_run_root(user_uuid: str, run_id: str) -> Path:
    settings = get_settings()
    return settings.data_dir / f"user_{user_uuid}" / "runs" / run_id


def _resolve_run_save_run_logs(run) -> bool:
    config = run.config or {}
    general_config = config.get("general_config")
    if isinstance(general_config, dict):
        return resolve_save_run_logs(general_config)
    config_overrides = config.get("config_overrides") or {}
    general_overrides = (
        config_overrides.get("general")
        if isinstance(config_overrides, dict)
        else None
    )
    return resolve_save_run_logs(general_overrides)


def _format_log_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return payload
        return json.dumps(parsed, indent=2, default=str)
    return json.dumps(payload, indent=2, default=str)


async def _run_blocking_artifact_io(
    operation: str,
    func,
    *args,
    run_id: str,
    user_uuid: str,
    path: Path | None = None,
):
    logger.info(
        "[ARTIFACT-IO] dispatch op=%s run=%s user=...%s path=%s",
        operation,
        run_id[:8],
        user_uuid[-8:],
        path if path is not None else "-",
    )
    try:
        result = await asyncio.to_thread(func, *args)
    except Exception as exc:
        logger.warning(
            "[ARTIFACT-IO] failed op=%s run=%s user=...%s path=%s err=%s",
            operation,
            run_id[:8],
            user_uuid[-8:],
            path if path is not None else "-",
            exc,
            exc_info=True,
        )
        raise

    logger.info(
        "[ARTIFACT-IO] complete op=%s run=%s user=...%s path=%s",
        operation,
        run_id[:8],
        user_uuid[-8:],
        path if path is not None else "-",
    )
    return result


def _generate_report_sync(generator: ReportGenerator, run, run_data: dict[str, Any]) -> Path:
    return generator.generate_html_report(run, run_data)


def _read_text_file_sync(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_shared_log_sync(shared_dir: Path, shared_file: Path, payload: dict[str, Any]) -> None:
    shared_dir.mkdir(exist_ok=True)
    shared_file.write_text(json.dumps(payload, indent=2, default=str))


def _serialize_log_entries_as_text(run, save_run_logs: bool, entries: list[dict]) -> str:
    lines = [
        f"Run Title: {run.title or run.id}",
        f"Run ID: {run.id}",
        f"Status: {run.status}",
        f"Created: {run.created_at.isoformat()}",
        f"Started: {run.started_at.isoformat() if run.started_at else '-'}",
        f"Completed: {run.completed_at.isoformat() if run.completed_at else '-'}",
        f"Saved Run Logs: {'yes' if save_run_logs else 'no'}",
        f"Entry Count: {len(entries)}",
        "",
    ]

    if not entries:
        if save_run_logs:
            lines.append("No stored log entries were found for this run.")
        else:
            lines.append(
                "Saved run logging was turned off for this run, so there are no stored entries to download."
            )
        return "\n".join(lines)

    for entry in entries:
        timestamp = str(entry.get("timestamp") or "")
        classification = str(entry.get("classification") or "EVENT")
        source = str(entry.get("source") or "apicostx")
        level = str(entry.get("level") or "INFO")
        message = str(entry.get("message") or "")
        event_type = entry.get("event_type")

        lines.append(
            f"{timestamp} [{classification}] [{source}] [{level}] {message}"
        )
        if event_type:
            lines.append(f"event_type: {event_type}")

        payload_text = _format_log_payload(entry.get("payload"))
        if payload_text:
            lines.append("payload:")
            lines.extend(f"  {line}" for line in payload_text.splitlines())
        lines.append("")

    return "\n".join(lines).rstrip()


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
):
    """
    Generate and download the HTML report for a run.
    Includes the Evaluation Timeline Chart.
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_with_tasks(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run_root = get_run_root(user['uuid'], run_id)
    reports_dir = run_root / "reports"
    generator = ReportGenerator(reports_dir)
    report_path = reports_dir / "report.html"

    if report_path.exists():
        return FileResponse(report_path)

    try:
        from app.services.results_reader import ResultsReader
        _reader = ResultsReader(db)
        _snapshot = await _reader.get_run_snapshot(run_id)
        run_data = build_full_run_detail(
            run,
            _snapshot,
        ).model_dump()
        report_path = await _run_blocking_artifact_io(
            "generate_report",
            _generate_report_sync,
            generator,
            run,
            run_data,
            run_id=run_id,
            user_uuid=user["uuid"],
            path=report_path,
        )
        return FileResponse(report_path)
    except Exception as e:
        logger.error(f"Failed to generate report for run {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate report")


@router.get("/runs/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    classification: str = Query("event", pattern="^(event|all)$",
                                description="'event' for admin-safe events only, 'all' for events + details"),
    offset: int = Query(0, ge=0, description="Row offset"),
    limit: int = Query(100, ge=1, le=5000, description="Max entries to return"),
    after_id: Optional[int] = Query(None, ge=0, description="Return rows with id greater than this cursor"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Get structured run log entries from sidecar DB.

    Returns paginated EVENT and/or DETAIL entries for a run.
    Default (classification=event) returns admin-safe lifecycle events only.
    Set classification=all to include DETAIL entries (may contain user content).
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    save_run_logs = _resolve_run_save_run_logs(run)

    reader = RunLogReader(user['uuid'])
    class_filter = "EVENT" if classification == "event" else None
    result = await reader.get_run_logs(run_id, classification=class_filter,
                                       offset=offset, limit=limit, after_id=after_id)
    result["limit"] = limit
    result["save_run_logs"] = save_run_logs
    return result


@router.get("/runs/{run_id}/logs/entries/{entry_id}")
async def get_run_log_entry_detail(
    run_id: str,
    entry_id: int,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> dict:
    """Return one full log entry payload for lazy detail display."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    reader = RunLogReader(user["uuid"])
    entry = await reader.get_log_entry(run_id, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return {"run_id": run_id, "entry": entry}


@router.get("/runs/{run_id}/logs/download")
async def download_run_logs(
    run_id: str,
    classification: str = Query(
        "all",
        pattern="^(event|all)$",
        description="'event' for lifecycle events only, 'all' for events + details",
    ),
    download_format: str = Query(
        "txt",
        alias="format",
        pattern="^(txt|json)$",
        description="Download format",
    ),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Response:
    """Download the stored sidecar run log as plain text or JSON."""
    repo = RunRepository(db, user_uuid=user["uuid"])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    save_run_logs = _resolve_run_save_run_logs(run)
    reader = RunLogReader(user["uuid"])
    class_filter = "EVENT" if classification == "event" else None
    entries = await reader.get_all_run_logs(run_id, classification=class_filter)

    safe_title = (run.title or run_id[:8]).replace(" ", "_")
    safe_title = "".join(c for c in safe_title if c.isalnum() or c in "_-")[:64]

    if download_format == "json":
        content = json.dumps(
            {
                "run_id": run_id,
                "title": run.title,
                "status": run.status,
                "classification": classification,
                "save_run_logs": save_run_logs,
                "entry_count": len(entries),
                "entries": entries,
            },
            indent=2,
            default=str,
        )
        filename = f"apicostx-log-{safe_title}.json"
        media_type = "application/json"
    else:
        content = _serialize_log_entries_as_text(run, save_run_logs, entries)
        filename = f"apicostx-log-{safe_title}.txt"
        media_type = "text/plain; charset=utf-8"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/logs/count")
async def get_run_log_count(
    run_id: str,
    classification: str = Query("event", pattern="^(event|all)$"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """Lightweight entry count for polling / badge updates."""
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    save_run_logs = _resolve_run_save_run_logs(run)

    reader = RunLogReader(user['uuid'])
    class_filter = "EVENT" if classification == "event" else None
    count = await reader.get_total_entries(run_id, classification=class_filter)
    return {"run_id": run_id, "total": count, "save_run_logs": save_run_logs}


@router.get("/runs/{run_id}/generated/{doc_id:path}")
async def get_generated_doc_content(
    run_id: str,
    doc_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Get the content of a generated document.

    Returns the markdown content of a generated or combined document.
    Documents are stored in data/user_{user_uuid}/runs/{run_id}/generated/{doc_id}.md
    """
    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Sanitize doc_id for filename
    safe_doc_id = doc_id.replace(':', '_').replace('/', '_').replace('\\', '_')
    generated_dir = get_run_root(user["uuid"], run_id) / "generated"
    file_path = (generated_dir / f"{safe_doc_id}.md").resolve()

    # Guard against path traversal
    if not str(file_path).startswith(str(generated_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid document ID")

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Generated document not found. The run may have been executed before content saving was enabled."
        )

    try:
        content = await _run_blocking_artifact_io(
            "read_generated_doc",
            _read_text_file_sync,
            file_path,
            run_id=run_id,
            user_uuid=user["uuid"],
            path=file_path,
        )
        return {
            "run_id": run_id,
            "doc_id": doc_id,
            "content": content,
            "content_length": len(content),
        }
    except Exception as e:
        logger.error(f"Failed to read generated doc {doc_id} for run {run_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read document")


@router.post("/runs/{run_id}/export")
async def generate_run_export(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> dict:
    """
    Trigger on-demand ZIP export generation for any run (completed or failed).
    Builds the archive synchronously. When this returns 200 the GET endpoint
    is ready to serve the file.
    """
    run = await _get_owned_run_or_404(db, user["uuid"], run_id)
    await _build_run_export_or_500(
        run_id=run_id,
        user_uuid=user["uuid"],
        run_name=run.title or run_id[:8],
        log_prefix="On-demand export failed",
    )

    return {"status": "ready", "run_id": run_id}


# In-memory rate-limit tracker: {(user_uuid, run_id): last_share_utc_timestamp}
_share_rate_limit: Dict[str, float] = {}
_SHARE_COOLDOWN_SECONDS = 60


@router.post("/runs/{run_id}/share-logs")
async def share_run_logs(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> dict:
    """
    User voluntarily shares their run's log entries (EVENT + DETAIL) with admin.

    Writes a JSON file to the shared_logs directory for admin review via SSH.
    Rate-limited to 1 share per run per minute.
    """
    user_uuid = user["uuid"]

    # Ownership check
    repo = RunRepository(db, user_uuid=user_uuid)
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # Rate limit: 1 share per run per minute
    rate_key = f"{user_uuid}:{run_id}"
    now = datetime.now(timezone.utc).timestamp()
    last_share = _share_rate_limit.get(rate_key, 0)
    if now - last_share < _SHARE_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="Logs for this run were shared recently. Please wait a minute.",
        )

    # Fetch all entries (EVENT + DETAIL)
    reader = RunLogReader(user_uuid)
    page_size = 5000
    offset = 0
    total_count = None
    all_entries = []

    while True:
        page = await reader.get_run_logs(
            run_id,
            classification=None,
            offset=offset,
            limit=page_size,
            include_payload=True,
        )
        if total_count is None:
            total_count = page["total"]
        page_entries = page["entries"]
        if not page_entries:
            break
        all_entries.extend(page_entries)
        offset += len(page_entries)
        if total_count is not None and offset >= total_count:
            break

    if not all_entries:
        raise HTTPException(status_code=404, detail="No log entries for this run")

    entry_count = len(all_entries)

    # Write to shared_logs directory
    settings = get_settings()
    shared_dir = settings.data_dir / "shared_logs"
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shared_file = shared_dir / f"{user_uuid}_{run_id}_{ts_str}.json"
    await _run_blocking_artifact_io(
        "share_logs_write",
        _write_shared_log_sync,
        shared_dir,
        shared_file,
        {
            "user_uuid": user_uuid,
            "run_id": run_id,
            "shared_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": entry_count,
            "entries": all_entries,
        },
        run_id=run_id,
        user_uuid=user_uuid,
        path=shared_file,
    )

    # Update rate-limit tracker
    _share_rate_limit[rate_key] = now

    logger.info(
        "[SHARE] User %s shared logs for run %s: %d entries -> %s",
        user_uuid[:8], run_id[:8], entry_count, shared_file.name,
    )

    return {"status": "shared", "entry_count": entry_count}


@router.head("/runs/{run_id}/export")
async def check_run_export(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> Response:
    """
    HEAD check: returns 200 if export.zip exists, 404 if not.
    Used by the web GUI button to detect pre-existing exports on mount.
    """
    export_path = get_run_root(user["uuid"], run_id) / "export.zip"
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="Export not found")
    size = export_path.stat().st_size
    return Response(
        status_code=200,
        headers={"Content-Type": "application/zip", "Content-Length": str(size)},
    )


@router.get("/runs/{run_id}/export")
async def download_run_export(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> FileResponse:
    """Download the ZIP export for a completed run."""
    run = await _get_owned_run_or_404(db, user["uuid"], run_id)

    # E3: Gate on run status, not blob presence (blob is deprecated)
    if run.status not in ("completed", "failed", "completed_with_errors"):
        raise HTTPException(
            status_code=404,
            detail="Export archive not available yet. It is generated automatically after run completion.",
        )

    export_path = get_run_root(user["uuid"], run_id) / "export.zip"
    if not export_path.exists():
        export_path = await _build_run_export_or_500(
            run_id=run_id,
            user_uuid=user["uuid"],
            run_name=run.title or run_id[:8],
            log_prefix="Export build failed",
        )

    # Derive a human-friendly filename from the run title
    safe_title = (run.title or run_id[:8]).replace(" ", "_")
    safe_title = "".join(c for c in safe_title if c.isalnum() or c in "_-")[:64]
    filename = f"apicostx-export-{safe_title}.zip"

    return FileResponse(
        path=str(export_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
