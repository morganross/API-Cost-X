"""
Evaluation criteria management for APICostX.

Provides utilities for loading/validating criteria from Content Library.

IMPORTANT: No default criteria are provided. Criteria MUST be configured
in the Content Library and referenced in the preset. This ensures all
evaluation behavior is explicitly configured.
"""

import os
from typing import List, Optional

import yaml

from .models import EvaluationCriterion


def parse_criteria_yaml(criteria_yaml: str) -> List[EvaluationCriterion]:
    """
    Parse criteria YAML using the strict Content Library contract.

    Every criterion must be an object with explicit `name` and `description`.
    Hidden fallback descriptions are forbidden.
    """
    if not criteria_yaml:
        raise ValueError("No criteria YAML provided")

    data = yaml.safe_load(criteria_yaml)
    if not data or "criteria" not in data:
        raise ValueError("Invalid criteria file format: missing 'criteria' key")

    parsed_criteria: List[EvaluationCriterion] = []
    for item in data["criteria"]:
        if isinstance(item, str):
            raise ValueError(
                f"Criteria '{item}' must have a description - no fallback defaults allowed"
            )
        if not isinstance(item, dict):
            raise ValueError(f"Invalid criterion format: {item}")
        if "name" not in item or not item["name"]:
            raise ValueError(f"Criterion missing 'name': {item}")
        if "description" not in item or not item["description"]:
            raise ValueError(
                f"Criteria '{item['name']}' missing 'description' - no fallback defaults allowed"
            )
        parsed_criteria.append(
            EvaluationCriterion(
                name=item["name"],
                description=item["description"],
            )
        )

    errors = validate_criteria(parsed_criteria)
    if errors:
        raise ValueError(f"Invalid criteria: {'; '.join(errors)}")

    return parsed_criteria


def load_criteria_from_yaml(path: str) -> List[EvaluationCriterion]:
    """
    Load evaluation criteria from a YAML file.

    Expected format:
    ```yaml
    criteria:
      - name: factuality
        description: "..."
      - name: relevance
        description: "..."
    ```

    Args:
        path: Path to YAML file

    Returns:
        List of EvaluationCriterion objects

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Criteria file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return parse_criteria_yaml(f.read())


def save_criteria_to_yaml(criteria: List[EvaluationCriterion], path: str) -> None:
    """
    Save evaluation criteria to a YAML file.

    Args:
        criteria: List of criteria to save
        path: Output path
    """
    data = {
        "criteria": [
            {
                "name": c.name,
                "description": c.description,
            }
            for c in criteria
        ]
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def format_criteria_for_prompt(criteria: List[EvaluationCriterion]) -> str:
    """
    Format criteria list for inclusion in LLM prompt.

    Args:
        criteria: List of criteria

    Returns:
        Formatted string with bullet points
    """
    lines = [c.to_prompt_line() for c in criteria]
    return "\n".join(lines)


def validate_criteria(criteria: List[EvaluationCriterion]) -> List[str]:
    """
    Validate a list of criteria and return any errors.

    Args:
        criteria: List of criteria to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not criteria:
        errors.append("Criteria list is empty")
        return errors

    names_seen = set()
    for c in criteria:
        if not c.name:
            errors.append("Criterion has empty name")
        elif c.name in names_seen:
            errors.append(f"Duplicate criterion name: {c.name}")
        else:
            names_seen.add(c.name)

        if not c.description:
            errors.append(f"Criterion '{c.name}' has empty description")

    return errors


class CriteriaManager:
    """
    Manages evaluation criteria with caching and validation.

    IMPORTANT: No default criteria are provided. Criteria MUST be set
    explicitly via set_criteria() or loaded from a YAML path.
    """

    def __init__(self, custom_path: Optional[str] = None):
        """
        Initialize criteria manager.

        Args:
            custom_path: Optional path to custom criteria YAML file
        """
        self._custom_path = custom_path
        self._criteria: Optional[List[EvaluationCriterion]] = None

    @property
    def criteria(self) -> List[EvaluationCriterion]:
        """
        Get the active criteria list.

        Loads from custom path if set. Raises error if no criteria configured.
        Results are cached.

        Raises:
            RuntimeError: If no criteria have been configured
        """
        if self._criteria is None:
            if self._custom_path and os.path.exists(self._custom_path):
                self._criteria = load_criteria_from_yaml(self._custom_path)
            else:
                raise RuntimeError(
                    "No evaluation criteria configured. "
                    "Criteria must be set from Content Library via eval_criteria_id in preset. "
                    "Create criteria in Content Library and select them in your preset."
                )
        return self._criteria

    @property
    def names(self) -> List[str]:
        """Get list of criterion names."""
        return [c.name for c in self.criteria]

    def format_for_prompt(self) -> str:
        """Format criteria for LLM prompt."""
        return format_criteria_for_prompt(self.criteria)

    def reload(self) -> None:
        """Force reload of criteria from source."""
        self._criteria = None
        _ = self.criteria  # Trigger reload

    def set_criteria(self, criteria: List[EvaluationCriterion]) -> None:
        """
        Set custom criteria programmatically.

        Args:
            criteria: List of criteria to use

        Raises:
            ValueError: If criteria are invalid
        """
        errors = validate_criteria(criteria)
        if errors:
            raise ValueError(f"Invalid criteria: {'; '.join(errors)}")
        self._criteria = criteria
