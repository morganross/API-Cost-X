import logging
from typing import List, Dict, Any, Union, Optional
from app.infra.db.models.run import Run
from .models import TimelineRow, TimelinePhase, TimelineStatus
from app.services.compiled_run_config import extract_compiled_run_config_payload

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _config_for_run(run: Union[Run, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(run, dict):
        config = run.get("config")
        return config if isinstance(config, dict) else run
    return run.config or {}


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if value == "":
            continue
        if value == []:
            continue
        if value == {}:
            continue
        return value
    return None


def _normalize_model_names(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple)):
        return []
    names: List[str] = []
    seen: set[str] = set()
    for item in value:
        if not item or not isinstance(item, str):
            continue
        if item in seen:
            continue
        seen.add(item)
        names.append(item)
    return names


def build_expected_plan(
    run: Union[Run, Dict[str, Any]],
    run_data: Optional[Dict[str, Any]] = None,
) -> List[TimelineRow]:
    """
    Build the expected execution plan based on run configuration.
    """
    rows: List[TimelineRow] = []
    config = _config_for_run(run)
    compiled = extract_compiled_run_config_payload(config)

    if compiled:
        doc_ids = _as_list(compiled.get("document_ids"))
        generators = _normalize_model_names(compiled.get("generators"))
        models = list((compiled.get("model_settings") or {}).values())
        iterations = compiled.get("iterations")
        if not isinstance(iterations, int) or iterations < 1:
            iterations = 1
        eval_judge_models = _normalize_model_names(compiled.get("eval_judge_models"))
        eval_enabled = bool(compiled.get("enable_single_eval")) and bool(eval_judge_models)
        eval_iterations = compiled.get("eval_iterations")
        if not isinstance(eval_iterations, int) or eval_iterations < 1:
            eval_iterations = 1
        pairwise_enabled = bool(compiled.get("enable_pairwise"))
        pairwise_judge_models = eval_judge_models if pairwise_enabled else []
    else:
        doc_ids = _as_list(config.get("document_ids"))
        generators = []
        models = []
        iterations = config.get("iterations")
        if not isinstance(iterations, int) or iterations < 1:
            iterations = 1
        eval_judge_models = []
        eval_enabled = False
        eval_iterations = 1
        pairwise_enabled = False
        pairwise_judge_models = []

    run_index = 1

    # 1. Generation Phase
    # For each document x generator x model x iteration
    for doc_id in doc_ids:
        for gen in generators:
            for model_cfg in models:
                model_name = model_cfg.get("model")
                provider = model_cfg.get("provider")
                full_model_name = f"{provider}:{model_name}" if provider else str(model_name)

                for i in range(1, iterations + 1):
                    rows.append(TimelineRow(
                        expected_run_index=run_index,
                        phase=TimelinePhase.GENERATION,
                        eval_type="generation",
                        judge_model=full_model_name,
                        target=f"{doc_id} (Iter {i})",
                        status=TimelineStatus.PENDING
                    ))
                    run_index += 1

    # 2. Single Eval Phase
    if eval_enabled and not eval_judge_models:
        logger.warning("Skipping expected single-eval rows because judge models are missing from compiled run config")

    if eval_enabled and eval_judge_models:
        # One eval per generated artifact
        for doc_id in doc_ids:
            for gen in generators:
                for model_cfg in models:
                    model_name = model_cfg.get("model")
                    for judge_model in eval_judge_models:
                        for trial in range(1, eval_iterations + 1):
                            for i in range(1, iterations + 1):
                                rows.append(TimelineRow(
                                    expected_run_index=run_index,
                                    phase=TimelinePhase.PRECOMBINE_SINGLE,
                                    eval_type="single",
                                    judge_model=judge_model,
                                    target=f"Eval: {doc_id} / {model_name} / {i}",
                                    status=TimelineStatus.PENDING
                                ))
                                run_index += 1

    # 3. Pairwise Eval Phase
    if pairwise_enabled and not pairwise_judge_models:
        logger.warning("Skipping expected pairwise rows because judge models are missing from compiled run config")

    for judge_model in pairwise_judge_models if pairwise_enabled else []:
        rows.append(TimelineRow(
            expected_run_index=run_index,
            phase=TimelinePhase.PRECOMBINE_PAIRWISE,
            eval_type="pairwise",
            judge_model=judge_model,
            target="Dynamic Pairwise Tournament",
            status=TimelineStatus.PENDING
        ))
        run_index += 1

    return rows
