"""
GitHub Connection API Routes.

Endpoints for managing GitHub repository connections.
"""
import asyncio
import base64
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.session import get_user_db
from app.auth.middleware import get_current_user
from app.infra.db.repositories import GitHubConnectionRepository, ContentRepository
from app.infra.db.models.content import ContentType as DBContentType
from app.security.github_tokens import (
    GITHUB_TOKEN_MESSAGE,
    GITHUB_TOKEN_REF,
    GitHubTokenError,
    get_github_token,
    is_github_token_configured,
)
from ..schemas.github_connection import (
    GitHubConnectionCreate,
    GitHubConnectionUpdate,
    GitHubConnectionSummary,
    GitHubConnectionDetail,
    GitHubConnectionList,
    GitHubConnectionTestResult,
    GitHubBrowseResponse,
    GitHubFileInfo,
    GitHubFileContent,
    GitHubImportRequest,
    GitHubCreateFolderRequest,
    GitHubCreateFolderResponse,
)
from ..schemas.content import ContentDetail

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github-connections", tags=["github"])


# ============================================================================
# GitHub API Helpers
# ============================================================================

def _github_log_context(
    *,
    operation: str,
    connection_id: Optional[str] = None,
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    path: Optional[str] = None,
) -> dict[str, str]:
    return {
        "operation": operation,
        "connection_id": connection_id or "-",
        "repo": repo or "-",
        "branch": branch or "-",
        "path": path or "-",
    }


async def _run_blocking_github_call(
    operation: str,
    func,
    *args,
    connection_id: Optional[str] = None,
    repo: Optional[str] = None,
    branch: Optional[str] = None,
    path: Optional[str] = None,
):
    context = _github_log_context(
        operation=operation,
        connection_id=connection_id,
        repo=repo,
        branch=branch,
        path=path,
    )
    logger.info(
        "[GH-IO] dispatch op=%s connection_id=%s repo=%s branch=%s path=%s",
        context["operation"],
        context["connection_id"],
        context["repo"],
        context["branch"],
        context["path"],
    )
    try:
        result = await asyncio.to_thread(func, *args)
    except Exception as exc:
        logger.warning(
            "[GH-IO] failed op=%s connection_id=%s repo=%s branch=%s path=%s err=%s",
            context["operation"],
            context["connection_id"],
            context["repo"],
            context["branch"],
            context["path"],
            exc,
            exc_info=True,
        )
        raise

    logger.info(
        "[GH-IO] complete op=%s connection_id=%s repo=%s branch=%s path=%s",
        context["operation"],
        context["connection_id"],
        context["repo"],
        context["branch"],
        context["path"],
    )
    return result


def _test_github_connection_sync(token: str, repo: str, branch: str) -> tuple[bool, str]:
    """Test a GitHub connection. Returns (is_valid, message)."""
    try:
        from github import Github
        from github.GithubException import GithubException, BadCredentialsException
    except ImportError:
        return False, "PyGithub not installed"

    try:
        g = Github(token)

        # Test authentication
        user = g.get_user()
        _ = user.login  # Force API call

        # Test repository access
        repository = g.get_repo(repo)
        _ = repository.full_name  # Force API call

        # Test branch access
        _ = repository.get_branch(branch)

        return True, f"Successfully connected to {repo} ({branch})"

    except BadCredentialsException:
        return False, "Invalid GitHub token"
    except GithubException as e:
        if e.status == 404:
            return False, f"Repository '{repo}' not found or no access"
        return False, f"GitHub API error: {e.data.get('message', str(e))}"
    except Exception as e:
        return False, f"Connection error: {str(e)}"


async def _test_github_connection(token: str, repo: str, branch: str) -> tuple[bool, str]:
    return await _run_blocking_github_call(
        "test_connection",
        _test_github_connection_sync,
        token,
        repo,
        branch,
        repo=repo,
        branch=branch,
    )


def _browse_repository_sync(token: str, repo: str, branch: str, path: str) -> dict[str, Any]:
    from github import Github

    g = Github(token)
    repository = g.get_repo(repo)
    clean_path = path.strip("/") if path != "/" else ""
    contents = repository.get_contents(clean_path or "", ref=branch)

    if not isinstance(contents, list):
        contents = [contents]

    files = [
        {
            "name": item.name,
            "path": item.path,
            "type": "dir" if item.type == "dir" else "file",
            "size": item.size if item.type != "dir" else None,
            "download_url": item.download_url if item.type != "dir" else None,
        }
        for item in contents
    ]
    files.sort(key=lambda item: (0 if item["type"] == "dir" else 1, item["name"].lower()))
    return {
        "repo": repository.full_name,
        "branch": branch,
        "path": path,
        "contents": files,
    }


