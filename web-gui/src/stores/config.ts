// config.ts - RunConfig store (all settings)
// Default configuration for the run-config store

import { create } from 'zustand'

// ============================================================================
// FPF (FilePromptForge) Configuration
// ============================================================================
type OpenRouterSearchContextSize = 'low' | 'medium' | 'high'

interface FpfConfig {
  enabled: boolean
  selectedModels: string[]
  maxTokens: number
  thinkingBudget: number
  temperature: number
  topP: number
  topK: number
  frequencyPenalty: number
  presencePenalty: number
  streamResponse: boolean
  includeMetadata: boolean
  savePromptHistory: boolean
  openrouterSearchContextSize: OpenRouterSearchContextSize
  openrouterSearchMaxResults: number
  openrouterSearchMaxTotalResults: number
}

// ============================================================================
// GPTR (GPT-Researcher) Configuration
// ============================================================================
interface GptrConfig {
  enabled: boolean
  selectedModels: string[]
  fastLlmTokenLimit: number
  smartLlmTokenLimit: number
  strategicLlmTokenLimit: number
  browseChunkMaxLength: number
  summaryTokenLimit: number
  temperature: number
  maxSearchResultsPerQuery: number
  totalWords: number
  maxIterations: number
  maxSubtopics: number
  reportType: string
  reportSource: string
  tone: string
  retriever: string
  scrapeUrls: boolean
  addSourceUrls: boolean
  verboseMode: boolean
  followLinks: boolean
  // Subprocess timeout and retry settings
  subprocessTimeoutMinutes: number
  subprocessRetries: number
}

// ============================================================================
// DR (Deep Research) Configuration
// ============================================================================
interface DrConfig {
  enabled: boolean
  selectedModels: string[]
  breadth: number
  depth: number
  maxResults: number
  concurrencyLimit: number
  temperature: number
  maxTokens: number
  timeout: number
  searchProvider: string
  enableCaching: boolean
  followLinks: boolean
  extractCode: boolean
  includeImages: boolean
  semanticSearch: boolean
  // Subprocess timeout and retry settings
  subprocessTimeoutMinutes: number
  subprocessRetries: number
}

// ============================================================================
// MA (Multi-Agent) Configuration
// ============================================================================
interface MaConfig {
  enabled: boolean
  selectedModels: string[]
  maxAgents: number
  communicationStyle: string
  enableConsensus: boolean
  enableDebate: boolean
  enableVoting: boolean
  maxRounds: number
}

// ============================================================================
// AI-Q Configuration
// ============================================================================
interface AiqConfig {
  enabled: boolean
  selectedModels: string[]
  smallModel: string
  profile: string
  agentType: string
  reportMinWords: number
  reportMaxWords: number
  intentClassifierLlm: string
  clarifierLlm: string
  clarifierPlannerLlm: string
  shallowResearchLlm: string
  orchestratorLlm: string
  researcherLlm: string
  plannerLlm: string
  summaryModelBinding: string
  intentModelName: string
  intentTemperature: number
  intentTopP: number
  intentMaxTokens: number
  intentRetries: number
  intentEnableThinking: boolean
  nanoModelName: string
  nanoTemperature: number
  nanoTopP: number
  nanoMaxTokens: number
  nanoRetries: number
  nanoEnableThinking: boolean
  gptOssModelName: string
  gptOssTemperature: number
  gptOssTopP: number
  gptOssMaxTokens: number
  gptOssMaxRetries: number
  openaiGpt52ModelName: string
  summaryModelName: string
  summaryTemperature: number
  summaryMaxTokens: number
  summaryRetries: number
  webSearchMaxResults: number
  webSearchMaxContentLength: number
  advancedWebSearchMaxResults: number
  advancedWebSearchAdvancedSearch: boolean
  clarifierMaxTurns: number
  clarifierEnablePlanApproval: boolean
  clarifierLogResponseMaxChars: number
  shallowResearchMaxLlmTurns: number
  shallowResearchMaxToolIterations: number
  deepResearchMaxLoops: number
  workflowEnableEscalation: boolean
  workflowEnableClarifier: boolean
  workflowUseAsyncDeepResearch: boolean
  knowledgeBackend: string
  knowledgeCollectionName: string
  knowledgeTopK: number
  knowledgeGenerateSummary: boolean
  knowledgeTimeoutSeconds: number
  dataSources: string[]
  webOnly: boolean
  preserveDebugArtifacts: boolean
  jobExpirySeconds: number | null
  timeoutSeconds: number | null
  advancedYamlOverrides: string
}

