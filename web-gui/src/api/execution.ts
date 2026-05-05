// execution.ts - Generation task API functions

import { apiClient } from './client'

// Types matching API schemas
export interface GenerationTask {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  generator: string
  query: string
  model: string
  provider: string
  progress: number
  current_stage?: string
  result?: GenerationResult
  error_message?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

export interface GenerationResult {
  content: string
  content_type: string
  model: string
  provider: string
  duration_seconds: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  sources: Array<{ url: string; title?: string }>
  images: string[]
}

export interface StartGenerationRequest {
  query: string
  generator?: string
  provider?: string
  model?: string
  report_type?: string
  tone?: string
}

export interface AdapterInfo {
  name: string
  display_name: string
  available: boolean
  health_message?: string
}

export const executionApi = {
  // Start a new generation task
  async start(data: StartGenerationRequest): Promise<GenerationTask> {
    return apiClient.post<GenerationTask>('/generation/start', data)
  },

  // Get task status
  async getTask(taskId: string): Promise<GenerationTask> {
    return apiClient.get<GenerationTask>(`/generation/${taskId}`)
  },

  // Cancel a task
  async cancelTask(taskId: string): Promise<GenerationTask> {
    return apiClient.post<GenerationTask>(`/generation/${taskId}/cancel`)
  },

  // List available adapters
  async listAdapters(): Promise<AdapterInfo[]> {
    return apiClient.get<AdapterInfo[]>('/generation/adapters')
  },
}
