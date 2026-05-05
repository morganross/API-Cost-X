import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Save, RotateCcw, Sliders, Play, FileText, Library, ExternalLink, Github, Folder, ChevronRight, RefreshCw, X, ArrowDownAZ, Star } from 'lucide-react'
import { Button } from '../components/ui/button'
import { useConfigStore } from '../stores/config'
import { useModelCatalog } from '../stores/modelCatalog'
import { notify } from '@/stores/notifications'
import { runsApi } from '@/api/runs'
import { contentsApi, type ContentSummary } from '@/api/contents'
import { githubApi } from '@/api/github'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
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
  type EvalConfigComplete as EvalConfig,
  type ConcurrencyConfigComplete as ConcurrencyConfig,
  type CombineConfigComplete as CombineConfig
} from '@/api/presets'
import {
  GeneralPanel,
  FpfParamsPanel,
  GptrParamsPanel,
  DeepResearchPanel,
  MultiAgentPanel,
  EvalPanel,
  CombinePanel,
  ConcurrencyPanel,
  RunEstimator,
} from '../components/config'

// Input source type
type InputSourceType = 'database' | 'github'

// Helper to format model to "provider:model" string for UI store
function formatModelString(provider: string, model: string): string {
  return `${provider}:${model}`;
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
  const maEnabled = config.ma.enabled
  const evalEnabled = config.eval.judgeModels.length > 0
  const combineEnabled = config.combine.selectedModels.length > 0
  const generationModelCount =
    config.fpf.selectedModels.length +
    config.gptr.selectedModels.length +
    config.dr.selectedModels.length +
    (maEnabled ? config.ma.selectedModels.length : 0)
  const pairwiseEnabled =
    config.eval.enablePairwise &&
    evalEnabled &&
    generationModelCount >= 2

  return {
    fpfEnabled,
    gptrEnabled,
    drEnabled,
    maEnabled,
    evalEnabled,
    combineEnabled,
    pairwiseEnabled,
    generators: [
      fpfEnabled ? 'fpf' : null,
      gptrEnabled ? 'gptr' : null,
      drEnabled ? 'dr' : null,
      maEnabled ? 'ma' : null,
    ].filter((generator): generator is string => generator !== null),
  }
}

function serializeConfigToPreset(
  config: ConfigStore,
  presetName: string,
  selectedInputDocIds: string[],
  selectedInstructionId: string | null,
  githubConfig: GitHubInputConfig
): PresetCreate {
  const derived = getDerivedActivation(config)
  const openrouterWebSearchConfig = buildOpenRouterWebSearchConfig(config)

  const general_config: GeneralConfig = {
    iterations: config.general.iterations,
    use_byok_first: false,
    save_run_logs: config.general.saveRunLogs,
    post_combine_top_n: config.combine.postCombineTopN,
    expose_criteria_to_generators: config.general.exposeCriteriaToGenerators,
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
    // Legacy field path
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
    // Legacy models array path
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
    // Legacy gptr_settings path
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
    // Legacy evaluation path
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
  };
}

