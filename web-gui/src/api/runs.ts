import { apiClient } from './client'
import type { RunEstimateSnapshot } from './presets'

// Types matching API schemas
export interface RunConfig {
  title: string
  description?: string
  documents: string[]
  models: string[]
  generators: string[]
  iterations: number
  evaluation_enabled: boolean
  pairwise_enabled: boolean
  log_level?: string  // Legacy API field
  config_overrides?: Record<string, unknown>
  run_estimate?: RunEstimateSnapshot
}

export interface GeneratedDocInfo {
  id: string
  model: string
  source_doc_id: string
  generator: string
  iteration: number
  completion_status?: string | null
  incomplete_reason?: string | null
}

export interface PairwiseRanking {
  doc_id: string
  wins: number
  losses: number
  elo: number
  colley?: number
  massey?: number
  bradley_terry?: number
}

export interface PairwiseComparison {
  doc_id_a: string
  doc_id_b: string
  winner: string  // doc_id of winner
  judge_model: string
  trial?: number
  reason: string
}

export interface PairwiseResults {
  total_comparisons: number
  winner_doc_id?: string
  rankings: PairwiseRanking[]
  comparisons?: PairwiseComparison[]  // Head-to-head comparisons
  pairwise_deviations?: Record<string, number>  // { judge_model: deviation_int } - percentage deviation from mean agreement rate
}

// ============================================================================
// Timeline & Generation Events
// ============================================================================

export type TimelinePhase =
  | 'initialization'
  | 'generation'
  | 'evaluation'
  | 'pairwise'
  | 'combination'
  | 'completion'

export interface TimelineEvent {
  phase: TimelinePhase
  event_type: string
  description: string
  model?: string | null
  timestamp?: string | null
  completed_at?: string | null  // ISO timestamp for end time
  duration_seconds?: number | null
  success?: boolean
  details?: Record<string, unknown> | null
}

export interface GenerationEvent {
  doc_id: string
  generator: string  // fpf, gptr, dr
  model?: string | null  // provider:model
  source_doc_id?: string | null
  iteration?: number
  duration_seconds?: number | null
  success?: boolean
  status?: 'pending' | 'running' | 'completed' | 'failed'
  error?: string
  token_count?: number
  started_at?: string | null  // ISO timestamp
  completed_at?: string | null  // ISO timestamp
}

// ============================================================================
// Detailed Evaluation Types
// ============================================================================

export interface CriterionScoreInfo {
  criterion: string
  score: number  // 1-5 scale
  reason: string  // Evaluator's rationale/explanation
}

export interface JudgeEvaluation {
  judge_model: string
  trial: number
  scores: CriterionScoreInfo[]  // Score per criterion
  average_score: number
}

export interface DocumentEvalDetail {
  evaluations: JudgeEvaluation[]  // All evaluations by all judges
  overall_average: number
}

export interface FpfStats {
  total_calls: number
  successful_calls: number
  failed_calls: number
  retries: number
  current_phase?: string
  current_call?: string
  last_error?: string
}

// ============================================================================
// Per-Source-Document Results (Multi-Doc Pipeline)
// ============================================================================

export type SourceDocStatus =
  | 'pending'
  | 'generating'
  | 'single_eval'
  | 'pairwise_eval'
  | 'combining'
  | 'post_combine_eval'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'
  | 'incomplete'

export interface SourceDocResult {
  source_doc_id: string
  source_doc_name: string
  status: SourceDocStatus

  // Generated documents for this source
  generated_docs: GeneratedDocInfo[]
  generated_doc_count?: number

  // Evaluation results
  single_eval_scores: Record<string, number>  // { gen_doc_id: avg_score }
  single_eval_score_count?: number
  single_eval_detailed?: Record<string, DocumentEvalDetail>
  pairwise_results?: PairwiseResults

