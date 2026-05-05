"""
GitHub Input Service.

Fetches files from GitHub repositories for use as input documents.
Supports both individual files and folder paths (fetches all .md files in folder).
"""
import base64
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.repositories import GitHubConnectionRepository, ContentRepository
from app.infra.db.models.content import ContentType
from app.security.github_tokens import GitHubTokenError, get_github_token


logger = logging.getLogger(__name__)


@dataclass
class FetchedFile:
    """Represents a file fetched from GitHub."""
    path: str
    name: str
    content: str
    size: int


@dataclass
class GitHubFetchResult:
    """Result of fetching files from GitHub."""
    success: bool
    files: List[FetchedFile]
    document_ids: List[str]  # IDs of created Content items
    error: Optional[str] = None


class GitHubInputService:
    """
    Service for fetching input documents from GitHub.

    Handles:
    - Single file fetching
    - Folder fetching (recursively gets all text files)
    - Importing fetched files to Content Library as INPUT_DOCUMENT
    """

    def __init__(self, db: AsyncSession, user_uuid: str):
        self.db = db
        self.user_uuid = user_uuid
        self.gh_repo = GitHubConnectionRepository(db, user_uuid=user_uuid)
        self.content_repo = ContentRepository(db, user_uuid=user_uuid)

    async def _get_github_client(self, connection_id: str) -> Tuple[Any, Any, str]:
        """
        Get PyGithub client and repository for a connection.

        Returns:
            Tuple of (Github client, Repository object, branch name)
        """
        from github import Github

        connection = await self.gh_repo.get_by_id(connection_id)
        if not connection:
            raise ValueError(f"GitHub connection {connection_id} not found")

        try:
            token = get_github_token()
        except GitHubTokenError as exc:
            raise ValueError(str(exc)) from exc

        g = Github(token)
        repository = g.get_repo(connection.repo)

        return g, repository, connection.branch

    async def fetch_file(
        self,
        connection_id: str,
        file_path: str
    ) -> FetchedFile:
        """
        Fetch a single file from GitHub.

        Args:
            connection_id: ID of the GitHub connection
            file_path: Path to the file in the repository

        Returns:
            FetchedFile with content
        """
        from github.GithubException import GithubException

        try:
            _, repository, branch = await self._get_github_client(connection_id)

            clean_path = file_path.strip("/")
            file_content = repository.get_contents(clean_path, ref=branch)

            if file_content.type == "dir":
                raise ValueError(f"Path {file_path} is a directory, not a file")

            # Decode content
            content = base64.b64decode(file_content.content).decode("utf-8")

            return FetchedFile(
                path=file_content.path,
                name=file_content.name,
                content=content,
                size=file_content.size,
            )

        except GithubException as e:
            raise ValueError(f"GitHub error fetching {file_path}: {e.data.get('message', str(e))}")
        except UnicodeDecodeError:
            raise ValueError(f"File {file_path} is not a text file")

    async def fetch_folder(
        self,
        connection_id: str,
        folder_path: str,
        extensions: Optional[List[str]] = None,
        recursive: bool = True
    ) -> List[FetchedFile]:
        """
        Fetch all files from a folder in GitHub.

        Args:
            connection_id: ID of the GitHub connection
            folder_path: Path to the folder in the repository
            extensions: List of file extensions to include (e.g., [".md", ".txt"])
                       If None, includes all text files
            recursive: Whether to recursively fetch subfolders

        Returns:
            List of FetchedFile objects
        """
        from github.GithubException import GithubException

        if extensions is None:
            extensions = [".md", ".txt", ".rst", ".html"]

        try:
            _, repository, branch = await self._get_github_client(connection_id)

            clean_path = folder_path.strip("/") if folder_path != "/" else ""

            files: List[FetchedFile] = []
            paths_to_process = [clean_path]

            while paths_to_process:
                current_path = paths_to_process.pop(0)

                try:
                    if current_path:
                        contents = repository.get_contents(current_path, ref=branch)
                    else:
                        contents = repository.get_contents("", ref=branch)
                except GithubException:
                    logger.warning(f"Could not access path: {current_path}")
                    continue

                # Handle both single item and list
                if not isinstance(contents, list):
                    contents = [contents]

                for item in contents:
                    if item.type == "dir" and recursive:
                        paths_to_process.append(item.path)
                    elif item.type == "file":
                        # Check extension
                        has_valid_ext = any(item.name.endswith(ext) for ext in extensions)
                        if has_valid_ext:
                            try:
                                content = base64.b64decode(item.content).decode("utf-8")
                                files.append(FetchedFile(
                                    path=item.path,
                                    name=item.name,
                                    content=content,
                                    size=item.size,
                                ))
                            except (UnicodeDecodeError, AttributeError):
                                logger.warning(f"Skipping non-text file: {item.path}")

            return files

        except GithubException as e:
            raise ValueError(f"GitHub error browsing {folder_path}: {e.data.get('message', str(e))}")

    async def fetch_paths(
        self,
        connection_id: str,
        paths: List[str],
        extensions: Optional[List[str]] = None
    ) -> List[FetchedFile]:
        """
        Fetch multiple paths (files or folders) from GitHub.

        Args:
            connection_id: ID of the GitHub connection
            paths: List of file or folder paths
            extensions: File extensions to include for folders

        Returns:
            List of all fetched files
        """
        from github.GithubException import GithubException

        all_files: List[FetchedFile] = []

        for path in paths:
            try:
                # First, determine if it's a file or folder
                _, repository, branch = await self._get_github_client(connection_id)

                clean_path = path.strip("/")
                item = repository.get_contents(clean_path, ref=branch)

                if isinstance(item, list) or item.type == "dir":
                    # It's a folder
                    folder_files = await self.fetch_folder(
                        connection_id, path, extensions
                    )
                    all_files.extend(folder_files)
                else:
                    # It's a file
                    file = await self.fetch_file(connection_id, path)
                    all_files.append(file)

            except GithubException as e:
                logger.error(f"Error fetching path {path}: {e}")
                raise ValueError(f"Failed to fetch {path}: {e.data.get('message', str(e))}")

        return all_files

    async def import_files_as_content(
        self,
        files: List[FetchedFile],
        connection_name: str,
        run_id: Optional[str] = None,
        input_root_path: Optional[str] = None,
    ) -> List[str]:
        """
        Import fetched files as INPUT_DOCUMENT content items.

        Args:
            files: List of FetchedFile objects to import
            connection_name: Name of the GitHub connection (for description)
            run_id: Optional run ID for tagging
            input_root_path: Root folder path for computing relative paths (for folder mirroring)

        Returns:
            List of created content IDs
        """
        document_ids: List[str] = []

        for file in files:
            # Create tags
            tags = ["github-import", f"source:{connection_name}"]
            if run_id:
                tags.append(f"run:{run_id[:8]}")

            # Compute relative path from input root for folder mirroring
            variables = {}
            if input_root_path is not None:
                clean_root = input_root_path.strip("/")
                file_path = file.path
                if clean_root and file_path.startswith(clean_root + "/"):
                    rel_path = file_path[len(clean_root) + 1:]
                else:
                    rel_path = file_path
                variables["github_relative_path"] = rel_path

            # Create content
            content = await self.content_repo.create(
                name=file.name,
                content_type=ContentType.INPUT_DOCUMENT.value,
                body=file.content,
                variables=variables,
                description=f"Imported from GitHub: {file.path}",
                tags=tags,
            )

            document_ids.append(content.id)
            logger.info(f"Imported GitHub file as content: {file.path} -> {content.id}")

        return document_ids

    async def list_output_filenames(
        self,
        connection_id: str,
        output_path: str
    ) -> set:
        """
        List filenames already present in the GitHub output folder.

        Returns:
            Set of filenames (e.g. {"report.md", "summary.txt"})
        """
        from github.GithubException import GithubException

        try:
            _, repository, branch = await self._get_github_client(connection_id)
            clean_path = output_path.strip("/") if output_path != "/" else ""

            if clean_path:
                contents = repository.get_contents(clean_path, ref=branch)
            else:
                contents = repository.get_contents("", ref=branch)

            if not isinstance(contents, list):
                contents = [contents]

            filenames = {item.name for item in contents if item.type == "file"}
            logger.info(f"Found {len(filenames)} existing files in output folder '{output_path}'")
            return filenames

        except GithubException as e:
            if e.status == 404:
                logger.info(f"Output folder '{output_path}' does not exist yet — no files to skip")
                return set()
            raise
        except Exception as e:
            logger.warning(f"Could not list output folder '{output_path}': {e} — skipping skip-logic")
            return set()

    async def fetch_and_import(
        self,
        connection_id: str,
        paths: List[str],
        run_id: Optional[str] = None,
        skip_existing_output_path: Optional[str] = None,
        output_filename_template: Optional[str] = None,
        output_models: Optional[List[str]] = None,
    ) -> GitHubFetchResult:
        """
        Fetch files from GitHub and import them as content.

        This is the main entry point for run creation.

        Args:
            connection_id: ID of the GitHub connection
            paths: List of file or folder paths to fetch
            run_id: Optional run ID for tagging
            skip_existing_output_path: If set, skip input files whose output
                already exists in this GitHub output folder (template-aware)
            output_filename_template: Filename template used by output_writer
            output_models: List of model strings for skip prefix computation

        Returns:
            GitHubFetchResult with document IDs
        """
        try:
            # Get connection name for description
            connection = await self.gh_repo.get_by_id(connection_id)
            if not connection:
                return GitHubFetchResult(
                    success=False,
                    files=[],
                    document_ids=[],
                    error=f"GitHub connection {connection_id} not found"
                )

            # Fetch all files
            logger.info(f"Fetching GitHub files from connection {connection.name}: {paths}")
            files = await self.fetch_paths(connection_id, paths)

            if not files:
                return GitHubFetchResult(
                    success=False,
                    files=[],
                    document_ids=[],
                    error=f"No files found at paths: {paths}"
                )

            logger.info(f"Fetched {len(files)} files from GitHub")

            # Skip files whose output already exists (template-aware prefix match)
            if skip_existing_output_path and files:
                existing_names = await self.list_output_filenames(
                    connection_id, skip_existing_output_path
                )
                if existing_names:
                    before_count = len(files)
                    SENTINEL = '20000101_000000'
                    SENTINEL_DATE = '2000-01-01'

                    def compute_skip_prefixes(file_name):
                        import re as _re, os as _os
                        source_base, ext = _os.path.splitext(file_name)
                        if ext.lower() not in {'.md', '.txt', '.rst', '.html', '.htm'}:
                            source_base = file_name
                            ext = ''
                        clean_source = _re.sub(r'[^a-zA-Z0-9_-]', '_', source_base)
                        models = output_models or ['unknown']
                        prefixes = []
                        for model_str in models:
                            clean_model = _re.sub(r'[^a-zA-Z0-9_-]', '_', model_str.split(':')[-1])
                            tmpl = output_filename_template or '{source_doc_name}_{winner_model}_{timestamp}'
                            try:
                                rendered = tmpl.format(
                                    source_doc_name=clean_source,
                                    source_doc_ext=ext,
                                    winner_model=clean_model,
                                    run_id='00000000',
                                    timestamp=SENTINEL,
                                    date=SENTINEL_DATE,
                                    preset_name='preset',
                                )
                            except KeyError:
                                rendered = clean_source
                            idx = rendered.find(SENTINEL)
                            if idx != -1:
                                prefix = rendered[:idx]
                            else:
                                if not rendered.endswith('.md'):
                                    rendered += '.md'
                                prefix = rendered
                            prefixes.append(prefix)
                        return prefixes

                    def is_skippable(file_name):
                        prefixes = compute_skip_prefixes(file_name)
                        return any(
                            any(ex.startswith(p) or ex == p for ex in existing_names)
                            for p in prefixes
                        )

                    skipped = [f for f in files if is_skippable(f.name)]
                    files = [f for f in files if not is_skippable(f.name)]
                    for sf in skipped:
                        logger.info(f"Skipping '{sf.name}' — output already exists in '{skip_existing_output_path}'")
                    logger.info(
                        f"Skip-existing: {len(skipped)}/{before_count} input files skipped, "
                        f"{len(files)} remaining"
                    )

                    if not files:
                        return GitHubFetchResult(
                            success=True,
                            files=[],
                            document_ids=[],
                            error=f"All {before_count} input files already have outputs in '{skip_existing_output_path}'"
                        )

            # Determine input root path for folder mirroring
            # If a single folder path was given, use it as root; otherwise None
            input_root_path = None
            if len(paths) == 1:
                # Check if the path is a folder by seeing if fetched files are under it
                candidate_root = paths[0].strip("/")
                if files and all(f.path.startswith(candidate_root + "/") for f in files):
                    input_root_path = candidate_root

            # Import as content
            document_ids = await self.import_files_as_content(
                files,
                connection.name,
                run_id,
                input_root_path=input_root_path,
            )

            return GitHubFetchResult(
                success=True,
                files=files,
                document_ids=document_ids,
            )

        except ImportError:
            return GitHubFetchResult(
                success=False,
                files=[],
                document_ids=[],
                error="PyGithub not installed. Run: pip install PyGithub"
            )
        except Exception as e:
            logger.exception(f"Error fetching from GitHub: {e}")
            return GitHubFetchResult(
                success=False,
                files=[],
                document_ids=[],
                error=str(e)
            )
