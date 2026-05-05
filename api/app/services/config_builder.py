"""
Shared model_settings builder for RunConfig assembly.

Both the CLI execute endpoint (presets.py) and the GUI start endpoint
(execution.py) need to build model_settings from per-generator
selected_models lists.  This module provides the shared logic so the
two code-paths stay in sync.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_INPUT_SOURCE_TYPE = "database"
DEFAULT_OUTPUT_DESTINATION = "library"
DEFAULT_OUTPUT_FILENAME_TEMPLATE = "{source_doc_name}_{winner_model}_{timestamp}"
DEFAULT_GITHUB_COMMIT_MESSAGE = "APICostX: Add winning document"
DEFAULT_KEY_MODE = "byok"

_KNOWN_OVERRIDE_SECTIONS = {
    "general",
    "concurrency",
    "fpf",
    "gptr",
    "dr",
    "ma",
    "aiq",
    "eval",
    "pairwise",
    "combine",
    "launch",
}


def _coerce_dict(value: Any) -> dict:
    """Return a shallow dict copy for plain dicts or Pydantic models."""
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return {}
    return {}


def _dedupe_strs(values: list[str]) -> list[str]:
    """Preserve order while removing empty or duplicate strings."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or not isinstance(value, str):
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _pick_first_defined(*values: Any, default: Any = None) -> Any:
    """Return the first non-None value, else default."""
    for value in values:
        if value is not None:
            return value
    return default


def _filter_known_keys(cfg: dict, *, section_name: str, allowed_keys: set[str]) -> dict:
    filtered = {key: value for key, value in cfg.items() if key in allowed_keys}
    dropped = sorted(key for key in cfg.keys() if key not in allowed_keys)
    if dropped:
        logger.warning(
            "[CONFIG NORMALIZE] Dropping unsupported %s keys: %s",
            section_name,
            dropped,
        )
    return filtered


def _normalize_general_override(general_config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(general_config),
        section_name="general",
        allowed_keys={
            "iterations",
            "use_byok_first",
            "save_run_logs",
            "post_combine_top_n",
            "run_estimate",
            "expose_criteria_to_generators",
        },
    )
    return cfg


def _normalize_concurrency_override(concurrency_config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(concurrency_config),
        section_name="concurrency",
        allowed_keys={
            "generation_concurrency",
            "eval_concurrency",
            "request_timeout",
            "fpf_max_retries",
            "fpf_retry_delay",
        },
    )
    return cfg


def _normalize_fpf_override(config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(config),
        section_name="fpf",
        allowed_keys={
            "enabled",
            "selected_models",
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "frequency_penalty",
            "presence_penalty",
            "stream_response",
            "include_metadata",
            "save_prompt_history",
            "web_search",
        },
    )
    return normalize_generation_config(cfg)


def _normalize_gptr_override(config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(config),
        section_name="gptr",
        allowed_keys={
            "enabled",
            "selected_models",
            "fast_llm_token_limit",
            "smart_llm_token_limit",
            "strategic_llm_token_limit",
            "browse_chunk_max_length",
            "summary_token_limit",
            "temperature",
            "max_search_results_per_query",
            "total_words",
            "max_iterations",
            "max_subtopics",
            "report_type",
            "report_source",
            "tone",
            "scrape_urls",
            "add_source_urls",
            "verbose_mode",
            "follow_links",
            "subprocess_timeout_minutes",
            "subprocess_retries",
        },
    )
    return normalize_generation_config(cfg)


def _normalize_dr_override(config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(config),
        section_name="dr",
        allowed_keys={
            "enabled",
            "selected_models",
            "breadth",
            "depth",
            "max_results",
            "concurrency_limit",
            "temperature",
            "max_tokens",
            "timeout",
            "search_provider",
            "enable_caching",
            "follow_links",
            "extract_code",
            "include_images",
            "semantic_search",
            "subprocess_timeout_minutes",
            "subprocess_retries",
        },
    )
    return normalize_generation_config(cfg)


def _normalize_ma_override(config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(config),
        section_name="ma",
        allowed_keys={
            "enabled",
            "selected_models",
            "max_agents",
            "communication_style",
            "enable_consensus",
            "enable_debate",
            "enable_voting",
            "max_rounds",
        },
    )
    return normalize_generation_config(cfg)


def _normalize_aiq_override(config: Any) -> dict:
    cfg = _filter_known_keys(
        _coerce_dict(config),
        section_name="aiq",
        allowed_keys={
            "enabled",
            "selected_models",
            "small_model",
            "profile",
            "agent_type",
            "report_min_words",
            "report_max_words",
            "intent_classifier_llm",
            "clarifier_llm",
            "clarifier_planner_llm",
            "shallow_research_llm",
            "orchestrator_llm",
            "researcher_llm",
            "planner_llm",
            "summary_model",
            "data_sources",
            "web_only",
            "preserve_debug_artifacts",
            "job_expiry_seconds",
            "timeout_seconds",
            "config_overrides",
            "advanced_yaml_overrides",
        },
    )
    return normalize_aiq_config(cfg)


def _normalize_eval_override(config: Any) -> dict:
    raw = _coerce_dict(config)
    if "eval_model" in raw:
        logger.info("[CONFIG NORMALIZE] Rewriting deprecated eval.eval_model into eval.judge_models")
    cfg = _filter_known_keys(
        raw,
        section_name="eval",
        allowed_keys={
            "enabled",
            "auto_run",
            "iterations",
            "pairwise_top_n",
            "judge_models",
            "eval_model",
            "timeout_seconds",
            "retries",
            "temperature",
            "max_tokens",
            "thinking_budget_tokens",
            "enable_semantic_similarity",
            "enable_factual_accuracy",
            "enable_coherence",
            "enable_relevance",
            "enable_completeness",
            "enable_citation",
        },
    )
    return normalize_eval_config(cfg)