def _get_file_content_sync(token: str, repo: str, branch: str, path: str) -> dict[str, Any]:
    from github import Github

    g = Github(token)
    repository = g.get_repo(repo)
    file_content = repository.get_contents(path.strip("/"), ref=branch)
    if file_content.type == "dir":
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")

    content = base64.b64decode(file_content.content).decode("utf-8")
    return {
        "path": path,
        "name": file_content.name,
        "content": content,
        "size": file_content.size,
        "encoding": "utf-8",
    }


def _fetch_github_import_payload_sync(token: str, repo: str, branch: str, path: str) -> dict[str, Any]:
    from github import Github

    g = Github(token)
    repository = g.get_repo(repo)
    file_content = repository.get_contents(path.strip("/"), ref=branch)
    if file_content.type == "dir":
        raise HTTPException(status_code=400, detail="Cannot import a directory")

    content_body = base64.b64decode(file_content.content).decode("utf-8")
    return {
        "name": file_content.name,
        "body": content_body,
    }


def _create_folder_sync(token: str, repo: str, branch: str, folder_path: str, commit_message: str) -> dict[str, Any]:
    from github import Github
    from github.GithubException import GithubException

    g = Github(token)
    repository = g.get_repo(repo)
    gitkeep_path = f"{folder_path}/.gitkeep"

    try:
        existing = repository.get_contents(folder_path, ref=branch)
        if isinstance(existing, list) or existing.type == "dir":
            return {
                "path": folder_path,
                "success": True,
                "message": f"Folder '{folder_path}' already exists",
            }
    except GithubException as exc:
        if exc.status != 404:
            raise

    repository.create_file(
        path=gitkeep_path,
        message=commit_message,
        content="",
        branch=branch,
    )
    return {
        "path": folder_path,
        "success": True,
        "message": f"Folder '{folder_path}' created successfully",
    }


def _connection_to_summary(conn) -> GitHubConnectionSummary:
    """Convert DB connection to summary response."""
    token_supported = is_github_token_configured()
    return GitHubConnectionSummary(
        id=conn.id,
        name=conn.name,
        repo=conn.repo,
        branch=conn.branch,
        is_valid=conn.is_valid and token_supported,
        last_tested_at=conn.last_tested_at,
        created_at=conn.created_at,
    )


