/**
 * Presets API client
 */
import { apiClient } from './client'

// Types
export interface ModelConfig {
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

export interface GptrSettings {
  report_type: string;
  report_source: string;
  tone: string;
  retriever: string;
  max_search_results: number;
  total_words: number;
  fast_llm: string;
  smart_llm: string;
}

export interface EvaluationSettings {
  enabled: boolean;
  criteria: string[];
  eval_model: string;
}

export interface PairwiseSettings {
  enabled: boolean;
  judge_model: string;
}

export interface FpfSettings {
  prompt_template: string;
}

// ============================================================================
// Complete Config Interfaces (for full preset persistence)
// ============================================================================

export type OpenRouterSearchContextSize = 'low' | 'medium' | 'high';

export interface OpenRouterWebSearchConfig {
  search_context_size: OpenRouterSearchContextSize;
  max_results: number;
  max_total_results: number;
}

export interface FpfConfigComplete {
  enabled: boolean;
  selected_models: string[];
  max_tokens: number;
  temperature: number;
  top_p: number;
  top_k: number;
  frequency_penalty: number;
  presence_penalty: number;
  stream_response: boolean;
  include_metadata: boolean;
  save_prompt_history: boolean;
  web_search?: OpenRouterWebSearchConfig;
}

export interface GptrConfigComplete {
  enabled: boolean;
  selected_models: string[];
  fast_llm_token_limit: number;
  smart_llm_token_limit: number;
  strategic_llm_token_limit: number;
  browse_chunk_max_length: number;
  summary_token_limit: number;
  temperature: number;
  max_search_results_per_query: number;
  total_words: number;
  max_iterations: number;
  max_subtopics: number;
  report_type: string;
  report_source: string;
  tone: string;
  scrape_urls: boolean;
  add_source_urls: boolean;
  verbose_mode: boolean;
  follow_links: boolean;
  // Subprocess timeout and retry settings
  subprocess_timeout_minutes: number;
  subprocess_retries: number;
}

export interface DrConfigComplete {
  enabled: boolean;
  selected_models: string[];
  breadth: number;
  depth: number;
  max_results: number;
  concurrency_limit: number;
  temperature: number;
  max_tokens: number;
  timeout: number | null;
  search_provider: string;
  enable_caching: boolean;
  follow_links: boolean;
  extract_code: boolean;
  include_images: boolean;
  semantic_search: boolean;
  // Subprocess timeout and retry settings
  subprocess_timeout_minutes: number;
  subprocess_retries: number;
}

export interface MaConfigComplete {
  enabled: boolean;
  selected_models: string[];
  max_agents: number;
  communication_style: string;
  enable_consensus: boolean;
  enable_debate: boolean;
  enable_voting: boolean;
  max_rounds: number;
}

export interface AiqConfigComplete {
  enabled: boolean;
  selected_models: string[];
  small_model?: string | null;
  profile: string;
  agent_type: string;
  report_min_words: number;
  report_max_words: number;
  intent_classifier_llm: string;
  clarifier_llm: string;
  clarifier_planner_llm: string;
  shallow_research_llm: string;
  orchestrator_llm: string;
  researcher_llm: string;
  planner_llm: string;
  summary_model: string;
  data_sources: string[];
  web_only: boolean;
  preserve_debug_artifacts: boolean;
  job_expiry_seconds: number | null;
  timeout_seconds: number | null;
  config_overrides: Record<string, unknown>;
  advanced_yaml_overrides: Record<string, unknown>;
}

export interface EvalConfigComplete {
  enabled: boolean;
  auto_run?: boolean;
  iterations: number;
  pairwise_top_n: number;
  judge_models: string[];
  timeout_seconds: number | null;  // Per-call timeout for judge LLM
  retries: number;  // Retry count for transient failures
  temperature: number;  // Temperature for judge LLM
  max_tokens: number;  // Max output tokens for judge LLM responses
  thinking_budget_tokens?: number;  // Thinking budget tokens for judge LLM
  enable_semantic_similarity?: boolean;
  enable_factual_accuracy: boolean;
  enable_coherence: boolean;
  enable_relevance: boolean;
  enable_completeness: boolean;
  enable_citation: boolean;
}

export interface PairwiseConfigComplete {
  enabled: boolean;
  judge_models: string[];
}

export interface CombineConfigComplete {
  enabled: boolean;
  selected_models: string[];
  strategy: string;
  max_tokens: number;  // Max output tokens for combine LLM
}

export interface RunEstimateSnapshot {
  generation: number;
  single_eval: number;
  pre_combine_pairwise: number;
  combine: number;
  post_combine_pairwise: number;
  total: number;
  document_count: number;
  per_doc: {
    generation: number;
    single_eval: number;
    pre_combine_pairwise: number;
    combine: number;
    post_combine_pairwise: number;
    total: number;
  };
}

export interface GeneralConfigComplete {
  iterations: number;
  use_byok_first?: boolean;
  save_run_logs: boolean;
  post_combine_top_n: number | null;
  expose_criteria_to_generators: boolean;
  run_estimate?: RunEstimateSnapshot;
  enable_logging?: boolean;
  output_dir?: string;
  save_intermediate?: boolean;
}

export interface ConcurrencyConfigComplete {
  generation_concurrency?: number;
  eval_concurrency?: number;
  request_timeout?: number | null;
  fpf_max_retries?: number;
  fpf_retry_delay?: number;
  max_concurrent?: number;
  launch_delay?: number;
  enable_rate_limiting?: boolean;
}

// ============================================================================
// Request/Response Types
// ============================================================================

export interface PresetCreate {
  name: string;
  description?: string;
  documents: string[];

