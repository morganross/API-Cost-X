from pydantic import BaseModel
from typing import Dict, List, Optional


class ModelInfo(BaseModel):
    """Information about a model including sections and limits."""

    sections: List[str]
    max_output_tokens: Optional[int] = None
    dr_native: bool = False


class ModelConfigResponse(BaseModel):
    """Response containing model configurations."""

    models: Dict[str, ModelInfo]
