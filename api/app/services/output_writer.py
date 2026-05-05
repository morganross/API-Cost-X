"""
Output Writer Service.

Handles writing winning documents to:
1. Content Library (as OUTPUT_DOCUMENT content type)
2. GitHub repository (via push)
"""
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.repositories import ContentRepository, GitHubConnectionRepository
from app.infra.db.models.content import ContentType
from app.security.github_tokens import GitHubTokenError, get_github_token


logger = logging.getLogger(__name__)


class OutputWriteResult:
    """Result of writing output."""

    def __init__(
        self,
        success: bool,
        content_id: Optional[str] = None,
        github_url: Optional[str] = None,
        github_commit_sha: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.content_id = content_id
        self.github_url = github_url
        self.github_commit_sha = github_commit_sha
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content_id": self.content_id,
            "github_url": self.github_url,
            "github_commit_sha": self.github_commit_sha,
            "error": self.error,
        }


class OutputWriter:
    """
    Writes winning documents to configured destinations.

    Destinations:
    - LIBRARY: Saves to Content Library as OUTPUT_DOCUMENT
    - GITHUB: Also pushes to GitHub repository
    """

    def __init__(self, db: AsyncSession, user_uuid: str):
        self.db = db
        self.user_uuid = user_uuid
        self.content_repo = ContentRepository(db, user_uuid=user_uuid)

    def render_filename(
        self,
        template: str,
        source_doc_name: str,
        winner_model: str,
        run_id: str,
        timestamp: Optional[datetime] = None,
        preset_name: Optional[str] = None,
    ) -> str:
        """
        Render filename from template.

        Supported placeholders:
        - {source_doc_name} - Input filename without extension
        - {source_doc_ext} - Input file extension (e.g. '.md')
        - {winner_model} - Cleaned model name
        - {run_id} - First 8 chars of run UUID
        - {timestamp} - YYYYMMDD_HHMMSS
        - {date} - YYYY-MM-DD
        - {preset_name} - Preset name, cleaned
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Clean model name for filename (remove provider prefix, special chars)
        clean_model = re.sub(r'[^a-zA-Z0-9_-]', '_', winner_model.split(':')[-1])

        # Clean source doc name - strip known extensions before cleaning
        import os
        source_base = source_doc_name
        source_ext = ""
        known_extensions = {'.md', '.txt', '.rst', '.html', '.htm'}
        _, ext = os.path.splitext(source_doc_name)
        if ext.lower() in known_extensions:
            source_base = source_doc_name[:len(source_doc_name) - len(ext)]
            source_ext = ext
        clean_source = re.sub(r'[^a-zA-Z0-9_-]', '_', source_base)

        # Clean preset name
        clean_preset = re.sub(r'[^a-zA-Z0-9_-]', '_', preset_name or "preset")

        result = template.format(
            source_doc_name=clean_source,
            source_doc_ext=source_ext,
            winner_model=clean_model,
            run_id=run_id[:8],  # Short run ID
            timestamp=timestamp.strftime("%Y%m%d_%H%M%S"),
            date=timestamp.strftime("%Y-%m-%d"),
            preset_name=clean_preset,
        )

        # Ensure .md extension
        if not result.endswith('.md'):
            result += '.md'

        return result

    async def write_to_library(
        self,
        content: str,
        name: str,
        run_id: str,
        winner_doc_id: str,
        source_doc_name: str,
        winner_model: str,
        description: Optional[str] = None,
    ) -> OutputWriteResult:
        """
        Save winning document to Content Library as OUTPUT_DOCUMENT.

        Args:
            content: The winning document content
            name: Display name for the content
            run_id: ID of the run that produced this
            winner_doc_id: ID of the winning document
            source_doc_name: Name of the source document
            winner_model: Model that generated the winner
            description: Optional description

        Returns:
            OutputWriteResult with content_id
        """
        try:
            # Build description
            if not description:
                description = (
                    f"Winning document from run {run_id[:8]}. "
                    f"Source: {source_doc_name}. "
                    f"Model: {winner_model}."
                )

            # Create content in library
            content_obj = await self.content_repo.create(
                name=name,
                content_type=ContentType.OUTPUT_DOCUMENT.value,
                body=content,
                variables={},
                description=description,
                tags=[
                    f"run:{run_id[:8]}",
                    f"model:{winner_model}",
                    f"source:{source_doc_name}",
                    "output",
                    "winner",
                ],
            )

            logger.info(
                f"Saved winning document to Content Library: "
                f"id={content_obj.id}, name={name}"
            )

            return OutputWriteResult(
                success=True,
                content_id=content_obj.id,
            )

        except Exception as e:
            logger.error("Failed to save winning document to library: %s", type(e).__name__)
            return OutputWriteResult(
                success=False,
                error=str(e),
            )

    async def push_to_github(
        self,
        content: str,
        connection_id: str,
        output_path: str,
        filename: str,
        commit_message: str,
    ) -> OutputWriteResult:
        """
        Push winning document to GitHub repository.

        Args:
            content: The document content
            connection_id: GitHub connection ID
            output_path: Path in repo (e.g., "/outputs/")
            filename: Filename to use
            commit_message: Git commit message

        Returns:
            OutputWriteResult with github_url and commit_sha
        """
        try:
            from github import Github
            from github.GithubException import GithubException

            # Get connection
            gh_repo = GitHubConnectionRepository(self.db, user_uuid=self.user_uuid)
            connection = await gh_repo.get_by_id(connection_id)

            if not connection:
                return OutputWriteResult(
                    success=False,
                    error=f"GitHub connection {connection_id} not found",
                )

            try:
                token = get_github_token()
            except GitHubTokenError as exc:
                return OutputWriteResult(
                    success=False,
                    error=str(exc),
                )

            # Connect to GitHub
            g = Github(token)
            repository = g.get_repo(connection.repo)

            # Build full path
            clean_path = output_path.strip("/")
            if clean_path:
                full_path = f"{clean_path}/{filename}"
            else:
                full_path = filename

            # Check if file exists
            try:
                existing = repository.get_contents(full_path, ref=connection.branch)
                # File exists - update it
                result = repository.update_file(
                    path=full_path,
                    message=commit_message,
                    content=content,
                    sha=existing.sha,
                    branch=connection.branch,
                )
                logger.info(f"Updated file on GitHub: {full_path}")
            except GithubException as e:
                if e.status == 404:
                    # File doesn't exist - create it
                    result = repository.create_file(
                        path=full_path,
                        message=commit_message,
                        content=content,
                        branch=connection.branch,
                    )
                    logger.info(f"Created new file on GitHub: {full_path}")
                else:
                    raise

            # Get the file URL
            html_url = f"https://github.com/{connection.repo}/blob/{connection.branch}/{full_path}"

            return OutputWriteResult(
                success=True,
                github_url=html_url,
                github_commit_sha=result["commit"].sha,
            )

        except ImportError:
            return OutputWriteResult(
                success=False,
                error="PyGithub not installed. Run: pip install PyGithub",
            )
        except Exception as e:
            logger.error("Failed to push to GitHub: %s", type(e).__name__)
            return OutputWriteResult(
                success=False,
                error=str(e),
            )

    async def write_winner(
        self,
        content: str,
        output_destination: str,
        filename_template: str,
        run_id: str,
        winner_doc_id: str,
        source_doc_name: str,
        winner_model: str,
        github_connection_id: Optional[str] = None,
        github_output_path: Optional[str] = None,
        github_commit_message: Optional[str] = None,
        preset_name: Optional[str] = None,
        source_relative_path: Optional[str] = None,
    ) -> OutputWriteResult:
        """
        Write winning document to configured destination(s).

        For LIBRARY and GITHUB destinations, always saves to Content Library first.
        For GITHUB, also pushes to the configured repository.

        Args:
            content: The winning document content
            output_destination: "none", "library", or "github"
            filename_template: Template for filename
            run_id: Run ID
            winner_doc_id: Winner document ID
            source_doc_name: Source document name
            winner_model: Model that generated the winner
            github_connection_id: GitHub connection (for github destination)
            github_output_path: Path in GitHub repo (for github destination)
            github_commit_message: Commit message (for github destination)
            preset_name: Preset name for {preset_name} placeholder
            source_relative_path: Relative path of source doc (for folder mirroring)

        Returns:
            OutputWriteResult combining library and github results
        """
        if output_destination == "none":
            logger.info(f"Output destination is 'none', skipping output write for run {run_id}")
            return OutputWriteResult(success=True)

        # Render filename
        filename = self.render_filename(
            template=filename_template,
            source_doc_name=source_doc_name,
            winner_model=winner_model,
            run_id=run_id,
            preset_name=preset_name,
        )

        # Always save to Content Library for library and github destinations
        library_result = await self.write_to_library(
            content=content,
            name=filename.replace('.md', ''),  # Remove extension for display name
            run_id=run_id,
            winner_doc_id=winner_doc_id,
            source_doc_name=source_doc_name,
            winner_model=winner_model,
        )

        if not library_result.success:
            return library_result

        # If github destination, also push to GitHub
        if output_destination == "github":
            if not github_connection_id:
                return OutputWriteResult(
                    success=False,
                    content_id=library_result.content_id,
                    error="GitHub destination selected but no connection configured",
                )

            if not github_output_path:
                return OutputWriteResult(
                    success=False,
                    content_id=library_result.content_id,
                    error="github_output_path is required when GitHub destination is selected",
                )
            if not github_commit_message:
                return OutputWriteResult(
                    success=False,
                    content_id=library_result.content_id,
                    error="github_commit_message is required when GitHub destination is selected",
                )

            # If source_relative_path has subdirs, mirror them under output_path
            effective_output_path = github_output_path
            if source_relative_path:
                import os
                rel_dir = os.path.dirname(source_relative_path)
                if rel_dir:
                    clean_output = github_output_path.strip("/")
                    effective_output_path = f"{clean_output}/{rel_dir}" if clean_output else rel_dir

            github_result = await self.push_to_github(
                content=content,
                connection_id=github_connection_id,
                output_path=effective_output_path,
                filename=filename,
                commit_message=github_commit_message,
            )

            # Combine results
            return OutputWriteResult(
                success=github_result.success,
                content_id=library_result.content_id,
                github_url=github_result.github_url,
                github_commit_sha=github_result.github_commit_sha,
                error=github_result.error,
            )

        return library_result
