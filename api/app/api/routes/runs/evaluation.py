"""
Re-evaluation endpoint for runs.

Allows re-running evaluation on existing generated documents.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any

from app.infra.db.session import get_user_db, get_user_session_by_uuid
from app.infra.db.repositories import RunRepository, ContentRepository
from app.auth.middleware import get_current_user
from app.services.config_builder import extract_judge_models, normalize_config_overrides, normalize_eval_config
from app.utils.paths import get_user_run_path

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/runs/{run_id}/reevaluate")
async def reevaluate_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_db)
) -> dict:
    """
    Re-run evaluation on all generated documents from an existing run.

    This will:
    1. Load the run and its preset
    2. Re-evaluate all generated documents using the criteria from the preset
    3. Update pre_combine_evals and eval_scores

    Useful for testing evaluation fixes without regenerating reports.
    """
    from app.evaluation.single_doc import SingleDocEvaluator, SingleEvalConfig, DocumentInput

    repo = RunRepository(db, user_uuid=user['uuid'])
    run = await repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # --- Normalized read: load generated docs from run_generated_docs table ---
    from app.infra.db.repositories.run_results import RunResultsRepository
    rr_repo = RunResultsRepository(db)
    gen_doc_rows = await rr_repo.get_generated_docs(run_id)
    generated_docs = [
        {
            "id": row.doc_id,
            "doc_id": row.doc_id,
            "model": row.model,
            "generator": row.generator,
            "source_doc_id": row.source_doc_id,
            "iteration": row.iteration,
        }
        for row in gen_doc_rows
    ]
    if not generated_docs:
        raise HTTPException(status_code=400, detail="No generated documents to evaluate")

    # Get preset to load eval config
    from app.infra.db.repositories import PresetRepository
    preset_repo = PresetRepository(db, user_uuid=user['uuid'])
    preset = await preset_repo.get_by_id(run.preset_id) if run.preset_id else None
    if not preset:
        raise HTTPException(status_code=400, detail="Run has no associated preset")

    # Get eval config from run.config (snapshot at run time) or preset - NO FALLBACKS
    run_config = run.config or {}
    config_overrides = normalize_config_overrides(preset.config_overrides or {})
    eval_config = normalize_eval_config(run_config.get("eval_config") or config_overrides.get("eval"))
    if not eval_config:
        raise HTTPException(status_code=400, detail="No eval config found in run or preset config_overrides")

    # Get criteria from Content Library
    content_repo = ContentRepository(db, user_uuid=user['uuid'])
    eval_criteria_id = preset.eval_criteria_id or config_overrides.get("eval_criteria_id")
    custom_criteria = None
    if eval_criteria_id:
        content = await content_repo.get_by_id(eval_criteria_id)
        if content:
            custom_criteria = content.body
            logger.info(f"Loaded eval criteria from Content Library: {content.name}")

    # Get single eval instructions
    single_eval_id = preset.single_eval_instructions_id if hasattr(preset, 'single_eval_instructions_id') else None
    if not single_eval_id:
        single_eval_id = config_overrides.get("single_eval_instructions_id")
    single_eval_instructions = None
    if single_eval_id:
        content = await content_repo.get_by_id(single_eval_id)
        if content:
            single_eval_instructions = content.body

    # Build evaluator config
    judge_models = extract_judge_models(eval_config.get("judge_models"), eval_config.get("eval_model"))
    if not judge_models:
        raise HTTPException(status_code=400, detail="judge_models not configured in eval settings")
    max_tokens = eval_config.get("max_tokens")
    if not max_tokens:
        raise HTTPException(status_code=400, detail="max_tokens not configured in eval settings")
    iterations = eval_config.get("iterations")
    if iterations is None:
        raise HTTPException(status_code=400, detail="iterations not configured in eval settings")
    temperature = eval_config.get("temperature")
    if temperature is None:
        raise HTTPException(status_code=400, detail="temperature not configured in eval settings")
    timeout_seconds = eval_config.get("timeout_seconds")
    if timeout_seconds is None:
        raise HTTPException(status_code=400, detail="timeout_seconds not configured in eval settings")
    retries = eval_config.get("retries")
    if retries is None:
        raise HTTPException(status_code=400, detail="retries not configured in eval settings")

    single_config = SingleEvalConfig(
        iterations=iterations,
        judge_models=judge_models,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        retries=retries,
        custom_instructions=single_eval_instructions,
        custom_criteria=custom_criteria,
    )

    async def run_reevaluation():
        """Background task to re-evaluate all documents in parallel with incremental DB writes."""
        import asyncio
        from app.evaluation.models import SingleEvalResult

        async with get_user_session_by_uuid(user['uuid']) as session:
            repo_inner = RunRepository(session, user_uuid=user['uuid'])

            # Lock for serializing DB writes
            db_lock = asyncio.Lock()

            # Kilobyte-scale running average per doc — only legitimate in-memory state.
            # (sum_of_scores, count) per doc_id.  No compound dict.  No blob.
            _doc_eval_running: dict[str, tuple[float, int]] = {}
            all_evaluators_incremental: set[str] = set()
            all_criteria_incremental: set[str] = set()
            eval_count = 0

            try:
                evaluator = SingleDocEvaluator(single_config, user_uuid=user["uuid"])

                # Build list of document inputs (parallel preparation)
                doc_inputs = []
                doc_metadata = {}  # Map doc_id -> (source_doc_id, model)
                generated_dir = get_user_run_path(user['uuid'], run_id, "generated")

                for gen_doc in generated_docs:
                    doc_id = gen_doc.get("id")
                    source_doc_id = gen_doc.get("source_doc_id")
                    model = gen_doc.get("model")

                    if not doc_id:
                        continue

                    # Read the generated document content from local data directory
                    doc_path = generated_dir / f"{doc_id}.md"

                    if not doc_path.exists():
                        logger.warning(f"Generated doc not found: {doc_path}")
                        continue

                    content = doc_path.read_text(encoding="utf-8")
                    doc_inputs.append(DocumentInput(doc_id=doc_id, content=content))
                    doc_metadata[doc_id] = (source_doc_id, model)

                if not doc_inputs:
                    logger.warning(f"No documents found to re-evaluate for run {run_id}")
                    return

                logger.info(f"Starting parallel re-evaluation of {len(doc_inputs)} documents for run {run_id}")

                async def on_eval_complete(doc_id: str, judge_model: str, trial: int, result: SingleEvalResult):
                    """Callback: fires after each judge evaluation — writes to DB immediately."""
                    nonlocal eval_count

                    async with db_lock:
                        eval_count += 1

                        # Update kilobyte-scale running average (sum, count) for this doc.
                        _s, _c = _doc_eval_running.get(doc_id, (0.0, 0))
                        for s in result.scores:
                            _s += s.score
                            _c += 1
                        _doc_eval_running[doc_id] = (_s, _c)

                        all_evaluators_incremental.add(result.model)
                        for s in result.scores:
                            all_criteria_incremental.add(s.criterion)

                        source_doc_id, _gen_model = doc_metadata.get(doc_id, (None, None))

                        logger.info(f"[DB] Saved eval #{eval_count}: {doc_id} | {judge_model} trial={trial} avg={result.average_score:.2f}")

                        # Persist eval scores to run_eval_scores (SSOT).
                        from app.services.run_callbacks import write_eval_scores
                        await write_eval_scores(
                            run_id, user["uuid"], doc_id,
                            source_doc_id or "",
                            trial, result,
                            all_criteria_incremental, all_evaluators_incremental,
                        )

                # Use the parallel evaluate_documents method with incremental callback
                summaries = await evaluator.evaluate_documents(doc_inputs, on_eval_complete=on_eval_complete)

                logger.info(f"Re-evaluation complete for run {run_id}: {len(summaries)} documents, {eval_count} evals saved")

            except Exception as e:
                logger.error(f"Re-evaluation failed: {e}")
                raise

    background_tasks.add_task(run_reevaluation)
    return {"status": "reevaluation_started", "run_id": run_id, "doc_count": len(generated_docs)}
