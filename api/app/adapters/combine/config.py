from typing import Optional, List
from pydantic import BaseModel, Field

class CombineConfig(BaseModel):
    """Configuration for the Combine Phase."""
    enabled: bool = Field(True, description="Whether the combine phase is enabled")
    models: List[str] = Field(default_factory=list, description="List of models to use for combination (e.g. ['openai:gpt-4o'])")
    instructions_file: Optional[str] = Field(None, description="Path to custom combine instructions file")

    # Generator selection
    generator_type: str = Field("fpf", description="Which generator to use (fpf, gptr)")

    class Config:
        extra = "ignore"