// Model Sort Toggle Component
function ModelSortToggle() {
  const { sortBy, sortDir, setSortBy } = useModelCatalog();

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-gray-400">Sort:</span>
      <div className="flex rounded overflow-hidden border border-gray-600 divide-x divide-gray-600">
        <button
          onClick={() => setSortBy('name')}
          className={`px-2 py-1 text-xs flex items-center gap-1 transition-colors ${
            sortBy === 'name'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
          }`}
          title="Sort alphabetically"
        >
          <ArrowDownAZ className="w-3 h-3" />
          A-Z{sortBy === 'name' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
        </button>
        <button
          onClick={() => setSortBy('genScore')}
          className={`px-2 py-1 text-xs flex items-center gap-1 transition-colors ${
            sortBy === 'genScore'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
          }`}
          title="Sort by benchmark score for each section (higher first)"
        >
          <Star className="w-3 h-3" />
          Score{sortBy === 'genScore' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
        </button>
      </div>
    </div>
  );
}

export default function Configure() {
  const navigate = useNavigate()
  const config = useConfigStore()

  // UI State
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [selectedPresetId, setSelectedPresetId] = useState<string>('')
  const [presetName, setPresetName] = useState('')

  // Run State
  const [runName, setRunName] = useState('New Run')
  const [runDescription, setRunDescription] = useState('')
  const [fpfInstructions, setFpfInstructions] = useState<string>('')  // Instructions come from preset
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

    try {
      // Use the new complete serialization function
      const githubConfig: GitHubInputConfig = {
        inputSourceType,
        githubConnectionId,
        githubInputPaths,
        githubOutputPath,
      };
      const presetData = serializeConfigToPreset(config, presetName, selectedInputDocIds, selectedInstructionId, githubConfig);

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
                model_count: created.fpf_config?.selected_models?.length ?? 0,
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
      } = deserializePresetToConfig(preset, config);
      setFpfInstructions(loadedFpfInstructions);
      setSelectedInstructionId(generationInstructionsId);

      // Set GitHub input source state
      setInputSourceType(loadedInputSource);
      setGithubConnectionId(loadedGithubConnId);
      setGithubInputPaths(loadedGithubInputPaths);
      setGithubOutputPath(loadedGithubOutputPath);

    } catch (err) {
      console.error('Failed to load preset:', err)
      notify.error('Failed to load preset details')
    }
  }

  const handleStartRun = async () => {
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
      notify.error('Failed to start run. Check console for details.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100 flex flex-col">
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
        data-eval-enabled={config.eval.judgeModels.length > 0}
        data-eval-models={JSON.stringify(config.eval.judgeModels)}
        data-combine-enabled={config.combine.selectedModels.length > 0}
        data-combine-models={JSON.stringify(config.combine.selectedModels)}
        data-iterations={config.general.iterations}
        data-is-submitting={isSubmitting}
        className="hidden"
        aria-hidden="true"
      />
      {/* Sticky header: title bar + preset/sort bar combined */}
      <div className="sticky top-0 z-10 backdrop-blur">
      {/* Header */}
      <div className="border-b border-gray-700 bg-gray-800/50">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <Sliders className="w-8 h-8 text-blue-400" />
              <div>
                <h1 className="text-2xl font-bold">Build Preset</h1>
                <p className="text-sm text-gray-400">
                  Configure parameters and save as a preset
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <RunEstimator documentCount={Math.max(1, selectedInputDocIds.length)} />
              <Button
                variant="primary"
                icon={<Play className="w-4 h-4" />}
                onClick={handleStartRun}
                disabled={isSubmitting}
                className="bg-green-600 hover:bg-green-700 text-white px-6"
              >
                {isSubmitting ? 'Starting...' : 'Execute Preset'}
              </Button>
            </div>
          </div>

          {/* Run Details Inputs */}
          <div className="grid grid-cols-2 gap-4 mb-2">
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
      </div>

      {/* Preset Selector */}
      <div className="border-b border-gray-700 bg-gray-800/30">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex items-center justify-between py-3">
            {/* Preset Selector */}
            <div className="flex items-center gap-3">
              <label htmlFor="preset-selector" className="text-sm text-gray-400">Preset:</label>
              <select
                id="preset-selector"
                name="preset-selector"
                value={selectedPresetId}
                onChange={handlePresetChange}
                className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
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
                className="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500 w-48"
                data-field="preset-name"
              />
              <Button
                variant="ghost"
                size="sm"
                icon={<Save className="w-4 h-4" />}
                onClick={handleSavePreset}
                data-action="save-preset"
              >
                Save
              </Button>
              <Button variant="ghost" size="sm" icon={<RotateCcw className="w-4 h-4" />} onClick={config.resetToDefaults} data-action="reset">
                Reset
              </Button>
            </div>

            {/* Sort Toggle */}
            <ModelSortToggle />
          </div>
        </div>
      </div>
      </div>{/* end sticky wrapper */}

      {/* Content */}
      <div className="flex-1 overflow-auto">
        <div className="max-w-[1600px] mx-auto px-4 py-6 pb-24">

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

              {/* Input Source Section */}
              <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                <div className="p-4 border-b border-gray-700">
                  <div className="flex items-center gap-3">
                    <Folder className="w-5 h-5 text-green-400" />
                    <div>
                      <h3 className="font-medium">Input Source</h3>
                      <p className="text-sm text-gray-400">Where to load documents from</p>
                    </div>
                  </div>

                  {/* Source Type Toggle */}
                  <div className="flex gap-2 mt-3">
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
                </div>

                <div className="p-4">
                  {inputSourceType === 'database' ? (
                    // Content Library Selection
                    <>
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-sm text-gray-400">Select documents to process</span>
                        <Link
                    to="/content"
                          className="inline-flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
                        >
                          <ExternalLink className="w-4 h-4" />
                          Add Documents</Link>
                      </div>
                      {inputDocuments.length === 0 ? (
                        <div className="text-center py-6 text-gray-400">
                          <FileText className="w-8 h-8 mx-auto mb-2 opacity-50" />
                          <p className="text-sm">No input documents in library</p>
                          <Link to="/content" className="text-blue-400 hover:text-blue-300 text-sm mt-1 inline-block">
                            Create one in Content Library →</Link>
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
                    // GitHub Configuration
                    <div className="space-y-4">
                      {/* Connection Selector */}
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

                      {/* Input Paths */}
                      {githubConnectionId && (
                        <>
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <label className="text-sm text-gray-400">Input Files/Folders</label>
                              <button
                                onClick={() => {
                                  setGithubBrowsePurpose('input');
                                  setShowGitHubFileBrowser(true);
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

                          {/* Output Path */}
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <label className="text-sm text-gray-400">Output Folder (optional)</label>
                              <button
                                onClick={() => {
                                  setGithubBrowsePurpose('output');
                                  setShowGitHubFileBrowser(true);
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
              </div>
            </div>

            {/* Column 2: Generate */}
            <div className="space-y-6">
              <FpfParamsPanel />
              <GptrParamsPanel />
              <DeepResearchPanel />
              <MultiAgentPanel />
            </div>

            {/* Column 3: Evaluate */}
            <div className="space-y-6">
              <EvalPanel />
            </div>

            {/* Column 4: Combine */}
            <div className="space-y-6">
              <CombinePanel />
            </div>
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