def _normalize_pairwise_override(config: Any) -> dict:
    raw = _coerce_dict(config)
    if "judge_model" in raw:
        logger.info("[CONFIG NORMALIZE] Rewriting deprecated pairwise.judge_model into pairwise.judge_models")
    cfg = _filter_known_keys(
        raw,
        section_name="pairwise",
        allowed_keys={
            "enabled",
            "judge_models",
            "judge_model",
        },
    )
    return normalize_pairwise_config(cfg)


def _normalize_combine_override(config: Any) -> dict:
    raw = _coerce_dict(config)
    if "model" in raw:
        logger.info("[CONFIG NORMALIZE] Rewriting deprecated combine.model into combine.selected_models")
    cfg = _filter_known_keys(
        raw,
        section_name="combine",
        allowed_keys={
            "enabled",
            "selected_models",
            "model",
            "strategy",
            "max_tokens",
        },
    )
    return normalize_combine_config(cfg)


def extract_model_keys(selected_models_list) -> Optional[list[str]]:
    """Convert a selected_models list (dicts or strings) to provider:model key strings."""
    if not selected_models_list:
        return None
    keys: list[str] = []
    for entry in selected_models_list:
        if isinstance(entry, dict):
            p = entry.get("provider")
            m = entry.get("model")
            if p and m:
                keys.append(f"{p}:{m}")
        elif isinstance(entry, str):
            keys.append(entry)
    return keys if keys else None


def extract_judge_models(judge_models_list: Any, legacy_model: Any = None) -> list[str]:
    """Normalize judge models from list form or legacy single-model form."""
    judge_models = extract_model_keys(judge_models_list)
    if judge_models:
        return _dedupe_strs(judge_models)
    if isinstance(legacy_model, str) and legacy_model:
        return [legacy_model]
    return []


def extract_combine_models(combine_config: Any) -> list[str]:
    """Normalize combine model selection from list form or legacy single-model form."""
    cfg = _coerce_dict(combine_config)
    selected_models = extract_model_keys(cfg.get("selected_models"))
    if selected_models:
        return _dedupe_strs(selected_models)
    legacy_model = cfg.get("model")
    if isinstance(legacy_model, str) and legacy_model:
        return [legacy_model]
    return []


def normalize_generation_config(generator_config: Any) -> dict:
    """Normalize a generation phase config so enabled tracks selected models."""
    cfg = _coerce_dict(generator_config)
    if not cfg:
        return {}
    selected_models = extract_model_keys(cfg.get("selected_models")) or []
    if selected_models or "selected_models" in cfg:
        cfg["selected_models"] = selected_models
    cfg["enabled"] = bool(selected_models)
    return cfg


def normalize_aiq_config(aiq_config: Any) -> dict:
    """Normalize AI-Q config for ACM's profile-and-model-driven integration shape."""
    cfg = _coerce_dict(aiq_config)
    if not cfg:
        return {}

    selected_models = extract_model_keys(cfg.get("selected_models")) or []

    small_model = cfg.get("small_model")
    if not isinstance(small_model, str):
        small_model = ""

    profile = cfg.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        profile = "deep_web_default"

    agent_type = cfg.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type.strip():
        agent_type = "deep_researcher"

    report_min_words = cfg.get("report_min_words")
    if not isinstance(report_min_words, int):
        report_min_words = 4000
    report_min_words = max(100, report_min_words)

    report_max_words = cfg.get("report_max_words")
    if not isinstance(report_max_words, int):
        report_max_words = 5000
    report_max_words = max(report_min_words, report_max_words)

    intent_classifier_llm = cfg.get("intent_classifier_llm")
    if not isinstance(intent_classifier_llm, str) or not intent_classifier_llm.strip():
        intent_classifier_llm = "nemotron_llm_intent"

    clarifier_llm = cfg.get("clarifier_llm")
    if not isinstance(clarifier_llm, str) or not clarifier_llm.strip():
        clarifier_llm = "nemotron_nano_llm"

    clarifier_planner_llm = cfg.get("clarifier_planner_llm")
    if not isinstance(clarifier_planner_llm, str) or not clarifier_planner_llm.strip():
        clarifier_planner_llm = "nemotron_nano_llm"

    shallow_research_llm = cfg.get("shallow_research_llm")
    if not isinstance(shallow_research_llm, str) or not shallow_research_llm.strip():
        shallow_research_llm = "nemotron_nano_llm"

    orchestrator_llm = cfg.get("orchestrator_llm")
    if not isinstance(orchestrator_llm, str) or not orchestrator_llm.strip():
        orchestrator_llm = "gpt_oss_llm"

    researcher_llm = cfg.get("researcher_llm")
    if not isinstance(researcher_llm, str) or not researcher_llm.strip():
        researcher_llm = "nemotron_nano_llm"

    planner_llm = cfg.get("planner_llm")
    if not isinstance(planner_llm, str) or not planner_llm.strip():
        planner_llm = "gpt_oss_llm"

    summary_model = cfg.get("summary_model")
    if not isinstance(summary_model, str) or not summary_model.strip():
        summary_model = "summary_llm"

    data_sources = cfg.get("data_sources")
    if isinstance(data_sources, (list, tuple)):
        normalized_sources = _dedupe_strs(
            [str(value).strip() for value in data_sources if str(value).strip()]
        )
    else:
        normalized_sources = []

    config_overrides = _coerce_dict(cfg.get("config_overrides"))
    advanced_yaml_overrides = _coerce_dict(cfg.get("advanced_yaml_overrides"))

    cfg["selected_models"] = selected_models
    cfg["small_model"] = small_model.strip()
    cfg["profile"] = profile.strip()
    cfg["agent_type"] = agent_type.strip()
    cfg["report_min_words"] = report_min_words
    cfg["report_max_words"] = report_max_words
    cfg["intent_classifier_llm"] = intent_classifier_llm.strip()
    cfg["clarifier_llm"] = clarifier_llm.strip()
    cfg["clarifier_planner_llm"] = clarifier_planner_llm.strip()
    cfg["shallow_research_llm"] = shallow_research_llm.strip()
    cfg["orchestrator_llm"] = orchestrator_llm.strip()
    cfg["researcher_llm"] = researcher_llm.strip()
    cfg["planner_llm"] = planner_llm.strip()
    cfg["summary_model"] = summary_model.strip()
    cfg["data_sources"] = normalized_sources or ["web"]
    cfg["enabled"] = bool(selected_models)
    cfg["web_only"] = bool(cfg.get("web_only", True))
    cfg["preserve_debug_artifacts"] = bool(cfg.get("preserve_debug_artifacts", True))
    cfg["config_overrides"] = config_overrides
    cfg["advanced_yaml_overrides"] = advanced_yaml_overrides
    return cfg


