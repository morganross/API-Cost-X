"""
Content API Routes.

Endpoints for managing content (instructions, criteria, fragments, input documents).
"""
import json
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.session import get_user_db
from app.infra.db.repositories import ContentRepository, RunRepository
from app.infra.db.models.content import ContentType as DBContentType
from app.auth.middleware import get_current_user
from app.services.config_builder import resolve_save_run_logs
from app.evaluation.criteria import parse_criteria_yaml
from app.services.log_reader import RunLogReader
from ..schemas.content import (
    ContentCreate,
    ContentUpdate,
    ContentSummary,
    ContentDetail,
    ContentList,
    ContentType,
    ContentResolveRequest,
    ContentResolved,
    ContentTypeCounts,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contents", tags=["contents"])
LOG_CONTENT_ID_PREFIX = "run-log:"


def _content_to_summary(content) -> ContentSummary:
    """Convert DB content to summary response."""
    return ContentSummary(
        id=content.id,
        name=content.name,
        content_type=ContentType(content.content_type),
        description=content.description,
        tags=content.tags or [],
        body_preview=content.body[:200] if content.body else "",
        created_at=content.created_at,
        updated_at=content.updated_at,
    )


def _content_to_detail(content) -> ContentDetail:
    """Convert DB content to detail response."""
    return ContentDetail(
        id=content.id,
        name=content.name,
        content_type=ContentType(content.content_type),
        body=content.body,
        variables=content.variables or {},
        description=content.description,
        tags=content.tags or [],
        created_at=content.created_at,
        updated_at=content.updated_at,
    )


def _validate_content_payload(content_type: ContentType, body: str) -> None:
    """Validate content bodies that carry strict runtime contracts."""
    if content_type == ContentType.EVAL_CRITERIA:
        try:
            parse_criteria_yaml(body)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid evaluation criteria: {exc}",
            ) from exc


def _make_log_content_id(run_id: str) -> str:
    return f"{LOG_CONTENT_ID_PREFIX}{run_id}"


def _extract_log_run_id(content_id: str) -> Optional[str]:
    if not content_id.startswith(LOG_CONTENT_ID_PREFIX):
        return None
    run_id = content_id[len(LOG_CONTENT_ID_PREFIX):].strip()
    return run_id or None


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


def _render_log_body(run, save_run_logs: bool, entries: list[dict]) -> str:
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
                "Saved run logging was turned off for this run, so there are no stored entries to show."
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


def _run_to_log_summary(run) -> ContentSummary:
    save_run_logs = _resolve_run_save_run_logs(run)
    status_label = run.status.replace("_", " ")
    preview = (
        f"Stored execution log for run '{run.title or run.id}'."
        if save_run_logs
        else f"Run '{run.title or run.id}' has saved log storage disabled."
    )
    return ContentSummary(
        id=_make_log_content_id(run.id),
        name=f"{run.title or run.id} Log",
        content_type=ContentType.LOGS,
        description=f"Run log for a {status_label} execution",
        tags=[],
        body_preview=preview,
        created_at=run.created_at,
        updated_at=run.completed_at or run.updated_at,
    )


def _run_to_log_detail(run, save_run_logs: bool, entries: list[dict]) -> ContentDetail:
    return ContentDetail(
        id=_make_log_content_id(run.id),
        name=f"{run.title or run.id} Log",
        content_type=ContentType.LOGS,
        body=_render_log_body(run, save_run_logs, entries),
        variables={},
        description=f"Read-only run log for execution {run.id}",
        tags=[],
        created_at=run.created_at,
        updated_at=run.completed_at or run.updated_at,
    )


# ============================================================================
# List / Search
# ============================================================================

