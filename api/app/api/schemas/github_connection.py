"""
Pydantic schemas for GitHub Connection API.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# Request Schemas
# ============================================================================

class GitHubConnectionCreate(BaseModel):
    """Request to create a new GitHub connection."""
    name: str = Field(..., min_length=1, max_length=255, description="Display name")
    repo: str = Field(
        ...,
        min_length=3,
        max_length=255,
        pattern=r"^[\w.-]+/[\w.-]+$",
        description="Repository in format 'owner/repo'"
    )
    branch: str = Field(default="main", max_length=100)
    token: Optional[str] = Field(None, description="Ignored in self-hosted mode; set GITHUB_TOKEN in root .env")


class GitHubConnectionUpdate(BaseModel):
    """Request to update a GitHub connection."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    branch: Optional[str] = Field(None, max_length=100)
    token: Optional[str] = Field(None, description="Ignored in self-hosted mode; set GITHUB_TOKEN in root .env")


# ============================================================================
# Response Schemas
# ============================================================================

class GitHubConnectionSummary(BaseModel):
    """Summary of a GitHub connection (for list views)."""
    id: str
    name: str
    repo: str
    branch: str
    is_valid: bool
    last_tested_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class GitHubConnectionDetail(BaseModel):
    """Full GitHub connection details."""
    id: str
    name: str
    repo: str
    branch: str
    is_valid: bool
    last_tested_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class GitHubConnectionList(BaseModel):
    """List of GitHub connections."""
    items: list[GitHubConnectionSummary]
    total: int


class GitHubConnectionTestResult(BaseModel):
    """Result of testing a GitHub connection."""
    id: str
    is_valid: bool
    message: str
    tested_at: datetime


# ============================================================================
# GitHub File Browser Schemas
# ============================================================================

class GitHubFileInfo(BaseModel):
    """Information about a file or directory in GitHub."""
    name: str
    path: str
    type: str = Field(description="'file' or 'dir'")
    size: Optional[int] = None  # Only for files
    download_url: Optional[str] = None  # Only for files


class GitHubBrowseResponse(BaseModel):
    """Response from browsing a GitHub directory."""
    connection_id: str
    repo: str
    branch: str
    path: str
    contents: list[GitHubFileInfo]


class GitHubFileContent(BaseModel):
    """Content of a file from GitHub."""
    connection_id: str
    path: str
    name: str
    content: str
    size: int
    encoding: str = "utf-8"


class GitHubImportRequest(BaseModel):
    """Request to import a file from GitHub as content."""
    path: str = Field(..., description="Path to file in repository")
    content_type: str = Field(..., description="Type of content to create")
    name: Optional[str] = Field(None, description="Name for the content (defaults to filename)")
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class GitHubCreateFolderRequest(BaseModel):
    """Request to create a folder in a GitHub repository."""
    path: str = Field(..., min_length=1, description="Path for the new folder (e.g. 'outputs/subfolder')")
    commit_message: Optional[str] = Field(None, description="Custom commit message")


class GitHubCreateFolderResponse(BaseModel):
    """Response from creating a folder in GitHub."""
    connection_id: str
    path: str
    success: bool
    message: str