def normalize_eval_config(eval_config: Any) -> dict:
    """Normalize single-eval config so enabled tracks judge model selection."""
    cfg = _coerce_dict(eval_config)
    if not cfg:
        return {}
    judge_models = extract_judge_models(cfg.get("judge_models"), cfg.get("eval_model"))
    if judge_models or "judge_models" in cfg or "eval_model" in cfg:
        cfg["judge_models"] = judge_models
    cfg["enabled"] = bool(judge_models)
    return cfg


def normalize_pairwise_config(pairwise_config: Any) -> dict:
    """Normalize pairwise config while preserving explicit enabled intent."""
    cfg = _coerce_dict(pairwise_config)
    if not cfg:
        return {}
    judge_models = extract_judge_models(cfg.get("judge_models"), cfg.get("judge_model"))
    if judge_models or "judge_models" in cfg or "judge_model" in cfg:
        cfg["judge_models"] = judge_models
    cfg["enabled"] = bool(cfg.get("enabled"))
    return cfg


def normalize_combine_config(combine_config: Any) -> dict:
    """Normalize combine config so enabled tracks selected models."""
    cfg = _coerce_dict(combine_config)
    if not cfg:
        return {}
    selected_models = extract_combine_models(cfg)
    if selected_models or "selected_models" in cfg or "model" in cfg:
        cfg["selected_models"] = selected_models
    cfg["enabled"] = bool(selected_models)
    return cfg