@router.get("", response_model=ContentList)
async def list_contents(
    content_type: Optional[ContentType] = Query(None, description="Filter by content type"),
    search: Optional[str] = Query(None, description="Search by name"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentList:
    """
    List contents with optional filtering.

    - Filter by content_type to get only specific types
    - Search by name for partial matches
    - Filter by tag
    """
    repo = ContentRepository(db, user_uuid=user['uuid'])
    run_repo = RunRepository(db, user_uuid=user['uuid'])
    offset = (page - 1) * page_size

    if content_type == ContentType.LOGS:
        if tag:
            items = []
            total = 0
        elif search:
            items = await run_repo.search_by_title(search, limit=page_size, offset=offset)
            total = await run_repo.count_search_by_title(search)
        else:
            items = await run_repo.get_all_for_list(limit=page_size, offset=offset)
            total = await run_repo.count()
        summaries = [_run_to_log_summary(run) for run in items]
    elif search and content_type is None:
        fetch_limit = offset + page_size
        content_matches = await repo.search_by_name(search, limit=fetch_limit)
        run_matches = await run_repo.search_by_title(search, limit=fetch_limit)
        combined = [
            *[_content_to_summary(content) for content in content_matches],
            *[_run_to_log_summary(run) for run in run_matches],
        ]
        combined.sort(key=lambda item: item.created_at, reverse=True)
        summaries = combined[offset:offset + page_size]
        total = len(combined)  # Approximate for search
    elif search:
        db_type = DBContentType(content_type.value) if content_type else None
        items = await repo.search_by_name(search, content_type=db_type, limit=page_size)
        total = len(items)  # Approximate for search
        summaries = [_content_to_summary(c) for c in items]
    elif tag:
        db_type = DBContentType(content_type.value) if content_type else None
        items = await repo.search_by_tag(tag, content_type=db_type, limit=page_size)
        total = len(items)
        summaries = [_content_to_summary(c) for c in items]
    elif content_type:
        db_type = DBContentType(content_type.value)
        items = await repo.get_by_type(db_type, limit=page_size, offset=offset)
        total = await repo.count_by_type(db_type)
        summaries = [_content_to_summary(c) for c in items]
    else:
        fetch_limit = offset + page_size
        contents = await repo.get_active(limit=fetch_limit, offset=0)
        runs = await run_repo.get_all_for_list(limit=fetch_limit, offset=0)
        combined = [
            *[_content_to_summary(content) for content in contents],
            *[_run_to_log_summary(run) for run in runs],
        ]
        combined.sort(key=lambda item: item.created_at, reverse=True)
        summaries = combined[offset:offset + page_size]
        total = await repo.count_active() + await run_repo.count()

    pages = (total + page_size - 1) // page_size if total > 0 else 1

    return ContentList(
        items=summaries,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/counts", response_model=ContentTypeCounts)
async def get_content_counts(
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentTypeCounts:
    """Get count of contents by type."""
    repo = ContentRepository(db, user_uuid=user['uuid'])
    run_repo = RunRepository(db, user_uuid=user['uuid'])

    counts = ContentTypeCounts()
    counts.generation_instructions = await repo.count_by_type(DBContentType.GENERATION_INSTRUCTIONS)
    counts.input_document = await repo.count_by_type(DBContentType.INPUT_DOCUMENT)
    counts.single_eval_instructions = await repo.count_by_type(DBContentType.SINGLE_EVAL_INSTRUCTIONS)
    counts.pairwise_eval_instructions = await repo.count_by_type(DBContentType.PAIRWISE_EVAL_INSTRUCTIONS)
    counts.eval_criteria = await repo.count_by_type(DBContentType.EVAL_CRITERIA)
    counts.combine_instructions = await repo.count_by_type(DBContentType.COMBINE_INSTRUCTIONS)
    counts.template_fragment = await repo.count_by_type(DBContentType.TEMPLATE_FRAGMENT)
    counts.logs = await run_repo.count()
    counts.total = (
        counts.generation_instructions +
        counts.input_document +
        counts.single_eval_instructions +
        counts.pairwise_eval_instructions +
        counts.eval_criteria +
        counts.combine_instructions +
        counts.template_fragment +
        counts.logs
    )

    return counts


# ============================================================================
# CRUD Operations
# ============================================================================

@router.post("", response_model=ContentDetail, status_code=201)
async def create_content(
    data: ContentCreate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentDetail:
    """Create new content."""
    if data.content_type == ContentType.LOGS:
        raise HTTPException(status_code=400, detail="Run logs are read-only and cannot be created")

    _validate_content_payload(data.content_type, data.body)

    repo = ContentRepository(db, user_uuid=user['uuid'])

    # Check if name already exists for this type
    existing = await repo.get_by_name(data.name)
    if existing and existing.content_type == data.content_type.value:
        raise HTTPException(
            status_code=400,
            detail=f"Content with name '{data.name}' already exists for type {data.content_type.value}"
        )

    content = await repo.create(
        name=data.name,
        content_type=data.content_type.value,
        body=data.body,
        variables=data.variables,
        description=data.description,
        tags=data.tags,
    )

    logger.info(f"Created content: {content.id} ({content.name})")
    return _content_to_detail(content)


@router.get("/{content_id}", response_model=ContentDetail)
async def get_content(
    content_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentDetail:
    """Get content by ID."""
    run_id = _extract_log_run_id(content_id)
    if run_id:
        run_repo = RunRepository(db, user_uuid=user['uuid'])
        run = await run_repo.get_by_id(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run log not found")

        save_run_logs = _resolve_run_save_run_logs(run)
        reader = RunLogReader(user['uuid'])
        entries = await reader.get_all_run_logs(run_id, classification=None)
        return _run_to_log_detail(run, save_run_logs, entries)

    repo = ContentRepository(db, user_uuid=user['uuid'])
    content = await repo.get_by_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    return _content_to_detail(content)


@router.put("/{content_id}", response_model=ContentDetail)
async def update_content(
    content_id: str,
    data: ContentUpdate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentDetail:
    """Update content."""
    if _extract_log_run_id(content_id):
        raise HTTPException(status_code=400, detail="Run logs are read-only and cannot be edited")

    repo = ContentRepository(db, user_uuid=user['uuid'])
    content = await repo.get_by_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    if data.body is not None:
        _validate_content_payload(ContentType(content.content_type), data.body)

    # Build update dict
    update_data = {}
    if data.name is not None:
        update_data["name"] = data.name
    if data.body is not None:
        update_data["body"] = data.body
    if data.variables is not None:
        update_data["variables"] = data.variables
    if data.description is not None:
        update_data["description"] = data.description
    if data.tags is not None:
        update_data["tags"] = data.tags

    if update_data:
        content = await repo.update(content_id, **update_data)

    logger.info(f"Updated content: {content_id}")
    return _content_to_detail(content)


@router.delete("/{content_id}", status_code=204)
async def delete_content(
    content_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
):
    """Delete content permanently."""
    if _extract_log_run_id(content_id):
        raise HTTPException(status_code=400, detail="Run logs are read-only and cannot be deleted")

    repo = ContentRepository(db, user_uuid=user['uuid'])

    success = await repo.delete(content_id)
    if not success:
        raise HTTPException(status_code=404, detail="Content not found")

    logger.info(f"Deleted content: {content_id}")


# ============================================================================
# Variable Resolution
# ============================================================================

@router.post("/{content_id}/resolve", response_model=ContentResolved)
async def resolve_content(
    content_id: str,
    data: ContentResolveRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentResolved:
    """
    Resolve/preview content with variables substituted.

    Static variables (linked to other content) are resolved recursively.
    Runtime variables are substituted from the request body.
    """
    if _extract_log_run_id(content_id):
        raise HTTPException(status_code=400, detail="Run logs do not support variable resolution")

    repo = ContentRepository(db, user_uuid=user['uuid'])
    content = await repo.get_by_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    resolved_body, unresolved = await _resolve_variables(
        content,
        data.runtime_variables,
        repo,
        visited=set()
    )

    return ContentResolved(
        id=content.id,
        name=content.name,
        content_type=ContentType(content.content_type),
        resolved_body=resolved_body,
        unresolved_variables=unresolved,
    )


async def _resolve_variables(
    content,
    runtime_vars: dict[str, str],
    repo: ContentRepository,
    visited: set[str],
) -> tuple[str, list[str]]:
    """
    Recursively resolve variables in content.

    Returns (resolved_body, list_of_unresolved_variables)
    """
    # Prevent infinite recursion
    if content.id in visited:
        return content.body, []
    visited.add(content.id)

    result = content.body
    unresolved = []

    # Find all {{VARIABLE}} patterns
    pattern = r"\{\{(\w+)\}\}"
    matches = re.findall(pattern, result)

    for var_name in matches:
        placeholder = f"{{{{{var_name}}}}}"

        # Check runtime variables first
        if var_name in runtime_vars:
            result = result.replace(placeholder, runtime_vars[var_name])
            continue

        # Check static variables (linked content)
        static_vars = content.variables or {}
        if var_name in static_vars and static_vars[var_name]:
            linked_id = static_vars[var_name]
            linked_content = await repo.get_by_id(linked_id)

            if linked_content:
                # Recursively resolve the linked content
                resolved_linked, linked_unresolved = await _resolve_variables(
                    linked_content, runtime_vars, repo, visited
                )
                result = result.replace(placeholder, resolved_linked)
                unresolved.extend(linked_unresolved)
            else:
                unresolved.append(var_name)
        else:
            # Variable not found
            unresolved.append(var_name)

    return result, unresolved


# ============================================================================
# Duplicate
# ============================================================================

@router.post("/{content_id}/duplicate", response_model=ContentDetail, status_code=201)
async def duplicate_content(
    content_id: str,
    name: Optional[str] = Query(None, description="Name for the duplicate"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentDetail:
    """Create a copy of existing content."""
    if _extract_log_run_id(content_id):
        raise HTTPException(status_code=400, detail="Run logs are read-only and cannot be duplicated")

    repo = ContentRepository(db, user_uuid=user['uuid'])
    content = await repo.get_by_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    _validate_content_payload(ContentType(content.content_type), content.body)

    new_name = name or f"{content.name} (Copy)"

    duplicate = await repo.create(
        name=new_name,
        content_type=content.content_type,
        body=content.body,
        variables=content.variables.copy() if content.variables else {},
        description=content.description,
        tags=content.tags.copy() if content.tags else [],
    )

    logger.info(f"Duplicated content {content_id} -> {duplicate.id}")
    return _content_to_detail(duplicate)