  // Complete config objects (NEW)
  general_config?: GeneralConfigComplete;
  fpf_config?: FpfConfigComplete;
  gptr_config?: GptrConfigComplete;
  dr_config?: DrConfigComplete;
  ma_config?: MaConfigComplete;
  aiq_config?: AiqConfigComplete;
  eval_config?: EvalConfigComplete;
  pairwise_config?: PairwiseConfigComplete;
  combine_config?: CombineConfigComplete;
  concurrency_config?: ConcurrencyConfigComplete;

  // Content Library instruction IDs
  single_eval_instructions_id?: string;
  pairwise_eval_instructions_id?: string;
  eval_criteria_id?: string;
  combine_instructions_id?: string;
  generation_instructions_id?: string;

  // GitHub input source configuration
  input_source_type?: 'database' | 'github';
  github_connection_id?: string;
  github_input_paths?: string[];
  github_output_path?: string;
  output_destination?: 'github' | 'library';

  output_filename_template?: string;
  prepend_source_first_line_frontmatter?: boolean; // opt-in; default false
  key_mode?: 'byok' | 'system';  // which API keys to use: byok=user keys, system=platform keys
  github_commit_message?: string;
}

export interface PresetUpdate {
  name?: string;
  description?: string;
  documents?: string[];

  // Complete config objects (NEW)
  general_config?: GeneralConfigComplete;
  fpf_config?: FpfConfigComplete;
  gptr_config?: GptrConfigComplete;
  dr_config?: DrConfigComplete;
  ma_config?: MaConfigComplete;
  aiq_config?: AiqConfigComplete;
  eval_config?: EvalConfigComplete;
  pairwise_config?: PairwiseConfigComplete;
  combine_config?: CombineConfigComplete;
  concurrency_config?: ConcurrencyConfigComplete;

  // Content Library instruction IDs
  single_eval_instructions_id?: string;
  pairwise_eval_instructions_id?: string;
  eval_criteria_id?: string;
  combine_instructions_id?: string;
  generation_instructions_id?: string;