def normalize_launch_config(launch_config: Any, *, apply_defaults: bool = False) -> dict:
    """Normalize launch/input/output settings into one canonical dict."""
    cfg = _coerce_dict(launch_config)
    normalized: dict[str, Any] = {}

    def _clean_str(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    input_source_type = _clean_str(cfg.get("input_source_type"))
    if apply_defaults or input_source_type is not None:
        normalized["input_source_type"] = input_source_type or DEFAULT_INPUT_SOURCE_TYPE

    github_connection_id = _clean_str(cfg.get("github_connection_id"))
    if apply_defaults or github_connection_id is not None:
        normalized["github_connection_id"] = github_connection_id

    github_input_paths_raw = cfg.get("github_input_paths")
    github_input_paths = (
        _dedupe_strs(
            [str(value).strip() for value in (github_input_paths_raw or []) if str(value).strip()]
        )
        if isinstance(github_input_paths_raw, (list, tuple))
        else []
    )
    if apply_defaults or github_input_paths or "github_input_paths" in cfg:
        normalized["github_input_paths"] = github_input_paths

    github_output_path = _clean_str(cfg.get("github_output_path"))
    if apply_defaults or github_output_path is not None:
        normalized["github_output_path"] = github_output_path

    output_destination = _clean_str(cfg.get("output_destination"))
    if apply_defaults or output_destination is not None:
        normalized["output_destination"] = output_destination or DEFAULT_OUTPUT_DESTINATION

    output_filename_template = _clean_str(cfg.get("output_filename_template"))
    if apply_defaults or output_filename_template is not None:
        normalized["output_filename_template"] = (
            output_filename_template or DEFAULT_OUTPUT_FILENAME_TEMPLATE
        )

    github_commit_message = _clean_str(cfg.get("github_commit_message"))
    if apply_defaults or github_commit_message is not None:
        normalized["github_commit_message"] = (
            github_commit_message or DEFAULT_GITHUB_COMMIT_MESSAGE
        )

    if "prepend_source_first_line_frontmatter" in cfg or apply_defaults:
        normalized["prepend_source_first_line_frontmatter"] = bool(
            cfg.get("prepend_source_first_line_frontmatter", False)
        )

    if "key_mode" in cfg:
        logger.warning(
            "[CONFIG NORMALIZE] Dropping deprecated launch.key_mode; self-hosted runtime always uses local provider keys"
        )

    return normalized


def normalize_config_overrides(config_overrides: Any) -> dict:
    """Normalize known phase configs without inventing missing sections."""
    overrides = _coerce_dict(config_overrides)
    dropped_sections = sorted(section for section in overrides.keys() if section not in _KNOWN_OVERRIDE_SECTIONS)
    if dropped_sections:
        logger.warning(
            "[CONFIG NORMALIZE] Dropping unsupported top-level override sections: %s",
            dropped_sections,
        )

    normalized: dict[str, Any] = {}
    if "general" in overrides:
        normalized["general"] = _normalize_general_override(overrides.get("general"))
    if "concurrency" in overrides:
        normalized["concurrency"] = _normalize_concurrency_override(overrides.get("concurrency"))
    if "fpf" in overrides:
        normalized["fpf"] = _normalize_fpf_override(overrides.get("fpf"))
    if "gptr" in overrides:
        normalized["gptr"] = _normalize_gptr_override(overrides.get("gptr"))
    if "dr" in overrides:
        normalized["dr"] = _normalize_dr_override(overrides.get("dr"))
    if "ma" in overrides:
        normalized["ma"] = _normalize_ma_override(overrides.get("ma"))
    if "aiq" in overrides:
        normalized["aiq"] = _normalize_aiq_override(overrides.get("aiq"))
    if "eval" in overrides:
        normalized["eval"] = _normalize_eval_override(overrides.get("eval"))
    if "pairwise" in overrides:
        normalized["pairwise"] = _normalize_pairwise_override(overrides.get("pairwise"))
    if "combine" in overrides:
        normalized["combine"] = _normalize_combine_override(overrides.get("combine"))
    if "launch" in overrides:
        normalized["launch"] = normalize_launch_config(overrides.get("launch"))
    return normalized


def resolve_save_run_logs(general_config: Any, default: bool = True) -> bool:
    """Resolve the stored run-log toggle from canonical general config."""
    cfg = _coerce_dict(general_config)
    if isinstance(cfg.get("save_run_logs"), bool):
        return cfg["save_run_logs"]
    return default


def derive_generation_configs(config_overrides: Any) -> tuple[dict, dict, dict, dict, dict]:
    """Return normalized FPF/GPTR/DR/MA/AIQ configs from overrides."""
    overrides = normalize_config_overrides(config_overrides)
    return (
        normalize_generation_config(overrides.get("fpf")),
        normalize_generation_config(overrides.get("gptr")),
        normalize_generation_config(overrides.get("dr")),
        normalize_generation_config(overrides.get("ma")),
        normalize_aiq_config(overrides.get("aiq")),
    )


def derive_enabled_generators(
    fpf_config: Any = None,
    gptr_config: Any = None,
    dr_config: Any = None,
    ma_config: Any = None,
    aiq_config: Any = None,
) -> list[str]:
    """Derive active generators from canonical generator-specific selection state."""
    generator_map = {
        "fpf": normalize_generation_config(fpf_config),
        "gptr": normalize_generation_config(gptr_config),
        "dr": normalize_generation_config(dr_config),
        "ma": normalize_generation_config(ma_config),
        "aiq": normalize_aiq_config(aiq_config),
    }
    enabled: list[str] = []
    for name, cfg in generator_map.items():
        if cfg.get("selected_models"):
            enabled.append(name)
    return enabled


def resolve_runtime_generators(
    fpf_config: Any = None,
    gptr_config: Any = None,
    dr_config: Any = None,
    ma_config: Any = None,
    aiq_config: Any = None,
    *,
    generation_config_present: bool,
    fallback_generators: Optional[list[str]] = None,
) -> list[str]:
    """Resolve runtime generators from configs, falling back only for legacy payloads."""
    derived = derive_enabled_generators(fpf_config, gptr_config, dr_config, ma_config, aiq_config)
    if derived or generation_config_present:
        return derived
    return _dedupe_strs(list(fallback_generators or []))


def has_generation_selection(
    fpf_config: Any = None,
    gptr_config: Any = None,
    dr_config: Any = None,
    ma_config: Any = None,
    aiq_config: Any = None,
) -> bool:
    """True when any generator section has at least one selected model."""
    return bool(derive_enabled_generators(fpf_config, gptr_config, dr_config, ma_config, aiq_config))


def extract_aiq_model_keys(aiq_config: Any) -> Optional[list[str]]:
    """Return real provider:model keys selected for AI-Q runs."""
    cfg = normalize_aiq_config(aiq_config)
    if not cfg or not cfg.get("enabled"):
        return None
    selected_models = extract_model_keys(cfg.get("selected_models"))
    return selected_models or None


def model_entries_from_model_keys(*model_key_groups: Optional[list[str]]) -> list[dict[str, str]]:
    """Convert provider:model keys to legacy model entry dicts."""
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in model_key_groups:
        for key in group or []:
            if key in seen:
                continue
            provider, base_model = key.split(":", 1)
            entries.append({"provider": provider, "model": base_model})
            seen.add(key)
    return entries


def coerce_generator_names(values: Any) -> list[str]:
    """Normalize generator enums/strings into stable generator names."""
    names: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            names.append(value)
            continue
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            names.append(enum_value)
    return _dedupe_strs(names)


def coerce_model_entries(values: Any) -> list[dict[str, Any]]:
    """Normalize legacy model payloads while preserving known extra fields."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values or []:
        if isinstance(value, str):
            provider, sep, model = value.partition(":")
            if not sep or not provider or not model:
                continue
            payload = {"provider": provider, "model": model}
        else:
            payload = _coerce_dict(value)
            provider = payload.get("provider")
            model = payload.get("model")
            if not isinstance(provider, str) or not provider:
                continue
            if not isinstance(model, str) or not model:
                continue
        key = f"{payload['provider']}:{payload['model']}"
        if key in seen:
            continue
        seen.add(key)
        entries.append(payload)
    return entries


def derive_model_entries_from_configs(
    fpf_config: Any = None,
    gptr_config: Any = None,
    dr_config: Any = None,
    ma_config: Any = None,
    aiq_config: Any = None,
) -> list[dict[str, str]]:
    """Build legacy model entries from normalized generator configs."""
    return model_entries_from_model_keys(
        extract_model_keys(normalize_generation_config(fpf_config).get("selected_models")),
        extract_model_keys(normalize_generation_config(gptr_config).get("selected_models")),
        extract_model_keys(normalize_generation_config(dr_config).get("selected_models")),
        extract_model_keys(normalize_generation_config(ma_config).get("selected_models")),
        extract_aiq_model_keys(aiq_config),
    )


def validate_generator_models(
    generators: list[str],
    fpf_model_keys: Optional[list[str]] = None,
    gptr_model_keys: Optional[list[str]] = None,
    dr_model_keys: Optional[list[str]] = None,
    aiq_model_keys: Optional[list[str]] = None,
) -> None:
    """Raise if an enabled generator has no executable selection state."""
    enabled = set(generators)
    unsupported = sorted(enabled - {"fpf", "gptr", "dr", "aiq"})
    if unsupported:
        raise ValueError(f"Unsupported generators selected: {unsupported}")
    if "fpf" in enabled and not fpf_model_keys:
        raise ValueError("FPF enabled but no FPF selected_models set in preset")
    if "gptr" in enabled and not gptr_model_keys:
        raise ValueError("GPTR enabled but no GPTR selected_models set in preset")
    if "dr" in enabled and not dr_model_keys:
        raise ValueError("DR enabled but no DR selected_models set in preset")
    if "aiq" in enabled and not aiq_model_keys:
        raise ValueError("AI-Q enabled but no AI-Q selected_models are configured")


def build_model_settings(
    fpf_model_keys: Optional[list[str]],
    gptr_model_keys: Optional[list[str]],
    dr_model_keys: Optional[list[str]],
    aiq_model_keys: Optional[list[str]] = None,
    fpf_config: Optional[dict] = None,
    gptr_config: Optional[dict] = None,
    dr_config: Optional[dict] = None,
    aiq_config: Optional[dict] = None,
) -> tuple[dict[str, dict], list[str]]:
    """
    Build ``model_settings`` and a sorted unique ``model_names`` list from
    per-generator selected_models and their respective configs.

    Settings are keyed by generator plus model ID so report types cannot
    overwrite each other when they use the same provider model.

    Returns (model_settings, model_names).
    """
    fpf_config = _coerce_dict(fpf_config)
    gptr_config = _coerce_dict(gptr_config)
    dr_config = _coerce_dict(dr_config)
    aiq_config = _coerce_dict(aiq_config)

    model_settings: dict[str, dict] = {}
    model_names: list[str] = []

    def _add(
        model_keys: Optional[list[str]],
        generator_config: dict,
        label: str,
        require_max_tokens: bool = True,
    ) -> None:
        if not model_keys:
            return
        temperature = generator_config.get("temperature")
        max_tokens = generator_config.get("max_tokens")
        if temperature is None:
            raise ValueError(f"{label} enabled but temperature is missing in preset")
        if require_max_tokens and max_tokens is None:
            raise ValueError(f"{label} enabled but max_tokens is missing in preset")

        # GPTR/DR don't actually use max_tokens (they have their own limits)
        effective_max_tokens = max_tokens if max_tokens is not None else 8192

        thinking_budget_tokens = generator_config.get("thinking_budget_tokens")

        for key in model_keys:
            parts = key.split(":", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"Model key {key} is invalid; expected provider:model")
            provider, base_model = parts
            settings = {
                "provider": provider,
                "model": base_model,
                "temperature": temperature,
                "max_tokens": effective_max_tokens,
            }
            if thinking_budget_tokens is not None:
                settings["thinking_budget_tokens"] = thinking_budget_tokens
            settings["generator"] = label.lower()
            settings_key = f"{label.lower()}::{key}"
            if settings_key in model_settings:
                raise ValueError(f"Duplicate {label} model_settings key: {settings_key}")
            model_settings[settings_key] = settings
            model_names.append(key)

    _add(fpf_model_keys, fpf_config, "FPF", require_max_tokens=True)
    _add(gptr_model_keys, gptr_config, "GPTR", require_max_tokens=False)
    _add(dr_model_keys, dr_config, "DR", require_max_tokens=False)
    if aiq_model_keys:
        for key in aiq_model_keys:
            parts = key.split(":", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"AI-Q model key {key} is invalid; expected provider:model")
            provider, base_model = parts
            settings_key = f"aiq::{key}"
            if settings_key in model_settings:
                raise ValueError(f"Duplicate AI-Q model_settings key: {settings_key}")
            model_settings[settings_key] = {
                "provider": provider,
                "model": base_model,
                "temperature": 0.0,
                "max_tokens": 8192,
                "generator": "aiq",
            }
            model_names.append(key)

    unique_names = sorted(set(model_names))
    if not unique_names:
        raise ValueError("No models configured for enabled generators in preset")

    logger.info(f"Built scoped model_settings for {len(model_settings)} generator/model pairs")
    return model_settings, unique_names


def derive_runtime_config_core(
    *,
    generators: list[str],
    general_config: Any = None,
    fpf_config: Any = None,
    gptr_config: Any = None,
    dr_config: Any = None,
    aiq_config: Any = None,
    eval_config: Any = None,
    pairwise_config: Any = None,
    combine_config: Any = None,
    pairwise_enabled_fallback: bool = False,
    save_run_logs_override: Any = None,
) -> dict[str, Any]:
    """Derive the shared runtime state used to assemble RunConfig objects."""
    normalized_generators = _dedupe_strs(list(generators or []))
    general_cfg = _coerce_dict(general_config)
    fpf_cfg = normalize_generation_config(fpf_config)
    gptr_cfg = normalize_generation_config(gptr_config)
    dr_cfg = normalize_generation_config(dr_config)
    aiq_cfg = normalize_aiq_config(aiq_config)
    eval_cfg = normalize_eval_config(eval_config)
    pairwise_cfg = normalize_pairwise_config(pairwise_config)
    combine_cfg = normalize_combine_config(combine_config)

    judge_models = extract_judge_models(eval_cfg.get("judge_models"), eval_cfg.get("eval_model"))
    eval_enabled = bool(judge_models)
    pairwise_enabled = (
        bool(pairwise_cfg.get("enabled"))
        if pairwise_cfg
        else bool(pairwise_enabled_fallback)
    )
    combine_models = extract_combine_models(combine_cfg)
    combine_enabled = bool(combine_models)
    save_run_logs = (
        save_run_logs_override
        if isinstance(save_run_logs_override, bool)
        else resolve_save_run_logs(general_cfg)
    )

    fpf_model_keys = extract_model_keys(fpf_cfg.get("selected_models"))
    gptr_model_keys = extract_model_keys(gptr_cfg.get("selected_models"))
    dr_model_keys = extract_model_keys(dr_cfg.get("selected_models"))
    aiq_model_keys = extract_aiq_model_keys(aiq_cfg)

    validate_generator_models(
        normalized_generators,
        fpf_model_keys,
        gptr_model_keys,
        dr_model_keys,
        aiq_model_keys,
    )

    model_settings, model_names = build_model_settings(
        fpf_model_keys=fpf_model_keys,
        gptr_model_keys=gptr_model_keys,
        dr_model_keys=dr_model_keys,
        aiq_model_keys=aiq_model_keys,
        fpf_config=fpf_cfg,
        gptr_config=gptr_cfg,
        dr_config=dr_cfg,
        aiq_config=aiq_cfg,
    )

    return {
        "generators": normalized_generators,
        "judge_models": judge_models,
        "eval_enabled": eval_enabled,
        "pairwise_enabled": pairwise_enabled,
        "combine_models": combine_models,
        "combine_enabled": combine_enabled,
        "save_run_logs": save_run_logs,
        "fpf_model_keys": fpf_model_keys,
        "gptr_model_keys": gptr_model_keys,
        "dr_model_keys": dr_model_keys,
        "aiq_model_keys": aiq_model_keys,
        "model_settings": model_settings,
        "model_names": model_names,
    }


def _filtered_phase_runtime_options(phase_config: Any, *, blocked_keys: set[str]) -> dict[str, Any]:
    cfg = _coerce_dict(phase_config)
    return {
        key: value
        for key, value in cfg.items()
        if key not in blocked_keys and value is not None
    }


def compile_generation_adapter_extra(
    *,
    phase_config: Any,
    task_id: str,
    run_id: str,
    phase: str,
    document_id: str,
    iteration: int,
    temperature: float,
    max_tokens: int,
    key_mode: str,
    request_timeout: Optional[int],
    fpf_max_retries: int,
    fpf_retry_delay: float,
    thinking_budget_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """
    Compile generation adapter extras with explicit precedence.

    Precedence:
    1. phase_config contributes only non-conflicting behavioral settings
    2. runtime invariants and per-model settings overwrite blocked fields
    """
    blocked_keys = {
        "selected_models",
        "enabled",
        "provider",
        "model",
        "temperature",
        "max_tokens",
        "thinking_budget_tokens",
        "task_id",
        "run_id",
        "phase",
        "document_id",
        "iteration",
        "timeout",
        "fpf_max_retries",
        "fpf_retry_delay",
        "key_mode",
        "max_completion_tokens",
    }
    extra = _filtered_phase_runtime_options(phase_config, blocked_keys=blocked_keys)
    extra.update(
        {
            "task_id": task_id,
            "run_id": run_id,
            "phase": phase,
            "document_id": document_id,
            "iteration": iteration,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "fpf_max_retries": fpf_max_retries,
            "fpf_retry_delay": fpf_retry_delay,
            "key_mode": key_mode,
        }
    )
    if request_timeout is not None:
        extra["timeout"] = request_timeout
    if thinking_budget_tokens is not None:
        extra["thinking_budget_tokens"] = thinking_budget_tokens
    return extra


def compile_combine_adapter_extra(
    *,
    combine_config: Any,
    task_id: str,
    run_id: str,
    document_id: str,
    max_tokens: int,
    key_mode: str,
    request_timeout: Optional[int],
    fpf_max_retries: int,
    fpf_retry_delay: float,
) -> dict[str, Any]:
    """
    Compile combine adapter extras with explicit precedence.
    """
    blocked_keys = {
        "selected_models",
        "enabled",
        "strategy",
        "model",
        "max_tokens",
        "task_id",
        "run_id",
        "phase",
        "document_id",
        "timeout",
        "fpf_max_retries",
        "fpf_retry_delay",
        "key_mode",
        "max_completion_tokens",
    }
    extra = _filtered_phase_runtime_options(combine_config, blocked_keys=blocked_keys)
    extra.update(
        {
            "task_id": task_id,
            "run_id": run_id,
            "phase": "combine",
            "document_id": document_id,
            "max_completion_tokens": max_tokens,
            "fpf_max_retries": fpf_max_retries,
            "fpf_retry_delay": fpf_retry_delay,
            "key_mode": key_mode,
        }
    )
    if request_timeout is not None:
        extra["timeout"] = request_timeout
    return extra


def compile_executor_runtime_controls(
    *,
    eval_config: Any,
    combine_config: Any,
    concurrency_config: Any,
    launch_config: Any,
) -> dict[str, Any]:
    """
    Compile launch-critical scalar runtime controls with one explicit precedence order.

    Precedence:
    1. canonical phase config values
    2. canonical launch config values
    3. product defaults
    """
    eval_cfg = normalize_eval_config(eval_config)
    combine_cfg = normalize_combine_config(combine_config)
    concurrency_cfg = _coerce_dict(concurrency_config)
    raw_launch_cfg = _coerce_dict(launch_config)
    launch_cfg = normalize_launch_config(raw_launch_cfg, apply_defaults=True)

    if "eval_timeout" in concurrency_cfg:
        logger.warning(
            "[RUNTIME CONTROLS] Ignoring deprecated concurrency.eval_timeout=%s; canonical eval timeout comes from eval.timeout_seconds",
            concurrency_cfg.get("eval_timeout"),
        )

    resolved = {
        "eval_temperature": eval_cfg.get("temperature"),
        "eval_max_tokens": eval_cfg.get("max_tokens"),
        "eval_thinking_budget_tokens": eval_cfg.get("thinking_budget_tokens"),
        "eval_iterations": _pick_first_defined(eval_cfg.get("iterations"), default=1),
        "eval_timeout": _pick_first_defined(eval_cfg.get("timeout_seconds"), default=600),
        "eval_retries": _pick_first_defined(eval_cfg.get("retries"), default=3),
        "pairwise_top_n": eval_cfg.get("pairwise_top_n"),
        "combine_strategy": combine_cfg.get("strategy"),
        "combine_max_tokens": combine_cfg.get("max_tokens"),
        "generation_concurrency": _pick_first_defined(
            concurrency_cfg.get("generation_concurrency"),
            default=3,
        ),
        "eval_concurrency": _pick_first_defined(
            concurrency_cfg.get("eval_concurrency"),
            default=2,
        ),
        "request_timeout": _pick_first_defined(
            concurrency_cfg.get("request_timeout"),
            default=1200,
        ),
        "fpf_max_retries": _pick_first_defined(
            concurrency_cfg.get("fpf_max_retries"),
            default=3,
        ),
        "fpf_retry_delay": _pick_first_defined(
            concurrency_cfg.get("fpf_retry_delay"),
            default=1.0,
        ),
        "input_source_type": launch_cfg.get("input_source_type"),
        "github_connection_id": launch_cfg.get("github_connection_id"),
        "github_input_paths": list(launch_cfg.get("github_input_paths") or []),
        "github_output_path": launch_cfg.get("github_output_path"),
        "output_destination": launch_cfg.get("output_destination"),
        "output_filename_template": launch_cfg.get("output_filename_template"),
        "github_commit_message": launch_cfg.get("github_commit_message"),
        "prepend_source_first_line_frontmatter": bool(
            launch_cfg.get("prepend_source_first_line_frontmatter", False)
        ),
    }
    logger.debug(
        "[RUNTIME CONTROLS] Resolved eval_iterations=%s(source=%s) eval_timeout=%s(source=%s) "
        "eval_retries=%s(source=%s) request_timeout=%s(source=%s) fpf_max_retries=%s(source=%s) "
        "fpf_retry_delay=%s(source=%s) generation_concurrency=%s(source=%s) eval_concurrency=%s(source=%s) "
        "input_source_type=%s(source=%s)",
        resolved["eval_iterations"],
        "eval.iterations" if eval_cfg.get("iterations") is not None else "default",
        resolved["eval_timeout"],
        "eval.timeout_seconds" if eval_cfg.get("timeout_seconds") is not None else "default",
        resolved["eval_retries"],
        "eval.retries" if eval_cfg.get("retries") is not None else "default",
        resolved["request_timeout"],
        "concurrency.request_timeout" if concurrency_cfg.get("request_timeout") is not None else "default",
        resolved["fpf_max_retries"],
        "concurrency.fpf_max_retries" if concurrency_cfg.get("fpf_max_retries") is not None else "default",
        resolved["fpf_retry_delay"],
        "concurrency.fpf_retry_delay" if concurrency_cfg.get("fpf_retry_delay") is not None else "default",
        resolved["generation_concurrency"],
        "concurrency.generation_concurrency" if concurrency_cfg.get("generation_concurrency") is not None else "default",
        resolved["eval_concurrency"],
        "concurrency.eval_concurrency" if concurrency_cfg.get("eval_concurrency") is not None else "default",
        resolved["input_source_type"],
        "launch.input_source_type" if raw_launch_cfg.get("input_source_type") is not None else "launch.default",
    )
    return resolved


def derive_canonical_preset_config_state(
    *,
    config_overrides: Any,
) -> dict[str, Any]:
    """
    Derive launch state strictly from canonical config_overrides.

    This is the preset-backed SSOT path. It intentionally does not consult
    legacy ``generators``, legacy ``models``, or legacy ``pairwise_enabled``.
    """
    overrides = normalize_config_overrides(config_overrides)
    general_cfg = _coerce_dict(overrides.get("general"))
    concurrency_cfg = _coerce_dict(overrides.get("concurrency"))
    fpf_cfg = normalize_generation_config(overrides.get("fpf"))
    gptr_cfg = normalize_generation_config(overrides.get("gptr"))
    dr_cfg = normalize_generation_config(overrides.get("dr"))
    ma_cfg = normalize_generation_config(overrides.get("ma"))
    aiq_cfg = normalize_aiq_config(overrides.get("aiq"))
    eval_cfg = normalize_eval_config(overrides.get("eval"))
    pairwise_cfg = normalize_pairwise_config(overrides.get("pairwise"))
    combine_cfg = normalize_combine_config(overrides.get("combine"))
    launch_cfg = normalize_launch_config(overrides.get("launch"))

    generation_config_present = any(key in overrides for key in ("fpf", "gptr", "dr", "ma", "aiq"))
    generators = derive_enabled_generators(fpf_cfg, gptr_cfg, dr_cfg, ma_cfg, aiq_cfg)
    models = derive_model_entries_from_configs(fpf_cfg, gptr_cfg, dr_cfg, ma_cfg, aiq_cfg)
    evaluation_enabled = bool(
        extract_judge_models(eval_cfg.get("judge_models"), eval_cfg.get("eval_model"))
    )
    pairwise_enabled = bool(pairwise_cfg.get("enabled")) if "pairwise" in overrides else False

    return {
        "overrides": overrides,
        "general_config": general_cfg,
        "concurrency_config": concurrency_cfg,
        "fpf_config": fpf_cfg,
        "gptr_config": gptr_cfg,
        "dr_config": dr_cfg,
        "ma_config": ma_cfg,
        "aiq_config": aiq_cfg,
        "eval_config": eval_cfg,
        "pairwise_config": pairwise_cfg,
        "combine_config": combine_cfg,
        "launch_config": launch_cfg,
        "generation_config_present": generation_config_present,
        "generators": generators,
        "models": models,
        "evaluation_enabled": evaluation_enabled,
        "pairwise_enabled": pairwise_enabled,
        "save_run_logs": resolve_save_run_logs(general_cfg),
    }


def derive_persisted_config_state(
    *,
    config_overrides: Any,
    fallback_generators: Any = None,
    fallback_models: Any = None,
    pairwise_enabled_fallback: bool = False,
) -> dict[str, Any]:
    """Derive normalized persisted config state shared by presets and run creation."""
    overrides = normalize_config_overrides(config_overrides)
    general_cfg = _coerce_dict(overrides.get("general"))
    concurrency_cfg = _coerce_dict(overrides.get("concurrency"))
    fpf_cfg = normalize_generation_config(overrides.get("fpf"))
    gptr_cfg = normalize_generation_config(overrides.get("gptr"))
    dr_cfg = normalize_generation_config(overrides.get("dr"))
    ma_cfg = normalize_generation_config(overrides.get("ma"))
    aiq_cfg = normalize_aiq_config(overrides.get("aiq"))
    eval_cfg = normalize_eval_config(overrides.get("eval"))
    pairwise_cfg = normalize_pairwise_config(overrides.get("pairwise"))
    combine_cfg = normalize_combine_config(overrides.get("combine"))
    generation_config_present = any(key in overrides for key in ("fpf", "gptr", "dr", "ma", "aiq"))

    generators = resolve_runtime_generators(
        fpf_config=fpf_cfg,
        gptr_config=gptr_cfg,
        dr_config=dr_cfg,
        ma_config=ma_cfg,
        aiq_config=aiq_cfg,
        generation_config_present=generation_config_present,
        fallback_generators=coerce_generator_names(fallback_generators),
    )
    models = derive_model_entries_from_configs(fpf_cfg, gptr_cfg, dr_cfg, ma_cfg, aiq_cfg)
    if not models and not generation_config_present:
        models = coerce_model_entries(fallback_models)

    evaluation_enabled = bool(
        extract_judge_models(eval_cfg.get("judge_models"), eval_cfg.get("eval_model"))
    )
    pairwise_enabled = (
        bool(pairwise_cfg.get("enabled"))
        if "pairwise" in overrides
        else bool(pairwise_enabled_fallback)
    )

    return {
        "overrides": overrides,
        "general_config": general_cfg,
        "concurrency_config": concurrency_cfg,
        "fpf_config": fpf_cfg,
        "gptr_config": gptr_cfg,
        "dr_config": dr_cfg,
        "ma_config": ma_cfg,
        "aiq_config": aiq_cfg,
        "eval_config": eval_cfg,
        "pairwise_config": pairwise_cfg,
        "combine_config": combine_cfg,
        "generation_config_present": generation_config_present,
        "generators": generators,
        "models": models,
        "evaluation_enabled": evaluation_enabled,
        "pairwise_enabled": pairwise_enabled,
        "save_run_logs": resolve_save_run_logs(general_cfg),
    }