  // Winner and combined output
  winner_doc_id?: string
  combined_doc?: GeneratedDocInfo  // Legacy: first combined doc
  combined_docs?: GeneratedDocInfo[]  // All combined docs
  combined_doc_count?: number

  // Post-combine evaluation
  post_combine_eval_scores?: Record<string, number>
  post_combine_eval_score_count?: number
  post_combine_pairwise?: PairwiseResults

  // Timeline events for this source doc
  timeline_events?: TimelineEvent[]

  // Per-document stats
  errors: string[]
  duration_seconds: number
  started_at?: string
  completed_at?: string

  // Deviation data for judges
  eval_deviations?: Record<string, Record<string, number>>  // { judge_model: { criterion: deviation, __TOTAL__: total } }

}

export interface SourceDocEvaluationMeta {
  total_count: number
  returned_count: number
  offset: number
  limit: number
  has_more: boolean
}

export interface Run {
  id: string
  title: string
  name?: string
  description?: string
  status: 'pending' | 'running' | 'paused' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled'
  mode?: string
  preset_id?: string
  log_level?: string  // Legacy API field
  config: RunConfig
  progress: {
    total_tasks: number
    completed_tasks: number
    failed_tasks: number
    current_task?: string
  }
  fpf_stats?: FpfStats
  tasks?: any[]
  current_phase?: string
  created_at: string
  started_at?: string
  completed_at?: string
  // Compatibility fields
  documentCount?: number
  modelCount?: number
  createdAt?: string
  completedAt?: string
  error_message?: string
  winner?: string
  // Structured evaluation data
  generated_docs?: GeneratedDocInfo[]  // List of generated documents
  post_combine_evals?: Record<string, Record<string, number>>  // { combined_doc_id: { judge_model: score } }
  pairwise_results?: PairwiseResults
  post_combine_pairwise?: PairwiseResults  // Pairwise comparison: combined doc vs winner
  combined_doc_ids?: string[]  // All combined document IDs
  // Detailed evaluation data with criteria breakdown
  pre_combine_evals_detailed?: Record<string, DocumentEvalDetail>  // { gen_doc_id: DocumentEvalDetail }
  post_combine_evals_detailed?: Record<string, DocumentEvalDetail>  // { combined_doc_id: DocumentEvalDetail }
  eval_deviations?: Record<string, Record<string, number>>  // { judge_model: { criterion: deviation_int } }
  criteria_list?: string[]  // All criteria used
  evaluator_list?: string[]  // All evaluator model names
  // Timeline events
  timeline_events?: TimelineEvent[]  // All timeline events
  // === NEW: Per-source-document results (multi-doc pipeline) ===
  source_doc_results?: Record<string, SourceDocResult>  // { source_doc_id: SourceDocResult }
  // UI-specific fields
  duration_seconds?: number // For running time display
  // Resume feature
  pause_requested?: number  // 1 = pause requested, 0 = none
  resume_count?: number    // how many times this run has been resumed
  run_estimate?: RunEstimateSnapshot
}

export interface RunLiveSummary {
  id: string
  status: Run['status']
  progress: Run['progress']
  started_at?: string
  completed_at?: string
  error_message?: string
  pause_requested?: number
  resume_count?: number
  fpf_stats?: FpfStats | null
  source_doc_results?: Record<string, Partial<SourceDocResult>>
}

export interface ResumeCheckpointCounts {
  completed: number
  total: number
  skipped: number
}

export interface RunResumeInfo {
  run_id: string
  run_status: Run['status']
  resumable: boolean
  resume_mode: string
  reason: string
  has_active_executor: boolean
  requires_preset: boolean
  phase_hint?: string | null
  stale_running_tasks: number
  reusable_generation_tasks: number
  reusable_eval_tasks: number
  reusable_pairwise_tasks: number
  reusable_pre_combine_pairwise_tasks: number
  reusable_post_combine_pairwise_tasks: number
  reusable_combine_tasks: number
  checkpoint_summary: Record<string, ResumeCheckpointCounts>
  warnings: string[]
  blocking_errors: string[]
}

