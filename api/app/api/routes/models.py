from fastapi import APIRouter
from app.api.schemas.models import ModelConfigResponse
from app.services import model_service

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelConfigResponse)
async def get_models():
    """
    Returns the hardcoded list of models and their supported sections.
    Source: app/config/models.yaml
    """
    data = model_service.get_model_config()
    return ModelConfigResponse(models=data)
