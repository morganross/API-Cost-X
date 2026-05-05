import { apiClient } from './client'

export interface EvaluationCriteria {
  name: string
  description: string
  weight: number
}

export interface EvaluationCriteriaResponse {
  criteria: EvaluationCriteria[]
}

export interface SingleDocEvaluationRequest {
  document_path: string
  content: string
  criteria?: EvaluationCriteria[]
  model?: string
}

export interface SingleDocEvaluationResult {
  document_path: string
  scores: Record<string, number>
  reasoning: Record<string, string>
  total_score: number
  judge_model: string
  timestamp: string
}

export interface PairwiseEvaluationRequest {
  doc_a_path: string
  doc_a_content: string
  doc_b_path: string
  doc_b_content: string
  criteria?: EvaluationCriteria[]
  model?: string
}

export interface PairwiseEvaluationResult {
  doc_a_path: string
  doc_b_path: string
  winner: 'A' | 'B' | 'Tie'
  reasoning: string
  judge_model: string
  timestamp: string
}

export interface FullEvaluationRequest {
  documents: Array<{ path: string; content: string }>
  criteria?: EvaluationCriteria[]
  model?: string
  pairwise_top_n?: number
}

export interface EvaluationSummary {
  single_evals: Record<string, SingleDocEvaluationResult>
  pairwise_evals: PairwiseEvaluationResult[]
  elo_ratings: Record<string, number>
  rankings: Array<{ path: string; score: number; rank: number }>
  winner: string
}

export interface EvaluationJob {
  id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  created_at: string
  completed_at?: string
  error?: string
  result?: EvaluationSummary
}

export const evaluationApi = {
  async getCriteria(): Promise<EvaluationCriteriaResponse> {
    return apiClient.get<EvaluationCriteriaResponse>('/evaluation/criteria')
  },

  async evaluateSingle(data: SingleDocEvaluationRequest): Promise<SingleDocEvaluationResult> {
    return apiClient.post<SingleDocEvaluationResult>('/evaluation/single', data)
  },

  async evaluatePairwise(data: PairwiseEvaluationRequest): Promise<PairwiseEvaluationResult> {
    return apiClient.post<PairwiseEvaluationResult>('/evaluation/pairwise', data)
  },

  async evaluateFull(data: FullEvaluationRequest): Promise<EvaluationSummary> {
    return apiClient.post<EvaluationSummary>('/evaluation/full', data)
  },

  async evaluateFullAsync(data: FullEvaluationRequest): Promise<{ job_id: string }> {
    return apiClient.post<{ job_id: string }>('/evaluation/full/async', data)
  },

  async getJob(jobId: string): Promise<EvaluationJob> {
    return apiClient.get<EvaluationJob>(`/evaluation/jobs/${jobId}`)
  }
}
