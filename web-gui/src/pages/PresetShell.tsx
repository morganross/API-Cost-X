import { useState, useEffect, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Save, RotateCcw, Sliders, Play, FileText, Library, ExternalLink, Github, Folder, ChevronRight, ChevronDown, RefreshCw, X } from 'lucide-react'
import { getGenScore, GenScoreType } from '@/data/genModelScores'
import { GEN_BADGE_QUANTILES, tierClass, renderBadge } from '@/data/badgeUtils'
import { Button } from '../components/ui/button'
import { useConfigStore } from '../stores/config'
import { useModelCatalog } from '../stores/modelCatalog'
import { getJudgeQuality } from '@/data/judgeQualityScores'
import { notify } from '@/stores/notifications'
import { runsApi } from '@/api/runs'
import { contentsApi, type ContentSummary } from '@/api/contents'
import { githubApi } from '@/api/github'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
import { estimateRuns } from '@/lib/estimateRuns'
import {
  listPresets,
  createPreset,
  updatePreset,
  getPreset,
  type PresetSummary,
  type PresetCreate,
  type PresetResponse,
  type GeneralConfigComplete as GeneralConfig,
  type FpfConfigComplete as FpfConfig,
  type GptrConfigComplete as GptrConfig,
  type DrConfigComplete as DrConfig,
  type MaConfigComplete as MaConfig,
  type AiqConfigComplete as AiqConfig,
  type EvalConfigComplete as EvalConfig,
  type ConcurrencyConfigComplete as ConcurrencyConfig,
  type CombineConfigComplete as CombineConfig,
  type RunEstimateSnapshot,
} from '@/api/presets'
import {
  GeneralPanel,
  FpfParamsPanel,
  GptrParamsPanel,
  DeepResearchPanel,
  MultiAgentPanel,
  AiqPanel,
  EvalPanel,
  CombinePanel,
  ConcurrencyPanel,
  RunEstimator,
} from '../components/config'
import ModelSelectorColumns from '../components/presets/ModelSelectorColumns'
import UnifiedPresetModelSection from '../components/presets/UnifiedPresetModelSection'
import { Section } from '../components/ui/section'

// Input source type
type InputSourceType = 'database' | 'github'
type PanelSectionKey = 'fpf' | 'gptr' | 'dr' | 'aiq' | 'ma' | 'eval' | 'combine'

const EXPENSIVE_MODEL_WARNING_DETAILS: Record<string, string> = {
  'openai:gpt-5.4-pro': '$30 input / $180 output',
  'openai:gpt-5.2-pro': '$21 input / $168 output',
  'openai:gpt-5-pro': '$15 input / $120 output',
  'openai:o3-pro': '$20 input / $80 output',
}

const EXPENSIVE_MODEL_WARNING_ORDER = [
  'openai:gpt-5.4-pro',
  'openai:gpt-5.2-pro',
  'openai:gpt-5-pro',
  'openai:o3-pro',
] as const

const EXPENSIVE_MODEL_WARNING_SET = new Set<string>(EXPENSIVE_MODEL_WARNING_ORDER)

function createCollapsedPanels(): Record<PanelSectionKey, boolean> {
  return {
    fpf: false,
    gptr: false,
    dr: false,
    aiq: false,
    ma: false,
    eval: false,
    combine: false,
  }
}

// Helper to format model to "provider:model" string for UI store
function formatModelString(provider: string, model: string): string {
  return `${provider}:${model}`;
}

function parseAiqAdvancedOverrides(value: string): Record<string, unknown> {
  const trimmed = value.trim()
  if (!trimmed) return {}
  const parsed = JSON.parse(trimmed)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('AI-Q advanced overrides must be a JSON object')
  }
  return parsed as Record<string, unknown>
}

type AiqStoreState = ReturnType<typeof useConfigStore.getState>['aiq']
type ConfigStoreState = ReturnType<typeof useConfigStore.getState>

function getExpensiveModelsInPresetConfig(config: ConfigStoreState): string[] {
  const selected = new Set<string>([
    ...config.fpf.selectedModels,
    ...config.gptr.selectedModels,
    ...config.dr.selectedModels,
    ...config.aiq.selectedModels,
    ...config.ma.selectedModels,
    ...config.combine.selectedModels,
    ...config.eval.judgeModels,
  ])

  return EXPENSIVE_MODEL_WARNING_ORDER.filter((model) => selected.has(model))
}

function confirmExpensiveModelAction(actionLabel: string, models: string[]): boolean {
  if (models.length === 0) return true

  const lines = models.map((model) => `- ${model} (${EXPENSIVE_MODEL_WARNING_DETAILS[model]})`)
  return window.confirm(
    `Warning: this preset includes very expensive models.\n\n${lines.join('\n')}\n\nClick OK to ${actionLabel}.`
  )
}

function buildAiqConfigOverrides(aiq: AiqStoreState): Record<string, unknown> {
  const llms: Record<string, Record<string, unknown>> = {
    nemotron_llm_intent: {
      model_name: aiq.intentModelName,
      temperature: aiq.intentTemperature,
      top_p: aiq.intentTopP,
      max_tokens: aiq.intentMaxTokens,
      num_retries: aiq.intentRetries,
      chat_template_kwargs: {
        enable_thinking: aiq.intentEnableThinking,
      },
    },
    nemotron_nano_llm: {
      model_name: aiq.nanoModelName,
      temperature: aiq.nanoTemperature,
      top_p: aiq.nanoTopP,
      max_tokens: aiq.nanoMaxTokens,
      num_retries: aiq.nanoRetries,
      chat_template_kwargs: {
        enable_thinking: aiq.nanoEnableThinking,
      },
    },
    gpt_oss_llm: {
      model_name: aiq.gptOssModelName,
      temperature: aiq.gptOssTemperature,
      top_p: aiq.gptOssTopP,
      max_tokens: aiq.gptOssMaxTokens,
      max_retries: aiq.gptOssMaxRetries,
    },
    summary_llm: {
      model_name: aiq.summaryModelName,
      temperature: aiq.summaryTemperature,
      max_tokens: aiq.summaryMaxTokens,
      num_retries: aiq.summaryRetries,
    },
    openai_gpt_5_2: {
      model_name: aiq.openaiGpt52ModelName,
      temperature: aiq.gptOssTemperature,
      top_p: aiq.gptOssTopP,
      max_tokens: aiq.gptOssMaxTokens,
      max_retries: aiq.gptOssMaxRetries,
    },
  }

  const functions: Record<string, Record<string, unknown>> = {
    web_search_tool: {
      max_results: aiq.webSearchMaxResults,
      max_content_length: aiq.webSearchMaxContentLength,
    },
    advanced_web_search_tool: {
      max_results: aiq.advancedWebSearchMaxResults,
      advanced_search: aiq.advancedWebSearchAdvancedSearch,
    },
    clarifier_agent: {
      max_turns: aiq.clarifierMaxTurns,
      enable_plan_approval: aiq.clarifierEnablePlanApproval,
      log_response_max_chars: aiq.clarifierLogResponseMaxChars,
    },
    shallow_research_agent: {
      max_llm_turns: aiq.shallowResearchMaxLlmTurns,
      max_tool_iterations: aiq.shallowResearchMaxToolIterations,
    },
    deep_research_agent: {
      max_loops: aiq.deepResearchMaxLoops,
      report_min_words: aiq.reportMinWords,
      report_max_words: aiq.reportMaxWords,
    },
    knowledge_search: {
      backend: aiq.knowledgeBackend,
      collection_name: aiq.knowledgeCollectionName,
      top_k: aiq.knowledgeTopK,
      generate_summary: aiq.knowledgeGenerateSummary,
      timeout: aiq.knowledgeTimeoutSeconds,
      summary_model: aiq.summaryModelBinding,
    },
  }

  const workflow: Record<string, unknown> = {
    enable_escalation: aiq.workflowEnableEscalation,
    enable_clarifier: aiq.workflowEnableClarifier,
    use_async_deep_research: aiq.workflowUseAsyncDeepResearch,
  }

  return {
    llms,
    functions,
    workflow,
  }
}

function readAiqOverrideSection(
  overrides: Record<string, unknown> | undefined,
  section: string,
  key: string
): Record<string, unknown> {
  const root = overrides?.[section]
  if (!root || typeof root !== 'object' || Array.isArray(root)) return {}
  const value = (root as Record<string, unknown>)[key]
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value as Record<string, unknown>
}

function readAiqWorkflowOverrides(overrides: Record<string, unknown> | undefined): Record<string, unknown> {
  const workflow = overrides?.workflow
  if (!workflow || typeof workflow !== 'object' || Array.isArray(workflow)) return {}
  return workflow as Record<string, unknown>
}

function readStringOverride(
  section: Record<string, unknown>,
  key: string,
  fallback: string
): string {
  const value = section[key]
  return typeof value === 'string' && value.trim() ? value : fallback
}

