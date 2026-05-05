// API module exports
export { apiClient, ApiError } from './client'
export { runsApi, type Run, type RunConfig, type CreateRunRequest, type SourceDocResult, type SourceDocStatus, type GeneratedDocInfo, type DocumentEvalDetail, type PairwiseRanking } from './runs'
export { documentsApi, type Document, type AddDocumentRequest } from './documents'
export {
  executionApi,
  type GenerationTask,
  type GenerationResult,
  type StartGenerationRequest,
  type AdapterInfo
} from './execution'
export {
  createPreset,
  listPresets,
  getPreset,
  updatePreset,
  deletePreset,
  duplicatePreset,
  executePreset,
  type PresetCreate,
  type PresetUpdate,
  type PresetResponse,
  type PresetSummary,
  type PresetList,
  type ModelConfig,
  type GptrSettings,
  type EvaluationSettings,
  type PairwiseSettings,
} from './presets'

export {
  evaluationApi,
  type EvaluationCriteria,
  type SingleDocEvaluationRequest,
  type SingleDocEvaluationResult,
  type PairwiseEvaluationRequest,
  type PairwiseEvaluationResult,
  type FullEvaluationRequest,
  type EvaluationSummary,
  type EvaluationJob
} from './evaluation'

export { modelsApi, type ModelConfigResponse } from './models'

export {
  contentsApi,
  contentTypeLabels,
  contentTypeIcons,
  type ContentType,
  type ContentSummary,
  type ContentDetail,
  type ContentList,
  type ContentCreate,
  type ContentUpdate,
  type ContentResolved,
  type ContentTypeCounts,
} from './contents'

export {
  githubApi,
  type GitHubConnectionSummary,
  type GitHubConnectionDetail,
  type GitHubConnectionList,
  type GitHubConnectionCreate,
  type GitHubConnectionUpdate,
  type GitHubConnectionTestResult,
  type GitHubFileInfo,
  type GitHubBrowseResponse,
  type GitHubFileContent,
  type GitHubImportRequest,
} from './github'