def _connection_to_detail(conn) -> GitHubConnectionDetail:
    """Convert DB connection to detail response."""
    token_supported = is_github_token_configured()
    return GitHubConnectionDetail(
        id=conn.id,
        name=conn.name,
        repo=conn.repo,
        branch=conn.branch,
        is_valid=conn.is_valid and token_supported,
        last_tested_at=conn.last_tested_at,
        last_error=conn.last_error if token_supported else GITHUB_TOKEN_MESSAGE,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


def _require_current_token() -> str:
    """Return GITHUB_TOKEN from root .env or raise HTTP 409."""
    try:
        return get_github_token()
    except GitHubTokenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ============================================================================
# CRUD Operations
# ============================================================================

@router.get("", response_model=GitHubConnectionList)
async def list_connections(
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubConnectionList:
    """List all GitHub connections."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connections = await repo.get_active()

    return GitHubConnectionList(
        items=[_connection_to_summary(c) for c in connections],
        total=len(connections),
    )


@router.post("", response_model=GitHubConnectionDetail, status_code=201)
async def create_connection(
    data: GitHubConnectionCreate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubConnectionDetail:
    """Create a new GitHub connection."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])

    # Check if connection to this repo already exists
    existing = await repo.get_by_repo(data.repo)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Connection to '{data.repo}' already exists"
        )

    # Test the connection before saving. GitHub secrets live only in root .env.
    token = _require_current_token()
    is_valid, message = await _test_github_connection(token, data.repo, data.branch)

    connection = await repo.create(
        name=data.name,
        repo=data.repo,
        branch=data.branch,
        token_ref=GITHUB_TOKEN_REF,
        is_valid=is_valid,
        last_tested_at=datetime.utcnow(),
        last_error=None if is_valid else message,
    )

    logger.info(f"Created GitHub connection: {connection.id} ({connection.repo})")

    if not is_valid:
        logger.warning(f"GitHub connection {connection.id} created but invalid: {message}")

    return _connection_to_detail(connection)


@router.get("/{connection_id}", response_model=GitHubConnectionDetail)
async def get_connection(
    connection_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubConnectionDetail:
    """Get a GitHub connection by ID."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    return _connection_to_detail(connection)


@router.put("/{connection_id}", response_model=GitHubConnectionDetail)
async def update_connection(
    connection_id: str,
    data: GitHubConnectionUpdate,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubConnectionDetail:
    """Update a GitHub connection."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Update fields
    update_data = {}
    if data.name is not None:
        update_data["name"] = data.name
    if data.branch is not None:
        update_data["branch"] = data.branch

    if update_data:
        connection = await repo.update(connection_id, **update_data)

    if data.token:
        logger.info("Ignoring submitted GitHub token; use GITHUB_TOKEN in root .env")

    if data.branch is not None:
        token = _require_current_token()
        is_valid, message = await _test_github_connection(token, connection.repo, connection.branch)
        await repo.update_test_status(connection_id, is_valid, None if is_valid else message)
        connection = await repo.get_by_id(connection_id)

    logger.info(f"Updated GitHub connection: {connection_id}")
    return _connection_to_detail(connection)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    connection_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
):
    """Delete a GitHub connection (soft delete)."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])

    success = await repo.soft_delete(connection_id)
    if not success:
        raise HTTPException(status_code=404, detail="Connection not found")

    logger.info(f"Deleted GitHub connection: {connection_id}")


# ============================================================================
# Test Connection
# ============================================================================

@router.post("/{connection_id}/test", response_model=GitHubConnectionTestResult)
async def test_connection(
    connection_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubConnectionTestResult:
    """Test a GitHub connection and update its status."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        token = get_github_token()
    except GitHubTokenError as exc:
        message = str(exc)
        await repo.update_test_status(connection_id, False, message)
        return GitHubConnectionTestResult(
            id=connection_id,
            is_valid=False,
            message=message,
            tested_at=datetime.utcnow(),
        )

    is_valid, message = await _test_github_connection(token, connection.repo, connection.branch)

    # Update status
    await repo.update_test_status(connection_id, is_valid, None if is_valid else message)

    logger.info(f"Tested GitHub connection {connection_id}: valid={is_valid}")

    return GitHubConnectionTestResult(
        id=connection_id,
        is_valid=is_valid,
        message=message,
        tested_at=datetime.utcnow(),
    )


# ============================================================================
# Browse Repository
# ============================================================================

@router.get("/{connection_id}/browse", response_model=GitHubBrowseResponse)
async def browse_repository(
    connection_id: str,
    path: str = Query("/", description="Path in repository to browse"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubBrowseResponse:
    """Browse files and directories in a GitHub repository."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        from github import Github
        from github.GithubException import GithubException

        token = _require_current_token()
        payload = await _run_blocking_github_call(
            "browse_repository",
            _browse_repository_sync,
            token,
            connection.repo,
            connection.branch,
            path,
            connection_id=connection_id,
            repo=connection.repo,
            branch=connection.branch,
            path=path,
        )
        files = [GitHubFileInfo(**item) for item in payload["contents"]]

        return GitHubBrowseResponse(
            connection_id=connection_id,
            repo=payload["repo"],
            branch=payload["branch"],
            path=payload["path"],
            contents=files,
        )

    except ImportError:
        raise HTTPException(status_code=500, detail="PyGithub not installed")
    except GithubException as e:
        raise HTTPException(status_code=400, detail=f"GitHub error: {e.data.get('message', str(e))}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error browsing repository: {str(e)}")


# ============================================================================
# Get File Content
# ============================================================================

@router.get("/{connection_id}/file", response_model=GitHubFileContent)
async def get_file_content(
    connection_id: str,
    path: str = Query(..., description="Path to file in repository"),
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubFileContent:
    """Get the content of a file from GitHub."""
    repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        from github import Github
        from github.GithubException import GithubException

        token = _require_current_token()
        payload = await _run_blocking_github_call(
            "get_file_content",
            _get_file_content_sync,
            token,
            connection.repo,
            connection.branch,
            path,
            connection_id=connection_id,
            repo=connection.repo,
            branch=connection.branch,
            path=path,
        )

        return GitHubFileContent(
            connection_id=connection_id,
            path=payload["path"],
            name=payload["name"],
            content=payload["content"],
            size=payload["size"],
            encoding=payload["encoding"],
        )

    except ImportError:
        raise HTTPException(status_code=500, detail="PyGithub not installed")
    except GithubException as e:
        raise HTTPException(status_code=400, detail=f"GitHub error: {e.data.get('message', str(e))}")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not a text file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


# ============================================================================
# Import File as Content
# ============================================================================

@router.post("/{connection_id}/import", response_model=ContentDetail, status_code=201)
async def import_file_as_content(
    connection_id: str,
    data: GitHubImportRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> ContentDetail:
    """Import a file from GitHub as content in the database."""
    gh_repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    content_repo = ContentRepository(db, user_uuid=user['uuid'])

    connection = await gh_repo.get_by_id(connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        from github import Github
        from github.GithubException import GithubException

        token = _require_current_token()
        import_payload = await _run_blocking_github_call(
            "import_file_read",
            _fetch_github_import_payload_sync,
            token,
            connection.repo,
            connection.branch,
            data.path,
            connection_id=connection_id,
            repo=connection.repo,
            branch=connection.branch,
            path=data.path,
        )

        # Validate content type
        try:
            content_type = DBContentType(data.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid content_type. Valid values: {[t.value for t in DBContentType]}"
            )

        # Create content
        name = data.name or import_payload["name"]

        content = await content_repo.create(
            name=name,
            content_type=content_type.value,
            body=import_payload["body"],
            variables={},
            description=data.description or f"Imported from {connection.repo}/{data.path}",
            tags=data.tags,
        )

        logger.info(f"Imported {data.path} from {connection.repo} as content {content.id}")

        return ContentDetail(
            id=content.id,
            name=content.name,
            content_type=data.content_type,
            body=content.body,
            variables=content.variables or {},
            description=content.description,
            tags=content.tags or [],
            created_at=content.created_at,
            updated_at=content.updated_at,
        )

    except ImportError:
        raise HTTPException(status_code=500, detail="PyGithub not installed")
    except GithubException as e:
        raise HTTPException(status_code=400, detail=f"GitHub error: {e.data.get('message', str(e))}")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not a text file")
    except Exception as e:
        logger.exception(f"Error importing file from GitHub")
        raise HTTPException(status_code=500, detail=f"Error importing file: {str(e)}")


# ============================================================================
# Create Folder
# ============================================================================

@router.post("/{connection_id}/create-folder", response_model=GitHubCreateFolderResponse, status_code=201)
async def create_folder(
    connection_id: str,
    data: GitHubCreateFolderRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db),
) -> GitHubCreateFolderResponse:
    """
    Create a folder in a GitHub repository.

    GitHub doesn't have real folders — this creates a .gitkeep file
    inside the desired path to materialize the folder.
    """
    gh_repo = GitHubConnectionRepository(db, user_uuid=user['uuid'])
    connection = await gh_repo.get_by_id(connection_id)

    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    try:
        from github import Github
        from github.GithubException import GithubException

        token = _require_current_token()
        clean_path = data.path.strip("/")
        if not clean_path:
            raise HTTPException(status_code=400, detail="Folder path cannot be empty")

        commit_msg = data.commit_message or f"Create folder: {clean_path}"
        payload = await _run_blocking_github_call(
            "create_folder",
            _create_folder_sync,
            token,
            connection.repo,
            connection.branch,
            clean_path,
            commit_msg,
            connection_id=connection_id,
            repo=connection.repo,
            branch=connection.branch,
            path=clean_path,
        )
        logger.info("Created folder in GitHub: %s", clean_path)

        return GitHubCreateFolderResponse(
            connection_id=connection_id,
            path=payload["path"],
            success=payload["success"],
            message=payload["message"],
        )

    except ImportError:
        raise HTTPException(status_code=500, detail="PyGithub not installed")
    except GithubException as e:
        raise HTTPException(status_code=400, detail=f"GitHub error: {e.data.get('message', str(e))}")
    except Exception as e:
        logger.exception(f"Error creating folder in GitHub")
        raise HTTPException(status_code=500, detail=f"Error creating folder: {str(e)}")