function readNumberOverride(
  section: Record<string, unknown>,
  key: string,
  fallback: number
): number {
  const value = section[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function readBooleanOverride(
  section: Record<string, unknown>,
  key: string,
  fallback: boolean
): boolean {
  const value = section[key]
  return typeof value === 'boolean' ? value : fallback
}

function readChatTemplateEnableThinking(section: Record<string, unknown>, fallback: boolean): boolean {
  const chatTemplate = section.chat_template_kwargs
  if (!chatTemplate || typeof chatTemplate !== 'object' || Array.isArray(chatTemplate)) return fallback
  const value = (chatTemplate as Record<string, unknown>).enable_thinking
  return typeof value === 'boolean' ? value : fallback
}

type ProviderFilterKey = 'openai' | 'google' | 'openrouter' | 'perplexity' | 'tavily' | 'anthropic' | 'others'

const PROVIDER_FILTER_ORDER: ProviderFilterKey[] = [
  'openai',
  'google',
  'openrouter',
  'perplexity',
  'tavily',
  'anthropic',
  'others',
]

const PROVIDER_FILTER_LABELS: Record<ProviderFilterKey, string> = {
  openai: 'openai',
  google: 'google',
  openrouter: 'open router',
  perplexity: 'perplexity',
  tavily: 'tavily',
  anthropic: 'anthropic',
  others: 'OTHERS',
}

function extractProviderFilterKey(modelKey: string): ProviderFilterKey {
  const colonIdx = modelKey.indexOf(':')
  if (colonIdx === -1) return 'others'
  const prefix = modelKey.slice(0, colonIdx)
  const rest = modelKey.slice(colonIdx + 1)

  if (prefix === 'openai' || prefix === 'openaidp') return 'openai'
  if (prefix === 'google' || prefix === 'googledp') return 'google'
  if (prefix === 'anthropic') return 'anthropic'
  if (prefix === 'tavily') return 'tavily'
  if (prefix === 'perplexity') return 'perplexity'
  if (prefix === 'openrouter') {
    if (rest.startsWith('perplexity/')) return 'perplexity'
    return 'openrouter'
  }

  return 'others'
}

// Extract key provider: the first segment before ':', used for key-presence checks
function extractKeyProvider(modelKey: string): string {
  const colonIdx = modelKey.indexOf(':')
  if (colonIdx === -1) return modelKey
  const prefix = modelKey.slice(0, colonIdx)
  if (prefix === 'openaidp') return 'openai'
  if (prefix === 'googledp') return 'google'
  return prefix
}

// ============================================================================
// Serialize Config Store -> PresetCreate (for saving)
// ============================================================================
type ConfigStore = ReturnType<typeof useConfigStore.getState>;

function hasOpenRouterFpfModel(selectedModels: string[]): boolean {
  return selectedModels.some((model) => model.startsWith('openrouter:'));
}

function coerceSearchInt(value: number, fallback: number, max: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(1, Math.min(max, Math.round(value)));
}

function buildOpenRouterWebSearchConfig(config: ConfigStore): FpfConfig['web_search'] | undefined {
  if (!hasOpenRouterFpfModel(config.fpf.selectedModels)) return undefined;
  const maxResults = coerceSearchInt(config.fpf.openrouterSearchMaxResults, 3, 10);
  const maxTotalResults = Math.max(
    maxResults,
    coerceSearchInt(config.fpf.openrouterSearchMaxTotalResults, 5, 20)
  );
  return {
    search_context_size: config.fpf.openrouterSearchContextSize,
    max_results: maxResults,
    max_total_results: maxTotalResults,
  };
}

interface GitHubInputConfig {
  inputSourceType: InputSourceType;
  githubConnectionId: string | null;
  githubInputPaths: string[];
  githubOutputPath: string | null;
}

function getDerivedActivation(config: ConfigStore) {
  const fpfEnabled = config.fpf.selectedModels.length > 0
  const gptrEnabled = config.gptr.selectedModels.length > 0
  const drEnabled = config.dr.selectedModels.length > 0
  const aiqEnabled = config.aiq.selectedModels.length > 0
  const maEnabled = config.ma.enabled
  const evalEnabled = config.eval.judgeModels.length > 0
  const combineEnabled = config.combine.selectedModels.length > 0
  const generationModelCount =
    config.fpf.selectedModels.length +
    config.gptr.selectedModels.length +
    config.dr.selectedModels.length +
    (aiqEnabled ? config.aiq.selectedModels.length : 0) +
    (maEnabled ? config.ma.selectedModels.length : 0)
  const pairwiseEnabled =
    config.eval.enablePairwise &&
    evalEnabled &&
    generationModelCount >= 2

  return {
    fpfEnabled,
    gptrEnabled,
    drEnabled,
    aiqEnabled,
    maEnabled,
    evalEnabled,
    combineEnabled,
    pairwiseEnabled,
    generationModelCount,
    generators: [
      fpfEnabled ? 'fpf' : null,
      gptrEnabled ? 'gptr' : null,
      drEnabled ? 'dr' : null,
      aiqEnabled ? 'aiq' : null,
      maEnabled ? 'ma' : null,
    ].filter((generator): generator is string => generator !== null),
  }
}

function toRunEstimateSnapshot(estimate: ReturnType<typeof estimateRuns>): RunEstimateSnapshot {
  return {
    generation: estimate.generation,
    single_eval: estimate.singleEval,
    pre_combine_pairwise: estimate.preCombinePairwise,
    combine: estimate.combine,
    post_combine_pairwise: estimate.postCombinePairwise,
    total: estimate.total,
    document_count: estimate.documentCount,
    per_doc: {
      generation: estimate.perDoc.generation,
      single_eval: estimate.perDoc.singleEval,
      pre_combine_pairwise: estimate.perDoc.preCombinePairwise,
      combine: estimate.perDoc.combine,
      post_combine_pairwise: estimate.perDoc.postCombinePairwise,
      total: estimate.perDoc.total,
    },
  }
}

function serializeConfigToPreset(
  config: ConfigStore,
  presetName: string,
  selectedInputDocIds: string[],
  selectedInstructionId: string | null,
  githubConfig: GitHubInputConfig,
  prependSourceFirstLineFrontmatter: boolean,
  keyMode: 'byok' | 'system' = 'system'
): PresetCreate {
  const derived = getDerivedActivation(config)
  const openrouterWebSearchConfig = buildOpenRouterWebSearchConfig(config)
  const runEstimate = toRunEstimateSnapshot(estimateRuns({
    documentCount: Math.max(1, selectedInputDocIds.length),
    fpfEnabled: derived.fpfEnabled,
    fpfModelCount: config.fpf.selectedModels.length,
    gptrEnabled: derived.gptrEnabled,
    gptrModelCount: config.gptr.selectedModels.length,
    drEnabled: derived.drEnabled,
    drModelCount: config.dr.selectedModels.length,
    aiqEnabled: derived.aiqEnabled,
    aiqModelCount: config.aiq.selectedModels.length,
    maEnabled: derived.maEnabled,
    maModelCount: config.ma.selectedModels.length,
    iterations: config.general.iterations,
    evalEnabled: derived.evalEnabled,
    pairwiseEnabled: derived.pairwiseEnabled,
    evalIterations: config.eval.iterations,
    judgeModelCount: config.eval.judgeModels.length,
    pairwiseTopN: config.eval.pairwiseTopN,
    combineEnabled: derived.combineEnabled,
    combineModelCount: config.combine.selectedModels.length,
  }))

  const general_config: GeneralConfig = {
    iterations: config.general.iterations,
    use_byok_first: keyMode === 'byok',
    save_run_logs: config.general.saveRunLogs,
    post_combine_top_n: config.combine.postCombineTopN,
    expose_criteria_to_generators: config.general.exposeCriteriaToGenerators,
    run_estimate: runEstimate,
  };

  // Serialize FpfConfig
  const fpf_config: FpfConfig = {
    enabled: derived.fpfEnabled,
    selected_models: config.fpf.selectedModels,
    max_tokens: config.fpf.maxTokens,
    temperature: config.fpf.temperature,
    top_p: config.fpf.topP,
    top_k: config.fpf.topK,
    frequency_penalty: config.fpf.frequencyPenalty,
    presence_penalty: config.fpf.presencePenalty,
    stream_response: config.fpf.streamResponse,
    include_metadata: config.fpf.includeMetadata,
    save_prompt_history: config.fpf.savePromptHistory,
    ...(openrouterWebSearchConfig ? { web_search: openrouterWebSearchConfig } : {}),
  };

  // Serialize GptrConfig
  const gptr_config: GptrConfig = {
    enabled: derived.gptrEnabled,
    selected_models: config.gptr.selectedModels,
    fast_llm_token_limit: config.gptr.fastLlmTokenLimit,
    smart_llm_token_limit: config.gptr.smartLlmTokenLimit,
    strategic_llm_token_limit: config.gptr.strategicLlmTokenLimit,
    browse_chunk_max_length: config.gptr.browseChunkMaxLength,
    summary_token_limit: config.gptr.summaryTokenLimit,
    temperature: config.gptr.temperature,
    max_search_results_per_query: config.gptr.maxSearchResultsPerQuery,
    total_words: config.gptr.totalWords,
    max_iterations: config.gptr.maxIterations,
    max_subtopics: config.gptr.maxSubtopics,
    report_type: config.gptr.reportType,
    report_source: config.gptr.reportSource,
    tone: config.gptr.tone,
    scrape_urls: config.gptr.scrapeUrls,
    add_source_urls: config.gptr.addSourceUrls,
    verbose_mode: config.gptr.verboseMode,
    follow_links: config.gptr.followLinks,
    // Subprocess timeout and retry settings
    subprocess_timeout_minutes: config.gptr.subprocessTimeoutMinutes,
    subprocess_retries: config.gptr.subprocessRetries,
  };

  // Serialize DrConfig
  const dr_config: DrConfig = {
    enabled: derived.drEnabled,
    selected_models: config.dr.selectedModels,
    breadth: config.dr.breadth,
    depth: config.dr.depth,
    max_results: config.dr.maxResults,
    concurrency_limit: config.dr.concurrencyLimit,
    temperature: config.dr.temperature,
    max_tokens: config.dr.maxTokens,
    timeout: config.dr.timeout,
    search_provider: config.dr.searchProvider,
    enable_caching: config.dr.enableCaching,
    follow_links: config.dr.followLinks,
    extract_code: config.dr.extractCode,
    include_images: config.dr.includeImages,
    semantic_search: config.dr.semanticSearch,
    // Subprocess timeout and retry settings
    subprocess_timeout_minutes: config.dr.subprocessTimeoutMinutes,
    subprocess_retries: config.dr.subprocessRetries,
  };

  // Serialize MaConfig
  const ma_config: MaConfig = {
    enabled: derived.maEnabled,
    selected_models: config.ma.selectedModels,
    max_agents: config.ma.maxAgents,
    communication_style: config.ma.communicationStyle,
    enable_consensus: config.ma.enableConsensus,
    enable_debate: config.ma.enableDebate,
    enable_voting: config.ma.enableVoting,
    max_rounds: config.ma.maxRounds,
  };

  const aiq_config: AiqConfig = {
    enabled: derived.aiqEnabled,
    selected_models: config.aiq.selectedModels,
    small_model: config.aiq.smallModel || null,
    profile: config.aiq.profile,
    agent_type: config.aiq.agentType,
    report_min_words: config.aiq.reportMinWords,
    report_max_words: config.aiq.reportMaxWords,
    intent_classifier_llm: config.aiq.intentClassifierLlm,
    clarifier_llm: config.aiq.clarifierLlm,
    clarifier_planner_llm: config.aiq.clarifierPlannerLlm,
    shallow_research_llm: config.aiq.shallowResearchLlm,
    orchestrator_llm: config.aiq.orchestratorLlm,
    researcher_llm: config.aiq.researcherLlm,
    planner_llm: config.aiq.plannerLlm,
    summary_model: config.aiq.summaryModelBinding,
    data_sources: config.aiq.webOnly ? ['web'] : config.aiq.dataSources,
    web_only: config.aiq.webOnly,
    preserve_debug_artifacts: config.aiq.preserveDebugArtifacts,
    job_expiry_seconds: config.aiq.jobExpirySeconds,
    timeout_seconds: config.aiq.timeoutSeconds,
    config_overrides: buildAiqConfigOverrides(config.aiq),
    advanced_yaml_overrides: parseAiqAdvancedOverrides(config.aiq.advancedYamlOverrides),
  };

  // Serialize EvalConfig
  const eval_config: EvalConfig = {
    enabled: derived.evalEnabled,
    auto_run: true,
    iterations: config.eval.iterations,
    pairwise_top_n: config.eval.pairwiseTopN,
    judge_models: config.eval.judgeModels,
    timeout_seconds: config.eval.timeoutSeconds,
    retries: config.eval.retries,
    temperature: config.eval.temperature,
    max_tokens: config.eval.maxTokens,
    thinking_budget_tokens: config.eval.thinkingBudget,
    enable_semantic_similarity: true,
    enable_factual_accuracy: config.eval.enableFactualAccuracy,
    enable_coherence: config.eval.enableCoherence,
    enable_relevance: config.eval.enableRelevance,
    enable_completeness: config.eval.enableCompleteness,
    enable_citation: config.eval.enableCitation,
  };

  // Serialize ConcurrencyConfig - use Zustand store values
  const concurrency_config: ConcurrencyConfig = {
    max_concurrent: config.concurrency.maxConcurrent,
    launch_delay: config.concurrency.launchDelay,
    generation_concurrency: config.concurrency.maxConcurrent,
    eval_concurrency: config.concurrency.evalConcurrency,
    request_timeout: config.concurrency.requestTimeout,
    fpf_max_retries: config.concurrency.fpfMaxRetries,
    fpf_retry_delay: config.concurrency.fpfRetryDelay,
  };

  // Serialize CombineConfig
  const combine_config: CombineConfig = {
    enabled: derived.combineEnabled,
    selected_models: config.combine.selectedModels,
    strategy: 'merge',
    max_tokens: config.combine.maxTokens,
  };

  return {
    name: presetName,
    description: 'Saved from Build Preset page',
    documents: selectedInputDocIds,
    pairwise_config: {
      enabled: derived.pairwiseEnabled,
      judge_models: config.eval.judgeModels,
    },
    general_config,
    fpf_config,
    gptr_config,
    dr_config,
    ma_config,
    aiq_config,
    eval_config,
    concurrency_config,
    combine_config,
    // Content Library instruction IDs
    single_eval_instructions_id: config.eval.singleEvalInstructionsId || undefined,
    pairwise_eval_instructions_id: config.eval.pairwiseEvalInstructionsId || undefined,
    eval_criteria_id: config.eval.evalCriteriaId || undefined,
    combine_instructions_id: config.combine.combineInstructionsId || undefined,
    generation_instructions_id: selectedInstructionId || undefined,
    // GitHub input source configuration
    input_source_type: githubConfig.inputSourceType,
    github_connection_id: githubConfig.githubConnectionId || undefined,
    github_input_paths: githubConfig.githubInputPaths.length > 0 ? githubConfig.githubInputPaths : undefined,
    github_output_path: githubConfig.githubOutputPath || undefined,
    output_destination: githubConfig.githubOutputPath ? 'github' : 'library',
    github_commit_message: 'APICostX: Add winning document',
    // Output filename template — always send a value so API service knows to write the winner
    output_filename_template: config.general.outputFilenameTemplate || '{source_doc_name}_{winner_model}_{timestamp}',
    // Opt-in frontmatter prepend behavior
    prepend_source_first_line_frontmatter: prependSourceFirstLineFrontmatter,
    key_mode: keyMode,
  };
}

// ============================================================================
// Deserialize PresetResponse -> Config Store (for loading)
// ============================================================================
function deserializePresetToConfig(
  preset: PresetResponse,
  config: ConfigStore
): {
  fpfInstructions: string;
  generationInstructionsId: string | null;
  inputSourceType: InputSourceType;
  githubConnectionId: string | null;
  githubInputPaths: string[];
  githubOutputPath: string | null;
  prependSourceFirstLineFrontmatter: boolean;
  keyMode: 'byok' | 'system';
} {
  // Load GeneralConfig
  if (preset.general_config) {
    config.updateGeneral({
      iterations: preset.general_config.iterations ?? config.general.iterations,
      saveRunLogs: preset.general_config.save_run_logs ?? preset.general_config.enable_logging ?? config.general.saveRunLogs,
      exposeCriteriaToGenerators: (preset.general_config as any).expose_criteria_to_generators ?? config.general.exposeCriteriaToGenerators,
      outputFilenameTemplate: (preset as any).output_filename_template ?? config.general.outputFilenameTemplate,
    });
  } else {
    // Fallback to legacy fields
    config.updateGeneral({
      iterations: preset.iterations ?? config.general.iterations,
    });
  }

  // Load FpfConfig
  if (preset.fpf_config) {
    const selectedModels = preset.fpf_config.selected_models ?? config.fpf.selectedModels
    config.updateFpf({
      enabled: selectedModels.length > 0,
      selectedModels,
      maxTokens: preset.fpf_config.max_tokens ?? config.fpf.maxTokens,
      temperature: preset.fpf_config.temperature ?? config.fpf.temperature,
      topP: preset.fpf_config.top_p ?? config.fpf.topP,
      topK: preset.fpf_config.top_k ?? config.fpf.topK,
      frequencyPenalty: preset.fpf_config.frequency_penalty ?? config.fpf.frequencyPenalty,
      presencePenalty: preset.fpf_config.presence_penalty ?? config.fpf.presencePenalty,
      streamResponse: preset.fpf_config.stream_response ?? config.fpf.streamResponse,
      includeMetadata: preset.fpf_config.include_metadata ?? config.fpf.includeMetadata,
      savePromptHistory: preset.fpf_config.save_prompt_history ?? config.fpf.savePromptHistory,
      openrouterSearchContextSize: preset.fpf_config.web_search?.search_context_size ?? config.fpf.openrouterSearchContextSize,
      openrouterSearchMaxResults: preset.fpf_config.web_search?.max_results ?? config.fpf.openrouterSearchMaxResults,
      openrouterSearchMaxTotalResults: preset.fpf_config.web_search?.max_total_results ?? config.fpf.openrouterSearchMaxTotalResults,
    });
  } else {
    // Fallback to legacy models array
    const modelNames = preset.models?.map(m => formatModelString(m.provider, m.model)) ?? config.fpf.selectedModels;
    const firstModel = preset.models?.[0];
    config.updateFpf({
      enabled: modelNames.length > 0,
      selectedModels: modelNames,
      temperature: firstModel?.temperature ?? config.fpf.temperature,
      maxTokens: firstModel?.max_tokens ?? config.fpf.maxTokens,
      thinkingBudget: (firstModel as any)?.thinking_budget_tokens ?? config.fpf.thinkingBudget,
    });
  }

  // Load GptrConfig
  if (preset.gptr_config) {
    const selectedModels = preset.gptr_config.selected_models ?? config.gptr.selectedModels
    config.updateGptr({
      enabled: selectedModels.length > 0,
      selectedModels,
      fastLlmTokenLimit: preset.gptr_config.fast_llm_token_limit ?? config.gptr.fastLlmTokenLimit,
      smartLlmTokenLimit: preset.gptr_config.smart_llm_token_limit ?? config.gptr.smartLlmTokenLimit,
      strategicLlmTokenLimit: preset.gptr_config.strategic_llm_token_limit ?? config.gptr.strategicLlmTokenLimit,
      browseChunkMaxLength: preset.gptr_config.browse_chunk_max_length ?? config.gptr.browseChunkMaxLength,
      summaryTokenLimit: preset.gptr_config.summary_token_limit ?? config.gptr.summaryTokenLimit,
      temperature: preset.gptr_config.temperature ?? config.gptr.temperature,
      maxSearchResultsPerQuery: preset.gptr_config.max_search_results_per_query ?? config.gptr.maxSearchResultsPerQuery,
      totalWords: preset.gptr_config.total_words ?? config.gptr.totalWords,
      maxIterations: preset.gptr_config.max_iterations ?? config.gptr.maxIterations,
      maxSubtopics: preset.gptr_config.max_subtopics ?? config.gptr.maxSubtopics,
      reportType: preset.gptr_config.report_type ?? config.gptr.reportType,
      reportSource: preset.gptr_config.report_source ?? config.gptr.reportSource,
      tone: preset.gptr_config.tone ?? config.gptr.tone,
      scrapeUrls: preset.gptr_config.scrape_urls ?? config.gptr.scrapeUrls,
      addSourceUrls: preset.gptr_config.add_source_urls ?? config.gptr.addSourceUrls,
      verboseMode: preset.gptr_config.verbose_mode ?? config.gptr.verboseMode,
      followLinks: preset.gptr_config.follow_links ?? config.gptr.followLinks,
      // Subprocess timeout and retry settings
      subprocessTimeoutMinutes: preset.gptr_config.subprocess_timeout_minutes ?? config.gptr.subprocessTimeoutMinutes,
      subprocessRetries: preset.gptr_config.subprocess_retries ?? config.gptr.subprocessRetries,
    });
  } else {
    // Fallback to legacy gptr_settings
    config.updateGptr({
      enabled: (preset.generators?.includes('gptr') ?? false) || config.gptr.selectedModels.length > 0,
      reportType: preset.gptr_settings?.report_type ?? config.gptr.reportType,
      reportSource: preset.gptr_settings?.report_source ?? config.gptr.reportSource,
      tone: preset.gptr_settings?.tone ?? config.gptr.tone,
      retriever: preset.gptr_settings?.retriever ?? config.gptr.retriever,
    });
  }

  // Load DrConfig
  if (preset.dr_config) {
    const selectedModels = preset.dr_config.selected_models ?? config.dr.selectedModels
    config.updateDr({
      enabled: selectedModels.length > 0,
      selectedModels,
      breadth: preset.dr_config.breadth ?? config.dr.breadth,
      depth: preset.dr_config.depth ?? config.dr.depth,
      maxResults: preset.dr_config.max_results ?? config.dr.maxResults,
      concurrencyLimit: preset.dr_config.concurrency_limit ?? config.dr.concurrencyLimit,
      temperature: preset.dr_config.temperature ?? config.dr.temperature,
      maxTokens: preset.dr_config.max_tokens ?? config.dr.maxTokens,
      timeout: preset.dr_config.timeout ?? config.dr.timeout,
      searchProvider: preset.dr_config.search_provider ?? config.dr.searchProvider,
      enableCaching: preset.dr_config.enable_caching ?? config.dr.enableCaching,
      followLinks: preset.dr_config.follow_links ?? config.dr.followLinks,
      extractCode: preset.dr_config.extract_code ?? config.dr.extractCode,
      includeImages: preset.dr_config.include_images ?? config.dr.includeImages,
      semanticSearch: preset.dr_config.semantic_search ?? config.dr.semanticSearch,
      // Subprocess timeout and retry settings
      subprocessTimeoutMinutes: preset.dr_config.subprocess_timeout_minutes ?? config.dr.subprocessTimeoutMinutes,
      subprocessRetries: preset.dr_config.subprocess_retries ?? config.dr.subprocessRetries,
    });
  }

  // Load MaConfig
  if (preset.ma_config) {
    config.updateMa({
      enabled: preset.ma_config.enabled ?? config.ma.enabled,
      selectedModels: preset.ma_config.selected_models ?? config.ma.selectedModels,
      maxAgents: preset.ma_config.max_agents ?? config.ma.maxAgents,
      communicationStyle: preset.ma_config.communication_style ?? config.ma.communicationStyle,
      enableConsensus: preset.ma_config.enable_consensus ?? config.ma.enableConsensus,
      enableDebate: preset.ma_config.enable_debate ?? config.ma.enableDebate,
      enableVoting: preset.ma_config.enable_voting ?? config.ma.enableVoting,
      maxRounds: preset.ma_config.max_rounds ?? config.ma.maxRounds,
    });
  }

  if (preset.aiq_config) {
    const aiqOverrides = (preset.aiq_config.config_overrides ?? {}) as Record<string, unknown>
    const intentOverrides = readAiqOverrideSection(aiqOverrides, 'llms', 'nemotron_llm_intent')
    const nanoOverrides = readAiqOverrideSection(aiqOverrides, 'llms', 'nemotron_nano_llm')
    const gptOssOverrides = readAiqOverrideSection(aiqOverrides, 'llms', 'gpt_oss_llm')
    const openaiOverrides = readAiqOverrideSection(aiqOverrides, 'llms', 'openai_gpt_5_2')
    const summaryOverrides = readAiqOverrideSection(aiqOverrides, 'llms', 'summary_llm')
    const webSearchOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'web_search_tool')
    const advancedWebSearchOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'advanced_web_search_tool')
    const clarifierOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'clarifier_agent')
    const shallowOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'shallow_research_agent')
    const deepOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'deep_research_agent')
    const knowledgeOverrides = readAiqOverrideSection(aiqOverrides, 'functions', 'knowledge_search')
    const workflowOverrides = readAiqWorkflowOverrides(aiqOverrides)
    config.updateAiq({
      enabled: (preset.aiq_config.selected_models?.length ?? 0) > 0,
      selectedModels: preset.aiq_config.selected_models ?? [],
      smallModel: preset.aiq_config.small_model ?? '',
      profile: preset.aiq_config.profile ?? config.aiq.profile,
      agentType: preset.aiq_config.agent_type ?? config.aiq.agentType,
      reportMinWords: readNumberOverride(
        deepOverrides,
        'report_min_words',
        preset.aiq_config.report_min_words ?? config.aiq.reportMinWords
      ),
      reportMaxWords: readNumberOverride(
        deepOverrides,
        'report_max_words',
        preset.aiq_config.report_max_words ?? config.aiq.reportMaxWords
      ),
      intentClassifierLlm: preset.aiq_config.intent_classifier_llm ?? config.aiq.intentClassifierLlm,
      clarifierLlm: preset.aiq_config.clarifier_llm ?? config.aiq.clarifierLlm,
      clarifierPlannerLlm: preset.aiq_config.clarifier_planner_llm ?? config.aiq.clarifierPlannerLlm,
      shallowResearchLlm: preset.aiq_config.shallow_research_llm ?? config.aiq.shallowResearchLlm,
      orchestratorLlm: preset.aiq_config.orchestrator_llm ?? config.aiq.orchestratorLlm,
      researcherLlm: preset.aiq_config.researcher_llm ?? config.aiq.researcherLlm,
      plannerLlm: preset.aiq_config.planner_llm ?? config.aiq.plannerLlm,
      summaryModelBinding: preset.aiq_config.summary_model ?? config.aiq.summaryModelBinding,
      intentModelName: readStringOverride(intentOverrides, 'model_name', config.aiq.intentModelName),
      intentTemperature: readNumberOverride(intentOverrides, 'temperature', config.aiq.intentTemperature),
      intentTopP: readNumberOverride(intentOverrides, 'top_p', config.aiq.intentTopP),
      intentMaxTokens: readNumberOverride(intentOverrides, 'max_tokens', config.aiq.intentMaxTokens),
      intentRetries: readNumberOverride(intentOverrides, 'num_retries', config.aiq.intentRetries),
      intentEnableThinking: readChatTemplateEnableThinking(intentOverrides, config.aiq.intentEnableThinking),
      nanoModelName: readStringOverride(nanoOverrides, 'model_name', config.aiq.nanoModelName),
      nanoTemperature: readNumberOverride(nanoOverrides, 'temperature', config.aiq.nanoTemperature),
      nanoTopP: readNumberOverride(nanoOverrides, 'top_p', config.aiq.nanoTopP),
      nanoMaxTokens: readNumberOverride(nanoOverrides, 'max_tokens', config.aiq.nanoMaxTokens),
      nanoRetries: readNumberOverride(nanoOverrides, 'num_retries', config.aiq.nanoRetries),
      nanoEnableThinking: readChatTemplateEnableThinking(nanoOverrides, config.aiq.nanoEnableThinking),
      gptOssModelName: readStringOverride(gptOssOverrides, 'model_name', config.aiq.gptOssModelName),
      gptOssTemperature: readNumberOverride(
        gptOssOverrides,
        'temperature',
        readNumberOverride(openaiOverrides, 'temperature', config.aiq.gptOssTemperature)
      ),
      gptOssTopP: readNumberOverride(
        gptOssOverrides,
        'top_p',
        readNumberOverride(openaiOverrides, 'top_p', config.aiq.gptOssTopP)
      ),
      gptOssMaxTokens: readNumberOverride(
        gptOssOverrides,
        'max_tokens',
        readNumberOverride(openaiOverrides, 'max_tokens', config.aiq.gptOssMaxTokens)
      ),
      gptOssMaxRetries: readNumberOverride(
        gptOssOverrides,
        'max_retries',
        readNumberOverride(openaiOverrides, 'max_retries', config.aiq.gptOssMaxRetries)
      ),
      openaiGpt52ModelName: readStringOverride(openaiOverrides, 'model_name', config.aiq.openaiGpt52ModelName),
      summaryModelName: readStringOverride(summaryOverrides, 'model_name', config.aiq.summaryModelName),
      summaryTemperature: readNumberOverride(summaryOverrides, 'temperature', config.aiq.summaryTemperature),
      summaryMaxTokens: readNumberOverride(summaryOverrides, 'max_tokens', config.aiq.summaryMaxTokens),
      summaryRetries: readNumberOverride(summaryOverrides, 'num_retries', config.aiq.summaryRetries),
      webSearchMaxResults: readNumberOverride(webSearchOverrides, 'max_results', config.aiq.webSearchMaxResults),
      webSearchMaxContentLength: readNumberOverride(
        webSearchOverrides,
        'max_content_length',
        config.aiq.webSearchMaxContentLength
      ),
      advancedWebSearchMaxResults: readNumberOverride(
        advancedWebSearchOverrides,
        'max_results',
        config.aiq.advancedWebSearchMaxResults
      ),
      advancedWebSearchAdvancedSearch: readBooleanOverride(
        advancedWebSearchOverrides,
        'advanced_search',
        config.aiq.advancedWebSearchAdvancedSearch
      ),
      clarifierMaxTurns: readNumberOverride(clarifierOverrides, 'max_turns', config.aiq.clarifierMaxTurns),
      clarifierEnablePlanApproval: readBooleanOverride(
        clarifierOverrides,
        'enable_plan_approval',
        config.aiq.clarifierEnablePlanApproval
      ),
      clarifierLogResponseMaxChars: readNumberOverride(
        clarifierOverrides,
        'log_response_max_chars',
        config.aiq.clarifierLogResponseMaxChars
      ),
      shallowResearchMaxLlmTurns: readNumberOverride(
        shallowOverrides,
        'max_llm_turns',
        config.aiq.shallowResearchMaxLlmTurns
      ),
      shallowResearchMaxToolIterations: readNumberOverride(
        shallowOverrides,
        'max_tool_iterations',
        config.aiq.shallowResearchMaxToolIterations
      ),
      deepResearchMaxLoops: readNumberOverride(deepOverrides, 'max_loops', config.aiq.deepResearchMaxLoops),
      workflowEnableEscalation: readBooleanOverride(
        workflowOverrides,
        'enable_escalation',
        config.aiq.workflowEnableEscalation
      ),
      workflowEnableClarifier: readBooleanOverride(
        workflowOverrides,
        'enable_clarifier',
        config.aiq.workflowEnableClarifier
      ),
      workflowUseAsyncDeepResearch: readBooleanOverride(
        workflowOverrides,
        'use_async_deep_research',
        config.aiq.workflowUseAsyncDeepResearch
      ),
      knowledgeBackend: readStringOverride(knowledgeOverrides, 'backend', config.aiq.knowledgeBackend),
      knowledgeCollectionName: readStringOverride(
        knowledgeOverrides,
        'collection_name',
        config.aiq.knowledgeCollectionName
      ),
      knowledgeTopK: readNumberOverride(knowledgeOverrides, 'top_k', config.aiq.knowledgeTopK),
      knowledgeGenerateSummary: readBooleanOverride(
        knowledgeOverrides,
        'generate_summary',
        config.aiq.knowledgeGenerateSummary
      ),
      knowledgeTimeoutSeconds: readNumberOverride(
        knowledgeOverrides,
        'timeout',
        config.aiq.knowledgeTimeoutSeconds
      ),
      dataSources: preset.aiq_config.data_sources ?? config.aiq.dataSources,
      webOnly: preset.aiq_config.web_only ?? config.aiq.webOnly,
      preserveDebugArtifacts: preset.aiq_config.preserve_debug_artifacts ?? config.aiq.preserveDebugArtifacts,
      jobExpirySeconds: preset.aiq_config.job_expiry_seconds ?? config.aiq.jobExpirySeconds,
      timeoutSeconds: preset.aiq_config.timeout_seconds ?? config.aiq.timeoutSeconds,
      advancedYamlOverrides: Object.keys(preset.aiq_config.advanced_yaml_overrides ?? {}).length > 0
        ? JSON.stringify(preset.aiq_config.advanced_yaml_overrides, null, 2)
        : '',
    });
  } else {
    config.updateAiq({
      enabled: false,
      selectedModels: [],
      smallModel: '',
      profile: 'deep_web_default',
      agentType: 'deep_researcher',
      reportMinWords: 3000,
      reportMaxWords: 5000,
      intentClassifierLlm: 'nemotron_llm_intent',
      clarifierLlm: 'nemotron_nano_llm',
      clarifierPlannerLlm: 'nemotron_nano_llm',
      shallowResearchLlm: 'nemotron_nano_llm',
      orchestratorLlm: 'gpt_oss_llm',
      researcherLlm: 'nemotron_nano_llm',
      plannerLlm: 'gpt_oss_llm',
      summaryModelBinding: 'summary_llm',
      intentModelName: 'nvidia/nemotron-3-nano-30b-a3b',
      intentTemperature: 0.5,
      intentTopP: 0.9,
      intentMaxTokens: 4096,
      intentRetries: 5,
      intentEnableThinking: true,
      nanoModelName: 'nvidia/nemotron-3-nano-30b-a3b',
      nanoTemperature: 0.1,
      nanoTopP: 0.3,
      nanoMaxTokens: 16384,
      nanoRetries: 5,
      nanoEnableThinking: true,
      gptOssModelName: 'openai/gpt-oss-120b',
      gptOssTemperature: 1.0,
      gptOssTopP: 1.0,
      gptOssMaxTokens: 256000,
      gptOssMaxRetries: 10,
      openaiGpt52ModelName: 'gpt-5.2',
      summaryModelName: 'nvidia/nemotron-mini-4b-instruct',
      summaryTemperature: 0.3,
      summaryMaxTokens: 100,
      summaryRetries: 5,
      webSearchMaxResults: 5,
      webSearchMaxContentLength: 1000,
      advancedWebSearchMaxResults: 2,
      advancedWebSearchAdvancedSearch: true,
      clarifierMaxTurns: 3,
      clarifierEnablePlanApproval: true,
      clarifierLogResponseMaxChars: 2000,
      shallowResearchMaxLlmTurns: 10,
      shallowResearchMaxToolIterations: 5,
      deepResearchMaxLoops: 3,
      workflowEnableEscalation: true,
      workflowEnableClarifier: true,
      workflowUseAsyncDeepResearch: true,
      knowledgeBackend: 'llamaindex',
      knowledgeCollectionName: 'acm-aiq',
      knowledgeTopK: 8,
      knowledgeGenerateSummary: true,
      knowledgeTimeoutSeconds: 30,
      dataSources: ['web'],
      webOnly: true,
      preserveDebugArtifacts: true,
      jobExpirySeconds: 86400,
      timeoutSeconds: 1800,
      advancedYamlOverrides: '',
    });
  }

  // Load EvalConfig
  if (preset.eval_config) {
    const judgeModels = preset.eval_config.judge_models ?? config.eval.judgeModels
    config.updateEval({
      enabled: judgeModels.length > 0,
      iterations: preset.eval_config.iterations ?? config.eval.iterations,
      pairwiseTopN: preset.eval_config.pairwise_top_n ?? config.eval.pairwiseTopN,
      judgeModels,
      timeoutSeconds: preset.eval_config.timeout_seconds ?? config.eval.timeoutSeconds,
      retries: preset.eval_config.retries ?? config.eval.retries,
      temperature: preset.eval_config.temperature ?? config.eval.temperature,
      maxTokens: preset.eval_config.max_tokens ?? config.eval.maxTokens,
      thinkingBudget: (preset.eval_config as any).thinking_budget_tokens ?? config.eval.thinkingBudget,
      enablePairwise: (preset.eval_config as any).enable_pairwise ?? preset.pairwise_config?.enabled ?? preset.pairwise?.enabled ?? config.eval.enablePairwise,
      enableFactualAccuracy: preset.eval_config.enable_factual_accuracy ?? config.eval.enableFactualAccuracy,
      enableCoherence: preset.eval_config.enable_coherence ?? config.eval.enableCoherence,
      enableRelevance: preset.eval_config.enable_relevance ?? config.eval.enableRelevance,
      enableCompleteness: preset.eval_config.enable_completeness ?? config.eval.enableCompleteness,
      enableCitation: preset.eval_config.enable_citation ?? config.eval.enableCitation,
    });
  } else {
    // Fallback to legacy evaluation
    config.updateEval({
      enabled: config.eval.judgeModels.length > 0,
    });
  }

  // Load instruction IDs from preset (top-level fields)
  config.updateEval({
    singleEvalInstructionsId: (preset as any).single_eval_instructions_id ?? null,
    pairwiseEvalInstructionsId: (preset as any).pairwise_eval_instructions_id ?? null,
    evalCriteriaId: (preset as any).eval_criteria_id ?? null,
  });

  // Load ConcurrencyConfig
  if (preset.concurrency_config) {
    config.updateConcurrency({
      maxConcurrent: preset.concurrency_config.max_concurrent ?? config.concurrency.maxConcurrent,
      evalConcurrency: preset.concurrency_config.eval_concurrency ?? config.concurrency.evalConcurrency,
      launchDelay: preset.concurrency_config.launch_delay ?? config.concurrency.launchDelay,
      requestTimeout: preset.concurrency_config.request_timeout ?? config.concurrency.requestTimeout,
    });
  }

  // Load CombineConfig
  if (preset.combine_config) {
    const selectedModels = preset.combine_config.selected_models ?? config.combine.selectedModels
    config.updateCombine({
      enabled: selectedModels.length > 0,
      selectedModels,
      maxTokens: preset.combine_config.max_tokens ?? config.combine.maxTokens,
      combineInstructionsId: (preset as any).combine_instructions_id ?? null,
      postCombineTopN: (preset.general_config as any)?.post_combine_top_n ?? config.combine.postCombineTopN,
    });
  }

  // Return extra local state values
  return {
    fpfInstructions: preset.fpf_settings?.prompt_template ?? '',
    generationInstructionsId: (preset as any).generation_instructions_id ?? null,
    // GitHub input source fields
    inputSourceType: (preset.input_source_type as InputSourceType) ?? 'database',
    githubConnectionId: preset.github_connection_id ?? null,
    githubInputPaths: preset.github_input_paths ?? [],
    githubOutputPath: preset.github_output_path ?? null,
    prependSourceFirstLineFrontmatter: (preset as any).prepend_source_first_line_frontmatter ?? false,
    keyMode: ((preset as any).key_mode as 'byok' | 'system') || 'system',
  };
}



