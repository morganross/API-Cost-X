from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class GptrConfig(BaseModel):
    """Configuration specific to GPT-Researcher adapter."""
    report_type: str = Field("research_report", description="Type of report to generate (research_report, deep, etc.)")
    tone: Optional[str] = Field(None, description="Tone of the report (Objective, etc.)")
    retriever: Optional[str] = Field(None, description="Retriever to use (tavily, duckduckgo, etc.)")
    source_urls: Optional[list[str]] = Field(None, description="List of source URLs to restrict research to")

    # Environment variable overrides for this specific run
    env_overrides: Dict[str, str] = Field(default_factory=dict, description="Environment variables to override for the subprocess")

    # Subprocess timeout and retry settings
    subprocess_timeout_minutes: int = Field(20, ge=10, le=45, description="Subprocess timeout in minutes (10-45)")
    subprocess_retries: int = Field(1, ge=0, le=3, description="Number of retries on timeout (0-3)")

    class Config:
        extra = "ignore"