export interface GetRunOptions {
  include?: string
  source_doc_id?: string
}

export const EXECUTION_RUN_DETAIL_INCLUDE = 'source_doc_overview'
export interface CreateRunRequest {
  name: string
  description?: string
  preset_id?: string  // Link to preset
  tags?: string[]
}

export interface GeneratedDocumentContent {
  id: string
  content: string
  filename?: string
}

export const runsApi = {
  // List all runs with optional filters
  async list(params?: { status?: string; limit?: number; offset?: number }): Promise<Run[]> {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.limit) query.set('limit', params.limit.toString())
    if (params?.offset) query.set('offset', params.offset.toString())
    const queryString = query.toString()
    const resp = await apiClient.get<{ items: any[]; total: number; page: number; page_size: number; pages: number }>(`/runs${queryString ? `?${queryString}` : ''}`)
    // API returns paginated response with items array
    return (resp.items || []).map(runsApi.mapRun)
  },

  async getLiveSummary(id: string): Promise<RunLiveSummary> {
    const resp = await apiClient.get<any>(`/runs/${id}/live-summary`)
    return runsApi.mapRunLiveSummary(resp)
  },

  async getSnapshot(id: string): Promise<Run> {
    const resp = await apiClient.get<any>(`/runs/${id}/snapshot`)
    return runsApi.mapRun(resp)
  },

  async getGeneratedDocumentContent(id: string, docId: string): Promise<GeneratedDocumentContent> {
    return apiClient.get<GeneratedDocumentContent>(`/runs/${id}/generated/${docId}`)
  },

  async getExecutionView(id: string): Promise<Run> {
    const resp = await apiClient.get<any>(`/runs/${id}`, {
      include: EXECUTION_RUN_DETAIL_INCLUDE,
    })
    return runsApi.mapRun(resp)
  },

  async getSourceDocEvaluation(
    id: string,
    sourceDocId: string,
    params?: { limit?: number; offset?: number }
  ): Promise<{ evaluation: SourceDocResult | null; meta: SourceDocEvaluationMeta | null }> {
    const resp = await apiClient.get<{ evaluation?: SourceDocResult | null; evaluation_meta?: SourceDocEvaluationMeta | null }>(`/runs/${id}/sections/evaluation`, {
      source_doc_id: sourceDocId,
      ...(params?.limit !== undefined ? { limit: params.limit } : {}),
      ...(params?.offset !== undefined ? { offset: params.offset } : {}),
    })
    return {
      evaluation: resp.evaluation ?? null,
      meta: resp.evaluation_meta ?? null,
    }
  },

  // Helper: Map API run shape into UI Run
  mapRun: (r: any): Run => {
    const tasks = r.tasks || []
    const total = tasks.length
    const completed = tasks.filter((t: any) => t.status === 'completed').length
    const failed = tasks.filter((t: any) => t.status === 'failed').length

    return {
      id: r.id,
      title: r.name,
      name: r.name,
      description: r.description,
      status: r.status,
      preset_id: r.preset_id,
      log_level: r.log_level,  // Include log_level from API response
      // Compatibility fields expected by UI components
      documentCount: r.document_count || (r.document_ids ? r.document_ids.length : 0),
      modelCount: r.model_count || (r.models ? r.models.length : 0),
      createdAt: r.created_at,
      created_at: r.created_at,
      completedAt: r.completed_at,
      completed_at: r.completed_at,
      started_at: r.started_at,
      config: {
        title: r.name,
        description: r.description,
        documents: r.document_ids,
        models: (r.models || []).map((m: any) => m.provider ? `${m.provider}:${m.model}` : m),
        generators: (r.generators || []),
        iterations: r.iterations,
        evaluation_enabled: r.evaluation?.enabled ?? true,
        pairwise_enabled: r.pairwise?.enabled ?? false,
        run_estimate: r.run_estimate,
      },
      // Backwards compatible mode field - default to full
      mode: 'full',
      progress: typeof r.progress === 'number' ? {
        total_tasks: total,
        completed_tasks: completed,
        failed_tasks: failed,
        current_task: tasks.find((t: any) => t.status === 'running')?.name
      } : r.progress,
      tasks: r.tasks,
      current_phase: r.status === 'running' ? 'Processing' : undefined,
      error_message: r.error_message,
      winner: r.winner,
      // Structured evaluation data
      generated_docs: r.generated_docs || [],
      post_combine_evals: r.post_combine_evals || {},
      pairwise_results: r.pairwise_results,
      post_combine_pairwise: r.post_combine_pairwise,
      combined_doc_ids: r.combined_doc_ids || [],
      // Detailed evaluation data
      pre_combine_evals_detailed: r.pre_combine_evals_detailed || {},
      post_combine_evals_detailed: r.post_combine_evals_detailed || {},
      criteria_list: r.criteria_list || [],
      evaluator_list: r.evaluator_list || [],
      // Timeline events
      timeline_events: r.timeline_events || [],
      // Per-source-document results (multi-doc pipeline)
      source_doc_results: r.source_doc_results || {},
      pause_requested: r.pause_requested ?? 0,
      resume_count: r.resume_count ?? 0,
      run_estimate: r.run_estimate,
    }
  },

  mapRunLiveSummary: (r: any): RunLiveSummary => ({
    id: r.id,
    status: r.status,
    progress: r.progress,
    started_at: r.started_at,
    completed_at: r.completed_at,
    error_message: r.error_message,
    pause_requested: r.pause_requested ?? 0,
    resume_count: r.resume_count ?? 0,
    fpf_stats: r.fpf_stats ?? null,
    ...(r.source_doc_results ? { source_doc_results: r.source_doc_results } : {}),
  }),


  // Create a new run
  async create(data: CreateRunRequest): Promise<Run> {
    const resp = await apiClient.post<any>('/runs', {
      name: data.name,
      description: data.description,
      preset_id: data.preset_id,
      tags: data.tags ?? [],
    })
    return runsApi.mapRun(resp)
  },

  // Start a run
  async start(id: string): Promise<Run> {
    const resp = await apiClient.post<any>(`/runs/${id}/start`)
    return runsApi.mapRun(resp)
  },

  // Pause a run
  async pause(id: string): Promise<Run> {
    const resp = await apiClient.post<any>(`/runs/${id}/pause`)
    return runsApi.mapRun(resp)
  },

  // Resume a paused or failed run
  async resume(id: string): Promise<{ status: string; run_id: string; resume_count: number }> {
    return apiClient.post<{ status: string; run_id: string; resume_count: number }>(`/runs/${id}/resume`)
  },

  async getResumeInfo(id: string): Promise<RunResumeInfo> {
    return apiClient.get<RunResumeInfo>(`/runs/${id}/resume-info`)
  },

  // Cancel a running run
  async cancel(id: string): Promise<Run> {
    const resp = await apiClient.post<any>(`/runs/${id}/cancel`)
    return runsApi.mapRun(resp)
  },

  // Delete a run
  async delete(id: string): Promise<void> {
    return apiClient.delete<void>(`/runs/${id}`)
  },

  async bulkDelete(target: 'failed' | 'completed_failed'): Promise<{ deleted: number; target: string }> {
    return apiClient.delete<{ deleted: number; target: string }>(`/runs/bulk`, { target })
  },

  async progress(id: string): Promise<Run['progress']> {
    const summary = await this.getLiveSummary(id)
    return summary.progress
  },

  // Get checkpoint summary (task-level completion counts per phase)
  async getCheckpoint(id: string): Promise<Record<string, any>> {
    return apiClient.get<Record<string, any>>(`/runs/${id}/checkpoint`)
  },
}
