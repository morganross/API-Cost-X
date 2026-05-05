import logging
import yaml
from pathlib import Path
from typing import Dict, Any

_logger = logging.getLogger(__name__)

# Path to the models.yaml file
# apicostx/app/services/model_service.py -> apicostx/app/config/models.yaml
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"


def get_model_config() -> Dict[str, Dict[str, Any]]:
    """
    Reads the models.yaml file and returns the configuration.

    Returns dict of model_key -> {sections: [...], max_output_tokens: int, dr_native: bool}

    Handles both old format (model: [sections]) and new format
    (model: {sections: [...], max_output_tokens: int, dr_native: bool}).
    """
    if not CONFIG_PATH.exists():
        _logger.warning("Model config not found at %s", CONFIG_PATH)
        return {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            return {}

        result = {}
        for model_key, model_data in data.items():
            if isinstance(model_data, list):
                # Old format: model: [sections]
                result[model_key] = {
                    "sections": model_data,
                    "max_output_tokens": None,
                    "dr_native": False,
                }
            elif isinstance(model_data, dict):
                # New format: model: {sections: [...], max_output_tokens: int, dr_native: bool}
                result[model_key] = {
                    "sections": model_data.get("sections", []),
                    "max_output_tokens": model_data.get("max_output_tokens"),
                    "dr_native": model_data.get("dr_native", False),
                }
            else:
                _logger.warning("Unknown format for model %s: %s", model_key, type(model_data))
                continue

        return result
    except Exception as e:
        _logger.error("Error reading model config: %s", e)
        return {}