interface PresetsProps {
  modelSectionVariant?: 'cards' | 'selectors' | 'unified'
  selectorLayout?: 'three-column' | 'four-column'
  inputSourcePlacement?: 'default' | 'top-panel'
}

export default function PresetShell({
  modelSectionVariant = 'cards',
  selectorLayout = 'three-column',
  inputSourcePlacement = 'default',
}: PresetsProps) {
  const navigate = useNavigate()
  const config = useConfigStore()
  const {
    models,
    fpfModels,
    fpfFreeModels,
    gptrModels,
    gptrFreeModels,
    drModels,
    drFreeModels,
    evalModels,
    evalFreeModels,
    combineModels,
    combineFreeModels,
    fetchModels,
    isLoading: isModelsLoading,
  } = useModelCatalog()

  const badgeQuantiles = GEN_BADGE_QUANTILES

  // Render score badges only.
  const renderModelBadges = (
    model: string,
    availability: { canEval: boolean; canFpf: boolean; canGptr: boolean; canDr: boolean }
  ) => {
    const jq   = availability.canEval  ? getJudgeQuality(model)      : undefined
    const fpf  = availability.canFpf   ? getGenScore('fpf',  model)  : undefined
    const gptr = availability.canGptr  ? getGenScore('gptr', model)  : undefined
    const dr   = availability.canDr    ? getGenScore('dr',   model)  : undefined
    const q    = badgeQuantiles

    return (
      <div className="flex items-center justify-between w-full px-1 pb-2 text-[10px] sm:text-xs font-mono text-gray-300 gap-0.5">
        <div className="flex gap-0.5">
          {renderBadge('FPF',    fpf?.score,   q.genScore,  true,  v => v.toFixed(2),        fpf  !== undefined, 'w-[4rem]',   `fpf-score-${model}`)}
          {renderBadge('GPT-R',  gptr?.score,  q.genScore, true,  v => v.toFixed(2),        gptr !== undefined, 'w-[5rem]',   `gptr-score-${model}`)}
          {renderBadge('DR',     dr?.score,    q.genScore,   true,  v => v.toFixed(2),        dr   !== undefined, 'w-[3.5rem]', `dr-score-${model}`)}
        </div>
        <div className="flex gap-0.5 shrink-0">
          {renderBadge('Eval',   jq?.sortino != null ? jq.sortino * 100 : undefined, q.evalScore, true,  v => `${v.toFixed(0)}%`, jq !== undefined, 'w-[4.5rem]', `eval-score-${model}`)}
        </div>
      </div>
    )
  }

  // UI State
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [selectedPresetId, setSelectedPresetId] = useState<string>('')
  const [presetName, setPresetName] = useState('')

  // UI State
  const [cardSortCol, setCardSortCol] = useState<string | null>(null)
  const [cardSortDir, setCardSortDir] = useState<'asc' | 'desc'>('asc')

  // Filter State
  const [filterProviders, setFilterProviders] = useState<Set<ProviderFilterKey> | null>(null) // null = not yet initialized
  const [showScored, setShowScored] = useState(true)
  const [showUnscored, setShowUnscored] = useState(false)
  const [showFree, setShowFree] = useState(true)
  const [hideKeyless, setHideKeyless] = useState(false)
  const [configuredKeyProviders, setConfiguredKeyProviders] = useState<Set<string>>(new Set())

  // Run State
  const [runName, setRunName] = useState('New Run')
  const [runDescription, setRunDescription] = useState('')
  const [fpfInstructions, setFpfInstructions] = useState<string>('')  // Instructions come from preset
  const [prependSourceFirstLineFrontmatter, setPrependSourceFirstLineFrontmatter] = useState(false)
  const [keyMode, setKeyMode] = useState<'byok' | 'system'>('system')
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Content Library State
  const [instructionContents, setInstructionContents] = useState<ContentSummary[]>([])
  const [selectedInstructionId, setSelectedInstructionId] = useState<string | null>(null)
  const [inputDocuments, setInputDocuments] = useState<ContentSummary[]>([])
  const [selectedInputDocIds, setSelectedInputDocIds] = useState<string[]>([])

  // GitHub Input Source State
  const [inputSourceType, setInputSourceType] = useState<InputSourceType>('database')
  const [githubConnectionId, setGithubConnectionId] = useState<string | null>(null)
  const [githubInputPaths, setGithubInputPaths] = useState<string[]>([])
  const [githubOutputPath, setGithubOutputPath] = useState<string | null>(null)
  const [showGitHubFileBrowser, setShowGitHubFileBrowser] = useState(false)
  const [githubBrowsePurpose, setGithubBrowsePurpose] = useState<'input' | 'output'>('input')
  const [showUnifiedTopInputSource, setShowUnifiedTopInputSource] = useState(false)
  const [expandedPanels, setExpandedPanels] = useState<Record<PanelSectionKey, boolean>>(createCollapsedPanels)

  // Load GitHub connections for input source dropdown
  const { data: githubConnectionsData } = useQuery({
    queryKey: ['github-connections'],
    queryFn: () => githubApi.list(),
  })
  const githubConnections = githubConnectionsData?.items ?? []

  // Load presets and content library on mount
  useEffect(() => {
    loadPresets()
    loadInstructionContents()
    loadInputDocuments()
  }, [])

    useEffect(() => {
      if (Object.keys(models).length === 0) {
        fetchModels()
      }
    }, [models, fetchModels])

  const loadPresets = async () => {
    try {
      console.log('Loading presets...')
      const result = await listPresets(1, 100)
      console.log('Loaded presets:', result)
      setPresets(result.items)
      if (result.items.length > 0 && !selectedPresetId) {
        // Don't auto-select for now
      }
    } catch (err) {
      console.error('Failed to load presets:', err)
      notify.error(`Failed to load presets: ${err}`)
    }
  }

  const loadInstructionContents = async () => {
    try {
      const items = await contentsApi.getGenerationInstructions()
      setInstructionContents(items)
    } catch (err) {
      console.error('Failed to load instruction contents:', err)
      // Don't notify, just log - content library might be empty
    }
  }

  const loadInputDocuments = async () => {
    try {
      const items = await contentsApi.getInputDocuments()
      setInputDocuments(items)
    } catch (err) {
      console.error('Failed to load input documents:', err)
    }
  }

  const handleSelectInstruction = async (contentId: string | null) => {
    setSelectedInstructionId(contentId)
    if (!contentId) {
      setFpfInstructions('')
      return
    }
    try {
      const content = await contentsApi.get(contentId)
      setFpfInstructions(content.body)
    } catch (err) {
      console.error('Failed to load instruction content:', err)
      notify.error('Failed to load instruction content')
    }
  }

  const toggleInputDoc = (docId: string) => {
    setSelectedInputDocIds(prev =>
      prev.includes(docId)
        ? prev.filter(d => d !== docId)
        : [...prev, docId]
    )
  }

  const handleSavePreset = async () => {
    if (!presetName) {
      notify.warning('Please enter a name for the preset')
      return
    }

    const expensiveModels = getExpensiveModelsInPresetConfig(config)
    if (!confirmExpensiveModelAction('save this preset', expensiveModels)) {
      return
    }

    try {
      // Use the new complete serialization function
      const githubConfig: GitHubInputConfig = {
        inputSourceType,
        githubConnectionId,
        githubInputPaths,
        githubOutputPath,
      };
      const presetData = serializeConfigToPreset(
        config,
        presetName,
        selectedInputDocIds,
        selectedInstructionId,
        githubConfig,
        prependSourceFirstLineFrontmatter,
        keyMode
      );

      // Check if we are updating an existing preset (by name match or ID)
      const existing = presets.find(p => p.id === selectedPresetId)
      if (existing && existing.name === presetName) {
        await updatePreset(existing.id, presetData)
        notify.success('Preset updated!')
      } else {
        const created = await createPreset(presetData)
        setSelectedPresetId(created.id)
        // Optimistically update the list
        setPresets(prev => {
            const newSummary: PresetSummary = {
                id: created.id,
                name: created.name,
                description: created.description,
                document_count: created.documents.length,
                model_count:
                  (created.fpf_config?.selected_models?.length ?? 0) +
                  (created.aiq_config?.selected_models?.length ?? 0),
                created_at: created.created_at,
                updated_at: created.updated_at,
                run_count: created.run_count,
                runnable: created.runnable,
            }
            return [...prev, newSummary]
        })
        notify.success('Preset created!')
      }

      await loadPresets()
    } catch (err) {
      console.error('Failed to save preset:', err)
      notify.error(err instanceof Error ? `Failed to save preset: ${err.message}` : 'Failed to save preset')
    }
  }

  const handlePresetChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value
    setSelectedPresetId(id)
    setExpandedPanels(createCollapsedPanels())

    if (!id) {
      // New preset selected - reset to defaults
      setPresetName('')
      setRunName('New Run')
      setRunDescription('')
      setSelectedInstructionId(null)
      setSelectedInputDocIds([])
      setFpfInstructions('')
      // Reset GitHub input source state
      setInputSourceType('database')
      setGithubConnectionId(null)
      setGithubInputPaths([])
      setGithubOutputPath(null)
      setPrependSourceFirstLineFrontmatter(false)
      setKeyMode('system')
      config.resetToDefaults()
      return
    }

    try {
      // Fetch full preset details
      const preset = await getPreset(id)
      console.log('Loaded preset details:', preset)

      // Update local state
      setPresetName(preset.name)
      setRunName(preset.name)
      setRunDescription(preset.description || '')
      setSelectedInputDocIds(preset.documents || [])

      // Use the new complete deserialization function
      const {
        fpfInstructions: loadedFpfInstructions,
        generationInstructionsId,
        inputSourceType: loadedInputSource,
        githubConnectionId: loadedGithubConnId,
        githubInputPaths: loadedGithubInputPaths,
        githubOutputPath: loadedGithubOutputPath,
        prependSourceFirstLineFrontmatter: loadedPrependSourceFirstLineFrontmatter,
        keyMode: loadedKeyMode,
      } = deserializePresetToConfig(preset, config);
      setFpfInstructions(loadedFpfInstructions);
      setSelectedInstructionId(generationInstructionsId);

      // Set GitHub input source state
      setInputSourceType(loadedInputSource);
      setGithubConnectionId(loadedGithubConnId);
      setGithubInputPaths(loadedGithubInputPaths);
      setGithubOutputPath(loadedGithubOutputPath);
      setPrependSourceFirstLineFrontmatter(loadedPrependSourceFirstLineFrontmatter);
      setKeyMode(loadedKeyMode);

    } catch (err) {
      console.error('Failed to load preset:', err)
      notify.error('Failed to load preset details')
    }
  }


    const fpfEligible = useMemo(() => new Set([...fpfModels, ...fpfFreeModels]), [fpfModels, fpfFreeModels])
    const gptrEligible = useMemo(() => new Set([...gptrModels, ...gptrFreeModels]), [gptrModels, gptrFreeModels])
    const drEligible = useMemo(() => new Set([...drModels, ...drFreeModels]), [drModels, drFreeModels])
    const evalEligible = useMemo(() => new Set([...evalModels, ...evalFreeModels]), [evalModels, evalFreeModels])
    const combineEligible = useMemo(() => new Set([...combineModels, ...combineFreeModels]), [combineModels, combineFreeModels])
    const isFreeModel = (model: string) => models[model]?.sections.includes('free') === true || model.includes(':free')

    const matrixModels = useMemo(() => {
      const combined = new Set<string>([
        ...fpfEligible,
        ...gptrEligible,
        ...drEligible,
        ...evalEligible,
        ...combineEligible,
      ])
      return Array.from(combined)
    }, [fpfEligible, gptrEligible, drEligible, evalEligible, combineEligible])

    const distinctProviders = useMemo(() => {
      const providerSet = new Set(matrixModels.map(extractProviderFilterKey))
      return PROVIDER_FILTER_ORDER.filter((provider) => providerSet.has(provider))
    }, [matrixModels])

    useEffect(() => {
      if (distinctProviders.length > 0 && filterProviders === null) {
        setFilterProviders(new Set(distinctProviders))
      }
    }, [distinctProviders, filterProviders])


    const truncateModelName = (name: string) => name.length > 30 ? `${name.slice(0, 30)}...` : name

    const matrixGridStyle = useMemo(() => ({
      ['--model-col-width' as any]: '45ch',
      ['--check-col' as any]: '20px',
    }), [])

    const toggleModelInList = (
      current: string[],
      model: string,
      checked: boolean
    ): string[] => {
      if (checked) {
        return Array.from(new Set([...current, model]))
      }
      return current.filter((m) => m !== model)
    }

    const handleMatrixToggle = (
      section: 'fpf' | 'gptr' | 'dr' | 'eval' | 'combine',
      model: string,
      checked: boolean
    ) => {
      if (checked) {
        setExpandedPanels((prev) => ({ ...prev, [section]: true }))
      }

      if (section === 'fpf') {
        const selectedModels = toggleModelInList(config.fpf.selectedModels, model, checked)
        config.updateFpf({ selectedModels, enabled: selectedModels.length > 0 })
        return
      }
      if (section === 'gptr') {
        const selectedModels = toggleModelInList(config.gptr.selectedModels, model, checked)
        config.updateGptr({ selectedModels, enabled: selectedModels.length > 0 })
        return
      }
      if (section === 'dr') {
        const selectedModels = toggleModelInList(config.dr.selectedModels, model, checked)
        config.updateDr({ selectedModels, enabled: selectedModels.length > 0 })
        return
      }
      if (section === 'eval') {
        const judgeModels = toggleModelInList(config.eval.judgeModels, model, checked)
        config.updateEval({ judgeModels, enabled: judgeModels.length > 0 })
        return
      }
      const selectedModels = toggleModelInList(config.combine.selectedModels, model, checked)
      config.updateCombine({ selectedModels, enabled: selectedModels.length > 0 })
    }

    const renderMatrixCheckbox = (
      enabled: boolean,
      checked: boolean,
      onChange: (checked: boolean) => void,
      testId: string
    ) => {
      if (!enabled) {
        return <span className="text-gray-600">—</span>
      }
      return (
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="w-4 h-4 rounded border-gray-600 text-blue-600 focus:ring-blue-500 bg-gray-700"
          data-testid={testId}
        />
      )
    }

  const handleStartRun = async () => {
    if (config.aiq.enabled && !selectedPresetId) {
      notify.warning('Save the preset before executing AI-Q from the presets page.')
      return
    }

    const expensiveModels = getExpensiveModelsInPresetConfig(config)
    if (!confirmExpensiveModelAction('execute this preset', expensiveModels)) {
      return
    }

    setIsSubmitting(true)
    try {
      const runRequest = {
        name: runName,
        description: runDescription,
        preset_id: selectedPresetId || undefined,
        tags: [] as string[],
      }

      const created = await runsApi.create(runRequest)
      await runsApi.start(created.id)
      // Navigate to execute page with run ID
      navigate(`/execute/${created.id}`)
    } catch (err) {
      console.error('Failed to start run:', err)
      notify.error(err instanceof Error ? err.message : 'Failed to start run.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const inputSourceBody = (
    <div className="space-y-4">
      {(inputSourcePlacement === 'top-panel' || modelSectionVariant === 'unified') && (
        <p className="text-sm text-gray-400">Where to load documents from</p>
      )}

      <div className="mb-4 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
        <label className="inline-flex items-start gap-2 text-sm text-gray-300 select-none">
          <input
            type="checkbox"
            checked={prependSourceFirstLineFrontmatter}
            onChange={(e) => setPrependSourceFirstLineFrontmatter(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-gray-600 bg-gray-700 text-blue-600 focus:ring-blue-500"
            data-field="prepend-source-first-line-frontmatter"
          />
          <span>
            <span className="block">Prepend source first line as frontmatter</span>
            <span className="mt-1 block text-xs text-gray-400">
              Adds YAML frontmatter to generated docs using the first line of the source document.
            </span>
          </span>
        </label>
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => setInputSourceType('database')}
          className={cn(
            'flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition-colors',
            inputSourceType === 'database'
              ? 'bg-green-500/20 text-green-400 border border-green-500'
              : 'bg-gray-700 text-gray-400 border border-transparent hover:bg-gray-600'
          )}
        >
          <FileText className="w-4 h-4" />
          Content Library
        </button>
        <button
          onClick={() => setInputSourceType('github')}
          className={cn(
            'flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition-colors',
            inputSourceType === 'github'
              ? 'bg-green-500/20 text-green-400 border border-green-500'
              : 'bg-gray-700 text-gray-400 border border-transparent hover:bg-gray-600'
          )}
        >
          <Github className="w-4 h-4" />
          GitHub
        </button>
      </div>

      {inputSourceType === 'database' ? (
        <>
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-gray-400">Select documents to process</span>
            <Link
              to="/content"
              className="inline-flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
            >
              <ExternalLink className="w-4 h-4" />
              Add Documents
            </Link>
          </div>
          {inputDocuments.length === 0 ? (
            <div className="text-center py-6 text-gray-400">
              <FileText className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p className="text-sm">No input documents in library</p>
              <Link to="/content" className="text-blue-400 hover:text-blue-300 text-sm mt-1 inline-block">
                Create one in Content Library →
              </Link>
            </div>
          ) : (
            <div className="grid gap-2 max-h-48 overflow-y-auto">
              {inputDocuments.map((doc) => (
                <label
                  key={doc.id}
                  data-testid={`input-doc-${doc.id}`}
                  className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                    selectedInputDocIds.includes(doc.id)
                      ? 'bg-blue-500/20 border border-blue-500'
                      : 'bg-gray-700/50 border border-transparent hover:bg-gray-700'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedInputDocIds.includes(doc.id)}
                    onChange={() => toggleInputDoc(doc.id)}
                    className="w-4 h-4 rounded border-gray-600 text-blue-600 focus:ring-blue-500 bg-gray-700"
                  />
                  <FileText className="w-5 h-5 text-blue-400" />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-gray-200">{doc.name}</div>
                    <div className="text-xs text-gray-500 line-clamp-1">{doc.body_preview}</div>
                  </div>
                </label>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">GitHub Connection</label>
            {githubConnections.length === 0 ? (
              <div className="text-center py-4 text-gray-400 bg-gray-700/50 rounded">
                <Github className="w-6 h-6 mx-auto mb-2 opacity-50" />
                <p className="text-sm">No GitHub connections</p>
                <a href="/github" className="text-blue-400 hover:text-blue-300 text-sm mt-1 inline-block">
                  Add one in Settings →
                </a>
              </div>
            ) : (
              <select
                value={githubConnectionId || ''}
                onChange={(e) => setGithubConnectionId(e.target.value || null)}
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-green-500"
              >
                <option value="">-- Select Connection --</option>
                {githubConnections.map((conn) => (
                  <option key={conn.id} value={conn.id}>
                    {conn.repo} ({conn.branch})
                  </option>
                ))}
              </select>
            )}
          </div>

          {githubConnectionId && (
            <>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm text-gray-400">Input Files/Folders</label>
                  <button
                    onClick={() => {
                      setGithubBrowsePurpose('input')
                      setShowGitHubFileBrowser(true)
                    }}
                    className="text-xs text-blue-400 hover:text-blue-300"
                  >
                    Browse...
                  </button>
                </div>
                {githubInputPaths.length === 0 ? (
                  <p className="text-xs text-gray-500 italic">No paths selected</p>
                ) : (
                  <div className="space-y-1 max-h-24 overflow-y-auto">
                    {githubInputPaths.map((p, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm bg-gray-700 px-2 py-1 rounded">
                        <Folder className="w-3 h-3 text-gray-400" />
                        <span className="flex-1 truncate">{p}</span>
                        <button
                          onClick={() => setGithubInputPaths(prev => prev.filter((_, idx) => idx !== i))}
                          className="text-gray-500 hover:text-red-400"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm text-gray-400">Output Folder (optional)</label>
                  <button
                    onClick={() => {
                      setGithubBrowsePurpose('output')
                      setShowGitHubFileBrowser(true)
                    }}
                    className="text-xs text-blue-400 hover:text-blue-300"
                  >
                    Browse...
                  </button>
                </div>
                {githubOutputPath ? (
                  <div className="flex items-center gap-2 text-sm bg-gray-700 px-2 py-1 rounded">
                    <Folder className="w-3 h-3 text-gray-400" />
                    <span className="flex-1 truncate">{githubOutputPath}</span>
                    <button
                      onClick={() => setGithubOutputPath(null)}
                      className="text-gray-500 hover:text-red-400"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ) : (
                  <p className="text-xs text-gray-500 italic">Results saved to database only</p>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )

  const inputSourceTopPanel =
    inputSourcePlacement === 'top-panel' || modelSectionVariant === 'unified' ? (
      <Section title="Input Source" icon={<Folder className="w-5 h-5" />} defaultExpanded={true}>
        {inputSourceBody}
      </Section>
    ) : undefined

  return (
    <div className="bg-gray-900 text-gray-100">
      {/* Hidden page state for automation/testing */}
      <div
        data-testid="page-state"
        data-preset-id={selectedPresetId || ''}
        data-preset-name={presetName}
        data-run-name={runName}
        data-selected-instruction={selectedInstructionId || ''}
        data-selected-documents={JSON.stringify(selectedInputDocIds)}
        data-fpf-enabled={config.fpf.selectedModels.length > 0}
        data-fpf-models={JSON.stringify(config.fpf.selectedModels)}
        data-gptr-enabled={config.gptr.selectedModels.length > 0}
        data-gptr-models={JSON.stringify(config.gptr.selectedModels)}
        data-aiq-enabled={config.aiq.selectedModels.length > 0}
        data-aiq-profile={config.aiq.profile}
        data-eval-enabled={config.eval.judgeModels.length > 0}
        data-eval-models={JSON.stringify(config.eval.judgeModels)}
        data-combine-enabled={config.combine.selectedModels.length > 0}
        data-combine-models={JSON.stringify(config.combine.selectedModels)}
        data-prepend-frontmatter={prependSourceFirstLineFrontmatter}
        data-iterations={config.general.iterations}
        data-is-submitting={isSubmitting}
        className="hidden"
        aria-hidden="true"
      />
      <section className="border-b border-gray-700 bg-gray-800/60">
        <div className="mx-auto max-w-7xl px-4 py-4">
          <div className="mb-4 flex items-start gap-3">
            <Sliders className="w-8 h-8 text-blue-400" />
            <div>
              <h1 className="text-2xl font-bold">Build Preset</h1>
              <p className="text-sm text-gray-400">
                Configure parameters and save as a preset
              </p>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
                <label htmlFor="execution-name" className="block text-xs text-gray-400 mb-1">Execution Name</label>
                <input
                    id="execution-name"
                    name="execution-name"
                    type="text"
                    value={runName}
                    onChange={(e) => setRunName(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:border-blue-500 outline-none"
                    data-field="execution-name"
                />
            </div>
            <div>
                <label htmlFor="description" className="block text-xs text-gray-400 mb-1">Description</label>
                <input
                    id="description"
                    name="description"
                    type="text"
                    value={runDescription}
                    onChange={(e) => setRunDescription(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:border-blue-500 outline-none"
                    placeholder="Optional description..."
                    data-field="description"
                />
            </div>
          </div>
        </div>
      </section>

      <div
        className="sticky z-20 border-b border-gray-700 bg-gray-900/95 backdrop-blur supports-[backdrop-filter]:bg-gray-900/85"
        style={{ top: 'var(--apicostx-page-sticky-top, var(--apicostx-header-offset, 173px))' }}
      >
        <div className="mx-auto max-w-7xl px-4 py-3">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <label htmlFor="preset-selector" className="text-sm text-gray-400 sm:shrink-0">Preset:</label>
              <select
                id="preset-selector"
                name="preset-selector"
                value={selectedPresetId}
                onChange={handlePresetChange}
                className="w-full min-w-0 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 sm:w-auto sm:min-w-[14rem]"
                data-field="preset-selector"
              >
                <option value="">-- New Preset --</option>
                {presets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </select>
              <input
                id="preset-name"
                name="preset-name"
                type="text"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="Preset Name"
                className="w-full min-w-0 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 sm:w-56"
                data-field="preset-name"
              />
              <Button
                variant="ghost"
                size="sm"
                icon={<Save className="w-4 h-4" />}
                onClick={handleSavePreset}
                className="w-full sm:w-auto"
                data-action="save-preset"
              >
                Save
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<RotateCcw className="w-4 h-4" />}
                onClick={() => {
                  config.resetToDefaults()
                  setKeyMode('system')
                  setExpandedPanels(createCollapsedPanels())
                }}
                className="w-full sm:w-auto"
                data-action="reset"
              >
                Reset
              </Button>
            </div>

            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
              <div className="w-full sm:w-auto">
                <RunEstimator documentCount={Math.max(1, selectedInputDocIds.length)} />
              </div>
              <Button
                variant="primary"
                icon={<Play className="w-4 h-4" />}
                onClick={handleStartRun}
                disabled={isSubmitting}
                className="w-full bg-green-600 px-6 text-white hover:bg-green-700 sm:w-auto"
              >
                {isSubmitting ? 'Starting...' : 'Execute Preset'}
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-[1600px] mx-auto px-4 py-6 pb-24">
            {modelSectionVariant === 'selectors' ? (
              <ModelSelectorColumns
                layout={selectorLayout}
                leadingSection={inputSourceTopPanel}
              />
            ) : modelSectionVariant === 'unified' ? (
              <UnifiedPresetModelSection
                leadingSection={inputSourceTopPanel}
                onLeadingSectionVisibilityChange={setShowUnifiedTopInputSource}
              />
            ) : (
            <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden mb-6" data-testid="presets-model-matrix">
              <div className="p-4 border-b border-gray-700">
                <h3 className="font-medium">Model × Section Cards</h3>
                <p className="text-sm text-gray-400">
                  Each card is one model in one section. Use the section labels to include or exclude it.
                </p>
              </div>
              <div className="p-4">
                {isModelsLoading && matrixModels.length === 0 ? (
                  <p className="text-sm text-gray-400">Loading models...</p>
                ) : matrixModels.length === 0 ? (
                  <p className="text-sm text-gray-500">No models available.</p>
                ) : (() => {
                  type ComboSection = 'fpf' | 'gptr' | 'dr' | 'eval' | 'combine'
                  const SECTION_META: Record<ComboSection, { label: string; pillClass: string; cardClass: string }> = {
                    fpf:     { label: 'FPF',     pillClass: 'bg-blue-700 text-blue-100',     cardClass: 'border-blue-800 bg-blue-900/20' },
                    gptr:    { label: 'GPT-R',   pillClass: 'bg-purple-700 text-purple-100', cardClass: 'border-purple-800 bg-purple-900/20' },
                    dr:      { label: 'DR',      pillClass: 'bg-amber-700 text-amber-100',   cardClass: 'border-amber-800 bg-amber-900/20' },
                    eval:    { label: 'Eval',    pillClass: 'bg-green-700 text-green-100',   cardClass: 'border-green-800 bg-green-900/20' },
                    combine: { label: 'Combine', pillClass: 'bg-gray-500 text-gray-100',     cardClass: 'border-gray-600 bg-gray-800/60' },
                  }

                  const renderComboCard = (model: string, section: ComboSection, isFree = false) => {
                    const meta = SECTION_META[section]
                    const isChecked = section === 'fpf'  ? config.fpf.selectedModels.includes(model)
                                    : section === 'gptr' ? config.gptr.selectedModels.includes(model)
                                    : config.dr.selectedModels.includes(model)

                    const sectionData = section === 'fpf'  ? getGenScore('fpf',  model)
                                       : section === 'gptr' ? getGenScore('gptr', model)
                                       : section === 'dr'   ? getGenScore('dr',   model)
                                       : undefined

                    const evalData = section === 'fpf' ? getJudgeQuality(model) : undefined

                    // Shared fixed-width classes so every pill/badge column aligns across all rows
                    const sectionPillCls = 'text-[10px] font-bold px-1 py-1 rounded uppercase tracking-wide text-center inline-flex items-center justify-center w-[3.25rem] shrink-0 border transition-colors'
                    const subPillCls   = 'text-[10px] font-bold px-1.5 py-1 rounded uppercase tracking-wide inline-flex items-center justify-center shrink-0 border transition-colors'
                    const scoreCls = 'text-[10px] font-mono px-1 py-0.5 rounded text-center inline-block w-12 shrink-0'
                    const emptyBadge = 'inline-block w-12 shrink-0'
                    const sectionButtonClass = isChecked
                      ? `${sectionPillCls} ${meta.pillClass} border-white/30 shadow-[0_0_0_1px_rgba(255,255,255,0.14)]`
                      : `${sectionPillCls} border-gray-700 bg-gray-900/70 text-gray-500 hover:border-gray-500 hover:text-gray-300`
                    const evalChecked = config.eval.judgeModels.includes(model)
                    const combineChecked = config.combine.selectedModels.includes(model)
                    const evalButtonClass = evalChecked
                      ? `${subPillCls} bg-green-700 text-green-100 border-green-500 shadow-[0_0_0_1px_rgba(34,197,94,0.18)]`
                      : `${subPillCls} border-gray-700 bg-gray-900/70 text-gray-500 hover:border-green-700/70 hover:text-green-200`
                    const combineButtonClass = combineChecked
                      ? `${subPillCls} bg-gray-500 text-gray-100 border-gray-300/40 shadow-[0_0_0_1px_rgba(255,255,255,0.10)]`
                      : `${subPillCls} border-gray-700 bg-gray-900/70 text-gray-500 hover:border-gray-500 hover:text-gray-300`

                    return (
                      <div
                        key={`${section}-${model}`}
                        className={`rounded-lg border px-3 py-1.5 flex items-center gap-2 ${meta.cardClass} ${isFree ? 'opacity-70' : ''}`}
                        data-model={model}
                        data-section={section}
                        data-checked={isChecked}
                      >
                        {/* LEFT: clickable section label + score */}
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            type="button"
                            aria-pressed={isChecked}
                            onClick={() => handleMatrixToggle(section, model, !isChecked)}
                            className={sectionButtonClass}
                            data-testid={`matrix-${section}-${model}`}
                          >
                            {meta.label}
                          </button>
                          {sectionData?.score != null
                            ? <span className={`${scoreCls} ${tierClass(sectionData.score, badgeQuantiles.genScore, true)}`}>{sectionData.score.toFixed(2)}</span>
                            : <span className={emptyBadge} />}
                        </div>

                        {/* MIDDLE: model name */}
                        <span className="flex flex-1 justify-center px-1">
                          <span
                            className="line-clamp-2 max-w-[18rem] text-center text-xs font-mono leading-4 text-gray-300 break-words"
                            title={model}
                          >
                            {model}
                          </span>
                        </span>

                        {/* RIGHT: eval + combine (FPF cards only) */}
                        {section === 'fpf' && (
                          <div className="flex items-center gap-2 shrink-0">
                            {evalEligible.has(model) && (
                              <div className="flex items-center gap-1">
                                <button
                                  type="button"
                                  aria-pressed={evalChecked}
                                  onClick={() => handleMatrixToggle('eval', model, !evalChecked)}
                                  className={evalButtonClass}
                                  data-testid={`matrix-eval-${model}`}
                                >
                                  Eval
                                </button>
                                {evalData?.sortino != null
                                  ? <span className={`${scoreCls} ${tierClass(evalData.sortino * 100, badgeQuantiles.evalScore, true)}`}>★{(evalData.sortino * 100).toFixed(0)}%</span>
                                  : <span className={emptyBadge} />}
                              </div>
                            )}
                            {combineEligible.has(model) && (
                              <div className="flex items-center gap-1">
                                <button
                                  type="button"
                                  aria-pressed={combineChecked}
                                  onClick={() => handleMatrixToggle('combine', model, !combineChecked)}
                                  className={combineButtonClass}
                                  data-testid={`matrix-combine-${model}`}
                                >
                                  Comb.
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  }

                  const buildCombos = (models: string[]) =>
                    models.flatMap(model => {
                      const sections: ComboSection[] = []
                      if (fpfEligible.has(model))  sections.push('fpf')
                      if (gptrEligible.has(model)) sections.push('gptr')
                      if (drEligible.has(model))   sections.push('dr')
                      return sections.map(s => ({ model, section: s }))
                    })

                  const handleColSort = (col: string) => {
                    if (cardSortCol === col) setCardSortDir(d => d === 'asc' ? 'desc' : 'asc')
                    else { setCardSortCol(col); setCardSortDir('asc') }
                  }
                  const sortCombos = (combos: { model: string; section: ComboSection }[]) => {
                    if (!cardSortCol) return combos
                    const sortOptionalComboMetric = (
                      entries: { model: string; section: ComboSection }[],
                      getMetric: (entry: { model: string; section: ComboSection }) => number | string | null | undefined
                    ) => {
                      const ranked: Array<{ entry: { model: string; section: ComboSection }; metric: number | string }> = []
                      const missing: Array<{ model: string; section: ComboSection }> = []

                      for (const entry of entries) {
                        const metric = getMetric(entry)
                        if (typeof metric === 'number') {
                          if (Number.isFinite(metric)) ranked.push({ entry, metric })
                          else missing.push(entry)
                          continue
                        }
                        if (typeof metric === 'string') {
                          ranked.push({ entry, metric })
                          continue
                        }
                        missing.push(entry)
                      }

                      ranked.sort((a, b) => {
                        const cmp =
                          typeof a.metric === 'string' && typeof b.metric === 'string'
                            ? a.metric.localeCompare(b.metric)
                            : (a.metric as number) - (b.metric as number)
                        if (cmp !== 0) return cardSortDir === 'asc' ? cmp : -cmp
                        return a.entry.model.localeCompare(b.entry.model)
                      })
                      missing.sort((a, b) => a.model.localeCompare(b.model))
                      return [...ranked.map((item) => item.entry), ...missing]
                    }
                    if (cardSortCol === 'score') return sortOptionalComboMetric(combos, (entry) => getGenScore(entry.section as GenScoreType, entry.model)?.score)
                    if (cardSortCol === 'evalScore') return sortOptionalComboMetric(combos, (entry) => getJudgeQuality(entry.model)?.sortino)
                    return sortOptionalComboMetric(combos, (entry) => entry.model)
                  }

                  const isCardVisible = (model: string): boolean => {
                    const isFree = isFreeModel(model)
                    if (isFree) return showFree
                    const cardProvider = extractProviderFilterKey(model)
                    if (filterProviders !== null && !filterProviders.has(cardProvider)) return false
                    const isScored = getGenScore('fpf', model)?.score != null
                    if (isScored && !showScored) return false
                    if (!isScored && !showUnscored) return false
                    if (hideKeyless && !isFree) {
                      const keyProvider = extractKeyProvider(model)
                      if (!configuredKeyProviders.has(keyProvider)) return false
                    }
                    return true
                  }

                  const allCombos = sortCombos(buildCombos(matrixModels))
                  const visibleCombos = allCombos.filter(({ model }) => isCardVisible(model))

                  const isFiltered = !showScored || !showUnscored || !showFree || hideKeyless ||
                    (filterProviders !== null && filterProviders.size < distinctProviders.length)

                  const clearFilters = () => {
                    if (distinctProviders.length > 0) setFilterProviders(new Set(distinctProviders))
                    setShowScored(true)
                    setShowUnscored(true)
                    setShowFree(true)
                    setHideKeyless(false)
                  }

                  const hdrBtn = (col: string, label: string, cls: string) => (
                    <button
                      onClick={() => handleColSort(col)}
                      className={`${cls} text-center hover:text-gray-300 transition-colors cursor-pointer ${cardSortCol === col ? 'text-blue-400' : ''}`}
                    >
                      {label}{cardSortCol === col ? (cardSortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                    </button>
                  )

                  const pillOn = 'font-semibold transition-colors border'
                  const pillOff = 'font-semibold transition-colors border bg-transparent border-gray-700 text-gray-500'

                  return (
                    <>
                      {/* Filter Bar */}
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pb-3 mb-3 border-b border-gray-700 text-xs">
                        {/* Provider */}
                        {filterProviders !== null && distinctProviders.length > 0 && (
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Provider</span>
                            {distinctProviders.map(p => {
                              const on = filterProviders.has(p)
                              return (
                                <button
                                  key={p}
                                  onClick={() => setFilterProviders(prev => { const n = new Set(prev ?? []); on ? n.delete(p) : n.add(p); return n })}
                                  className={`px-2 py-0.5 rounded-full ${on ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}
                                >
                                  {PROVIDER_FILTER_LABELS[p]}
                                </button>
                              )
                            })}
                          </div>
                        )}

                        {/* Status / misc */}
                        <div className="flex items-center gap-1.5">
                          <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Show</span>
                          <button onClick={() => setShowScored(v => !v)} className={`px-2 py-0.5 rounded-full ${showScored ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}>Scored</button>
                          <button onClick={() => setShowUnscored(v => !v)} className={`px-2 py-0.5 rounded-full ${showUnscored ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}>Unscored</button>
                          <button onClick={() => setShowFree(v => !v)} className={`px-2 py-0.5 rounded-full ${showFree ? `${pillOn} bg-green-700 text-green-100 border-green-600` : pillOff}`}>Free</button>
                        </div>

                        <div className="flex items-center gap-1.5">
                          <button onClick={() => setHideKeyless(v => !v)} className={`px-2 py-0.5 rounded-full ${hideKeyless ? `${pillOn} bg-yellow-700 text-yellow-100 border-yellow-600` : pillOff}`}>BYOK only</button>
                        </div>

                        {isFiltered && (
                          <button onClick={clearFilters} className="text-[10px] text-gray-500 hover:text-gray-300 underline ml-auto">
                            Clear filters
                          </button>
                        )}
                      </div>

                      {/* Column header row — widths match card layout exactly */}
                      <div className="flex items-center gap-2 px-3 py-1 mb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-500 border-b border-gray-700">
                        {/* Left side: selector label + score */}
                        <div className="flex items-center gap-1 shrink-0">
                          {hdrBtn('report', 'Report', 'w-[3.25rem] shrink-0')}
                          {hdrBtn('score', 'Score', 'w-12 shrink-0')}
                        </div>
                        {/* Middle: model name */}
                        {hdrBtn('model', 'Model Name', 'flex-1')}
                        {/* Right side: eval group + combine group */}
                        <div className="flex items-center gap-2 shrink-0">
                          <div className="flex items-center gap-1">
                            <span className="shrink-0 px-1.5">Eval</span>
                            {hdrBtn('evalScore', 'Score', 'w-12 shrink-0')}
                          </div>
                          <div className="flex items-center gap-1">
                            <span className="shrink-0 px-1.5">Combine</span>
                          </div>
                        </div>
                      </div>

                      {visibleCombos.length === 0 ? (
                        <p className="text-sm text-gray-500 py-6 text-center">No cards match the current filters.</p>
                      ) : (
                        <div className="grid grid-cols-1 gap-1.5">
                          {visibleCombos.map(({ model, section }) => renderComboCard(model, section, isFreeModel(model)))}
                        </div>
                      )}

                      {visibleCombos.length !== allCombos.length && (
                        <p className="text-[10px] text-gray-600 text-right mt-2">
                          Showing {visibleCombos.length} of {allCombos.length} cards
                        </p>
                      )}
                    </>
                  )
                })()}
              </div>
            </div>
            )}

          {/* 4-column layout: Setup / Generate / Evaluate / Combine */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
            {/* Column 1: Setup */}
            <div className="space-y-6">
              <GeneralPanel />
              <ConcurrencyPanel />

              <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                <div className="p-4 border-b border-gray-700 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Library className="w-5 h-5 text-purple-400" />
                    <div>
                      <h3 className="font-medium">Generation Instructions</h3>
                      <p className="text-sm text-gray-400">Prepended before input document in prompt</p>
                    </div>
                  </div>
                  <Link
                    to="/content"
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
                  >
                    <ExternalLink className="w-3 h-3" />
                    Library</Link>
                </div>

                <div className="p-4">
                  {instructionContents.length === 0 ? (
                    <p className="text-xs text-gray-500">No generation instructions in library</p>
                  ) : (
                    <select
                      value={selectedInstructionId || ''}
                      onChange={(e) => handleSelectInstruction(e.target.value || null)}
                      className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-purple-500"
                      data-testid="generation-instructions-select"
                    >
                      <option value="">-- No Instructions --</option>
                      {instructionContents.map((content) => (
                        <option key={content.id} value={content.id}>{content.name}</option>
                      ))}
                    </select>
                  )}
                </div>
              </div>

              {inputSourcePlacement !== 'top-panel' && !(modelSectionVariant === 'unified' && showUnifiedTopInputSource) && (
                <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                  <div className="p-4 border-b border-gray-700">
                    <div className="flex items-center gap-3">
                      <Folder className="w-5 h-5 text-green-400" />
                      <div>
                        <h3 className="font-medium">Input Source</h3>
                        <p className="text-sm text-gray-400">Where to load documents from</p>
                      </div>
                    </div>
                  </div>

                  <div className="p-4">
                    {inputSourceBody}
                  </div>
                </div>
              )}
            </div>

            {/* Column 2: Generate */}
            <div className="space-y-6">
              <FpfParamsPanel
                defaultExpanded={false}
                expanded={expandedPanels.fpf}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, fpf: expanded }))}
              />
              <GptrParamsPanel
                defaultExpanded={false}
                expanded={expandedPanels.gptr}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, gptr: expanded }))}
              />
              <DeepResearchPanel
                defaultExpanded={false}
                expanded={expandedPanels.dr}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, dr: expanded }))}
              />
              <AiqPanel
                defaultExpanded={false}
                expanded={expandedPanels.aiq}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, aiq: expanded }))}
              />
              <MultiAgentPanel
                defaultExpanded={false}
                expanded={expandedPanels.ma}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, ma: expanded }))}
              />
            </div>

            {/* Column 3: Evaluate */}
            <div className="space-y-6">
              <EvalPanel
                defaultExpanded={false}
                expanded={expandedPanels.eval}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, eval: expanded }))}
              />
            </div>

            {/* Column 4: Combine */}
            <div className="space-y-6">
              <CombinePanel
                defaultExpanded={false}
                expanded={expandedPanels.combine}
                onExpandedChange={(expanded) => setExpandedPanels((prev) => ({ ...prev, combine: expanded }))}
              />
            </div>
          </div>
      </div>

      {/* GitHub File Browser Modal */}
      {showGitHubFileBrowser && githubConnectionId && (
        <GitHubFileBrowserModal
          connectionId={githubConnectionId}
          purpose={githubBrowsePurpose}
          onSelect={(path) => {
            if (githubBrowsePurpose === 'input') {
              setGithubInputPaths(prev => prev.includes(path) ? prev : [...prev, path]);
            } else {
              setGithubOutputPath(path);
            }
          }}
          onClose={() => setShowGitHubFileBrowser(false)}
        />
      )}
    </div>
  )
}

// GitHub File Browser Modal Component
function GitHubFileBrowserModal({
  connectionId,
  purpose,
  onSelect,
  onClose,
}: {
  connectionId: string;
  purpose: 'input' | 'output';
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [currentPath, setCurrentPath] = useState('');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const { data: browseData, isLoading } = useQuery({
    queryKey: ['github-browse', connectionId, currentPath],
    queryFn: () => githubApi.browse(connectionId, currentPath || undefined),
  });

  const handleNavigate = (path: string) => {
    setCurrentPath(path);
    setSelectedPath(null);
  };

  const handleSelect = (path: string, isDirectory: boolean) => {
    if (purpose === 'output' && !isDirectory) {
      // For output, only allow directories
      return;
    }
    setSelectedPath(path);
  };

  const handleConfirm = () => {
    if (selectedPath) {
      onSelect(selectedPath);
      onClose();
    }
  };

  const pathParts = currentPath.split('/').filter(Boolean);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 border border-gray-700 rounded-lg w-full max-w-2xl max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Github className="w-5 h-5 text-white" />
            <h2 className="font-semibold">
              {purpose === 'input' ? 'Select Input Files/Folders' : 'Select Output Folder'}
            </h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Breadcrumb Navigation */}
        <div className="px-4 py-2 border-b border-gray-700 flex items-center gap-1 text-sm overflow-x-auto">
          <button
            onClick={() => handleNavigate('')}
            className="text-blue-400 hover:text-blue-300 flex items-center gap-1"
          >
            <Folder className="w-4 h-4" />
            root
          </button>
          {pathParts.map((part, i) => (
            <span key={i} className="flex items-center gap-1">
              <ChevronRight className="w-4 h-4 text-gray-500" />
              <button
                onClick={() => handleNavigate(pathParts.slice(0, i + 1).join('/'))}
                className="text-blue-400 hover:text-blue-300"
              >
                {part}
              </button>
            </span>
          ))}
        </div>

        {/* File List */}
        <div className="flex-1 overflow-y-auto p-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : browseData?.contents.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <Folder className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>Empty directory</p>
            </div>
          ) : (
            <div className="space-y-1">
              {browseData?.contents.map((item) => {
                const isDir = item.type === 'dir';
                const isSelectable = purpose === 'input' || isDir;
                return (
                  <div
                    key={item.path}
                    className={cn(
                      'flex items-center gap-3 p-2 rounded cursor-pointer transition-colors',
                      selectedPath === item.path
                        ? 'bg-blue-500/20 border border-blue-500'
                        : isSelectable
                        ? 'hover:bg-gray-700'
                        : 'opacity-50 cursor-not-allowed'
                    )}
                    onClick={() => {
                      if (isDir) {
                        // Double-click to navigate (single click to select)
                        if (selectedPath === item.path) {
                          handleNavigate(item.path);
                        } else {
                          handleSelect(item.path, true);
                        }
                      } else if (isSelectable) {
                        handleSelect(item.path, false);
                      }
                    }}
                  >
                    {isDir ? (
                      <Folder className="w-5 h-5 text-yellow-400" />
                    ) : (
                      <FileText className="w-5 h-5 text-gray-400" />
                    )}
                    <span className="flex-1">{item.name}</span>
                    {isDir && (
                      <ChevronRight className="w-4 h-4 text-gray-500" />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-700 flex items-center justify-between">
          <div className="text-sm text-gray-400">
            {selectedPath ? (
              <span>Selected: <code className="bg-gray-700 px-1 rounded">{selectedPath}</code></span>
            ) : (
              <span>{purpose === 'output' ? 'Click folder to select, double-click to enter' : 'Click to select, double-click folder to enter'}</span>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            {purpose === 'input' && (
              <Button
                variant="primary"
                onClick={() => {
                  if (browseData?.contents) {
                    browseData.contents.forEach(item => onSelect(item.path))
                  }
                  onClose()
                }}
              >
                Select All
              </Button>
            )}
            {purpose === 'output' && (
              <Button
                variant="secondary"
                onClick={async () => {
                  const folderName = prompt('Enter new folder name:')
                  if (folderName) {
                    try {
                      const fullPath = currentPath ? currentPath + '/' + folderName : folderName
                      await githubApi.createFolder(connectionId, { path: fullPath })
                      // Refresh browse data by navigating to current path
                      setCurrentPath(prev => prev)
                    } catch (err) {
                      console.error('Create folder failed:', err)
                    }
                  }
                }}
              >
                New Folder
              </Button>
            )}
            <Button
              variant="primary"
              onClick={handleConfirm}
              disabled={!selectedPath}
            >
              Select
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