// ============================================================================
// Eval Configuration
// ============================================================================
interface EvalConfig {
  enabled: boolean
  iterations: number
  pairwiseTopN: number
  judgeModels: string[]
  timeoutSeconds: number  // Per-call timeout for judge LLM
  retries: number  // Retry count for transient failures
  temperature: number  // Temperature for judge LLM
  maxTokens: number  // Max output tokens for judge LLM responses
  thinkingBudget: number  // Thinking budget tokens for judge LLM
  enableFactualAccuracy: boolean
  enableCoherence: boolean
  enableRelevance: boolean
  enableCompleteness: boolean
  enableCitation: boolean
  enablePairwise: boolean
  // Content Library instruction IDs
  singleEvalInstructionsId: string | null
  pairwiseEvalInstructionsId: string | null
  evalCriteriaId: string | null
}

// ============================================================================
// Concurrency Configuration
// ============================================================================
interface ConcurrencyConfig {
  maxConcurrent: number
  evalConcurrency: number
  launchDelay: number
  requestTimeout: number | null
  // FPF API retry settings (for transient errors like 429, 500s)
  fpfMaxRetries: number
  fpfRetryDelay: number
}

// ============================================================================
// Combine Configuration
// ============================================================================
interface CombineConfig {
  enabled: boolean
  selectedModels: string[]
  maxTokens: number  // Max output tokens for combine LLM
  // Content Library instruction ID
  combineInstructionsId: string | null
  // Post-combine evaluation settings
  postCombineTopN: number | null
}

// ============================================================================
// General Run Configuration
// ============================================================================
interface GeneralConfig {
  iterations: number
  saveRunLogs: boolean
  exposeCriteriaToGenerators: boolean  // When true, eval criteria appended to generation prompts
  outputFilenameTemplate: string
}

// ============================================================================
// Full Config Store Interface
// ============================================================================
interface ConfigState {
  general: GeneralConfig
  fpf: FpfConfig
  gptr: GptrConfig
  dr: DrConfig
  ma: MaConfig
  aiq: AiqConfig
  eval: EvalConfig
  concurrency: ConcurrencyConfig
  combine: CombineConfig

  // Update methods
  updateGeneral: (updates: Partial<GeneralConfig>) => void
  updateFpf: (updates: Partial<FpfConfig>) => void
  updateGptr: (updates: Partial<GptrConfig>) => void
  updateDr: (updates: Partial<DrConfig>) => void
  updateMa: (updates: Partial<MaConfig>) => void
  updateAiq: (updates: Partial<AiqConfig>) => void
  updateEval: (updates: Partial<EvalConfig>) => void
  updateConcurrency: (updates: Partial<ConcurrencyConfig>) => void
  updateCombine: (updates: Partial<CombineConfig>) => void
  resetToDefaults: () => void
}

// ============================================================================
// Default values
// ============================================================================
const defaultGeneral: GeneralConfig = {
  iterations: 3,
  saveRunLogs: true,
  exposeCriteriaToGenerators: false,
  outputFilenameTemplate: '',
}

const defaultFpf: FpfConfig = {
  enabled: true,
  selectedModels: [],  // REQUIRED from preset - no hardcoded default
  maxTokens: 32000,
  thinkingBudget: 2048,
  temperature: 0.7,
  topP: 0.95,
  topK: 40,
  frequencyPenalty: 0.0,
  presencePenalty: 0.0,
  streamResponse: true,
  includeMetadata: true,
  savePromptHistory: true,
  openrouterSearchContextSize: 'low',
  openrouterSearchMaxResults: 3,
  openrouterSearchMaxTotalResults: 5,
}

const defaultGptr: GptrConfig = {
  enabled: true,
  selectedModels: [],  // REQUIRED from preset - no hardcoded default
  fastLlmTokenLimit: 4000,
  smartLlmTokenLimit: 8000,
  strategicLlmTokenLimit: 16000,
  browseChunkMaxLength: 8000,
  summaryTokenLimit: 2000,
  temperature: 0.4,
  maxSearchResultsPerQuery: 5,
  totalWords: 3000,
  maxIterations: 4,
  maxSubtopics: 5,
  reportType: 'research_report',
  reportSource: 'web',
  tone: 'Objective',
  retriever: 'tavily',
  scrapeUrls: true,
  addSourceUrls: true,
  verboseMode: false,
  followLinks: true,
  // Subprocess timeout and retry settings
  subprocessTimeoutMinutes: 20,  // 20 minutes default
  subprocessRetries: 1,  // 1 retry on timeout
}