  // GitHub input source configuration
  input_source_type?: 'database' | 'github';
  github_connection_id?: string;
  github_input_paths?: string[];
  github_output_path?: string;
  output_destination?: 'github' | 'library' | 'none';
  prepend_source_first_line_frontmatter?: boolean; // opt-in; default false
  key_mode?: 'byok' | 'system';  // which API keys to use: byok=user keys, system=platform keys
  github_commit_message?: string;
}

export interface PresetResponse {
  id: string;
  name: string;
  description?: string;
  documents: string[];

  // Complete config objects (NEW)
  general_config?: GeneralConfigComplete;
  fpf_config?: FpfConfigComplete;
  gptr_config?: GptrConfigComplete;
  dr_config?: DrConfigComplete;
  ma_config?: MaConfigComplete;
  aiq_config?: AiqConfigComplete;
  eval_config?: EvalConfigComplete;
  pairwise_config?: PairwiseConfigComplete;
  combine_config?: CombineConfigComplete;
  concurrency_config?: ConcurrencyConfigComplete;

  // Content Library instruction IDs
  single_eval_instructions_id?: string;
  pairwise_eval_instructions_id?: string;
  eval_criteria_id?: string;
  combine_instructions_id?: string;
  generation_instructions_id?: string;

  // GitHub input source configuration
  input_source_type?: 'database' | 'github';
  github_connection_id?: string;
  github_input_paths?: string[];
  github_output_path?: string;
  output_destination?: 'github' | 'library' | 'none';
  output_filename_template?: string;
  github_commit_message?: string;
  prepend_source_first_line_frontmatter?: boolean; // opt-in; default false
  key_mode?: 'byok' | 'system';  // which API keys to use: byok=user keys, system=platform keys

  // Legacy fallback fields retained only so older deserializers can read existing payloads safely
  generators?: string[];
  models?: ModelConfig[];
  iterations?: number;
  gptr_settings?: GptrSettings;
  fpf_settings?: FpfSettings;
  evaluation?: EvaluationSettings;
  pairwise?: PairwiseSettings;

  created_at: string;
  updated_at?: string;
  run_count: number;
  last_run_at?: string;
  runnable: boolean;
  validation_errors?: string[];
}

export interface PresetSummary {
  id: string;
  name: string;
  description?: string;
  document_count: number;
  model_count: number;
  created_at: string;
  updated_at?: string;
  run_count: number;
  runnable: boolean;
}

export interface PresetList {
  items: PresetSummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// API Functions

export async function createPreset(data: PresetCreate): Promise<PresetResponse> {
  return apiClient.post<PresetResponse>('/presets', data);
}

export async function listPresets(
  page: number = 1,
  pageSize: number = 20
): Promise<PresetList> {
  return apiClient.get<PresetList>('/presets', {
    page: page.toString(),
    page_size: pageSize.toString(),
  });
}

export async function getPreset(presetId: string): Promise<PresetResponse> {
  return apiClient.get<PresetResponse>(`/presets/${presetId}`);
}

export async function updatePreset(
  presetId: string,
  data: PresetUpdate
): Promise<PresetResponse> {
  return apiClient.put<PresetResponse>(`/presets/${presetId}`, data);
}

export async function deletePreset(
  presetId: string,
  permanent: boolean = false
): Promise<{ status: string; preset_id: string; permanent: boolean }> {
  const params = permanent ? { permanent: 'true' } : undefined;
  return apiClient.delete(`/presets/${presetId}`, params);
}

export async function duplicatePreset(
  presetId: string,
  newName: string
): Promise<PresetResponse> {
  return apiClient.post<PresetResponse>(
    `/presets/${presetId}/duplicate`,
    null,
    { new_name: newName }
  );
}

export async function executePreset(
  presetId: string
): Promise<{ status: string; run_id: string; preset_id: string; preset_name: string }> {
  return apiClient.post(`/presets/${presetId}/execute`);
}