const defaultDr: DrConfig = {
  enabled: true,
  selectedModels: [],  // REQUIRED from preset - no hardcoded default
  breadth: 4,
  depth: 3,
  maxResults: 10,
  concurrencyLimit: 5,
  temperature: 0.5,
  maxTokens: 16000,
  timeout: 600, // Increased from 300 to handle slow LLM evaluations
  searchProvider: 'tavily',
  enableCaching: true,
  followLinks: true,
  extractCode: true,
  includeImages: false,
  semanticSearch: true,
  // Subprocess timeout and retry settings
  subprocessTimeoutMinutes: 20,  // 20 minutes default
  subprocessRetries: 1,  // 1 retry on timeout
}

const defaultMa: MaConfig = {
  enabled: false,
  selectedModels: [],  // REQUIRED from preset - no hardcoded default
  maxAgents: 3,
  communicationStyle: 'sequential',
  enableConsensus: true,
  enableDebate: false,
  enableVoting: false,
  maxRounds: 3,
}

const defaultAiq: AiqConfig = {
  enabled: false,
  selectedModels: [],
  smallModel: '',
  profile: 'deep_web_default',
  agentType: 'deep_researcher',
  reportMinWords: 4000,
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
  deepResearchMaxLoops: 2,
  workflowEnableEscalation: true,
  workflowEnableClarifier: true,
  workflowUseAsyncDeepResearch: true,
  knowledgeBackend: 'llamaindex',
  knowledgeCollectionName: 'test_collection',
  knowledgeTopK: 5,
  knowledgeGenerateSummary: true,
  knowledgeTimeoutSeconds: 300,
  dataSources: ['web'],
  webOnly: true,
  preserveDebugArtifacts: true,
  jobExpirySeconds: 3600,
  timeoutSeconds: 1200,
  advancedYamlOverrides: '',
}

const defaultEval: EvalConfig = {
  enabled: true,
  enablePairwise: true,
  iterations: 3,
  pairwiseTopN: 5,
  judgeModels: [],  // REQUIRED from preset - no hardcoded default
  timeoutSeconds: 600,  // 10 min per-call timeout
  retries: 3,  // Retry count for transient failures
  temperature: 0.3,  // Temperature for judge LLM
  maxTokens: 16384,  // Max output tokens for judge LLM
  thinkingBudget: 2048,  // Thinking budget tokens for judge LLM
  enableFactualAccuracy: true,
  enableCoherence: true,
  enableRelevance: true,
  enableCompleteness: true,
  enableCitation: false,
  singleEvalInstructionsId: null,
  pairwiseEvalInstructionsId: null,
  evalCriteriaId: null,
}

const defaultConcurrency: ConcurrencyConfig = {
  maxConcurrent: 5,
  evalConcurrency: 5,
  launchDelay: 1.0,
  requestTimeout: null,
  fpfMaxRetries: 3,
  fpfRetryDelay: 1.0,
}

const defaultCombine: CombineConfig = {
  enabled: true,
  selectedModels: [],  // REQUIRED from preset - no hardcoded default
  maxTokens: 64000,  // Max output tokens for combine LLM
  combineInstructionsId: null,
  postCombineTopN: 5,
}

// ============================================================================
// Zustand Store
// ============================================================================
export const useConfigStore = create<ConfigState>((set) => ({
  general: { ...defaultGeneral },
  fpf: { ...defaultFpf },
  gptr: { ...defaultGptr },
  dr: { ...defaultDr },
  ma: { ...defaultMa },
  aiq: { ...defaultAiq },
  eval: { ...defaultEval },
  concurrency: { ...defaultConcurrency },
  combine: { ...defaultCombine },

  updateGeneral: (updates) =>
    set((state) => ({ general: { ...state.general, ...updates } })),

  updateFpf: (updates) =>
    set((state) => ({ fpf: { ...state.fpf, ...updates } })),

  updateGptr: (updates) =>
    set((state) => ({ gptr: { ...state.gptr, ...updates } })),

  updateDr: (updates) =>
    set((state) => ({ dr: { ...state.dr, ...updates } })),

  updateMa: (updates) =>
    set((state) => ({ ma: { ...state.ma, ...updates } })),

  updateAiq: (updates) =>
    set((state) => ({ aiq: { ...state.aiq, ...updates } })),

  updateEval: (updates) =>
    set((state) => ({ eval: { ...state.eval, ...updates } })),

  updateConcurrency: (updates) =>
    set((state) => ({ concurrency: { ...state.concurrency, ...updates } })),

  updateCombine: (updates) =>
    set((state) => ({ combine: { ...state.combine, ...updates } })),

  resetToDefaults: () =>
    set({
      general: { ...defaultGeneral },
      fpf: { ...defaultFpf },
      gptr: { ...defaultGptr },
      dr: { ...defaultDr },
      ma: { ...defaultMa },
      aiq: { ...defaultAiq },
      eval: { ...defaultEval },
      concurrency: { ...defaultConcurrency },
      combine: { ...defaultCombine },
    }),
}))
