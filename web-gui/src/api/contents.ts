/**
 * Content API client
 *
 * For managing content (instructions, criteria, fragments, input documents)
 */
import { apiClient } from './client'

// ============================================================================
// Types
// ============================================================================

export type ContentType =
  | 'generation_instructions'
  | 'input_document'
  | 'single_eval_instructions'
  | 'pairwise_eval_instructions'
  | 'eval_criteria'
  | 'combine_instructions'
  | 'template_fragment'
  | 'logs'

export interface ContentSummary {
  id: string
  name: string
  content_type: ContentType
  description: string | null
  tags: string[]
  body_preview: string
  created_at: string
  updated_at: string | null
}

export interface ContentDetail {
  id: string
  name: string
  content_type: ContentType
  body: string
  variables: Record<string, string | null>
  description: string | null
  tags: string[]
  created_at: string
  updated_at: string | null
}

export interface ContentList {
  items: ContentSummary[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface ContentCreate {
  name: string
  content_type: ContentType
  body: string
  variables?: Record<string, string | null>
  description?: string
  tags?: string[]
}

export interface ContentUpdate {
  name?: string
  body?: string
  variables?: Record<string, string | null>
  description?: string
  tags?: string[]
}

export interface ContentResolved {
  id: string
  name: string
  content_type: ContentType
  resolved_body: string
  unresolved_variables: string[]
}

export interface ContentTypeCounts {
  generation_instructions: number
  input_document: number
  single_eval_instructions: number
  pairwise_eval_instructions: number
  eval_criteria: number
  combine_instructions: number
  template_fragment: number
  logs: number
  total: number
}

// ============================================================================
// API Functions
// ============================================================================

export const contentsApi = {
  /**
   * List contents with optional filtering
   */
  async list(params?: {
    content_type?: ContentType
    search?: string
    tag?: string
    page?: number
    page_size?: number
  }): Promise<ContentList> {
    return apiClient.get<ContentList>('/contents', params as Record<string, string | number>)
  },

  /**
   * Get content type counts
   */
  async counts(): Promise<ContentTypeCounts> {
    return apiClient.get<ContentTypeCounts>('/contents/counts')
  },

  /**
   * Get a single content by ID
   */
  async get(id: string): Promise<ContentDetail> {
    return apiClient.get<ContentDetail>(`/contents/${id}`)
  },

  /**
   * Create new content
   */
  async create(data: ContentCreate): Promise<ContentDetail> {
    return apiClient.post<ContentDetail>('/contents', data)
  },

  /**
   * Update content
   */
  async update(id: string, data: ContentUpdate): Promise<ContentDetail> {
    return apiClient.put<ContentDetail>(`/contents/${id}`, data)
  },

  /**
   * Delete content (soft delete)
   */
  async delete(id: string): Promise<void> {
    return apiClient.delete(`/contents/${id}`)
  },

  /**
   * Resolve content with variable substitution
   */
  async resolve(id: string, runtimeVariables: Record<string, string>): Promise<ContentResolved> {
    return apiClient.post<ContentResolved>(`/contents/${id}/resolve`, {
      runtime_variables: runtimeVariables
    })
  },

  /**
   * Duplicate content
   */
  async duplicate(id: string, name?: string): Promise<ContentDetail> {
    const params = name ? { name } : undefined
    return apiClient.post<ContentDetail>(`/contents/${id}/duplicate`, undefined, params)
  },

  /**
   * Get contents by type (convenience method)
   */
  async listByType(contentType: ContentType, page = 1, pageSize = 100): Promise<ContentList> {
    return this.list({ content_type: contentType, page, page_size: pageSize })
  },

  /**
   * Get all generation instructions
   */
  async getGenerationInstructions(): Promise<ContentSummary[]> {
    const result = await this.listByType('generation_instructions')
    return result.items
  },

  /**
   * Get all input documents
   */
  async getInputDocuments(): Promise<ContentSummary[]> {
    const result = await this.listByType('input_document')
    return result.items
  },

  /**
   * Get all eval criteria
   */
  async getEvalCriteria(): Promise<ContentSummary[]> {
    const result = await this.listByType('eval_criteria')
    return result.items
  },
}

// Helper to get human-readable content type labels
export const contentTypeLabels: Record<ContentType, string> = {
  generation_instructions: 'Generation Instructions',
  input_document: 'Input Documents',
  single_eval_instructions: 'Single Eval Instructions',
  pairwise_eval_instructions: 'Pairwise Eval Instructions',
  eval_criteria: 'Evaluation Criteria',
  combine_instructions: 'Combine Instructions',
  template_fragment: 'Template Fragments',
  logs: 'Run Logs',
}

// Helper to get content type icons (using emoji for now)
export const contentTypeIcons: Record<ContentType, string> = {
  generation_instructions: '📝',
  input_document: '📄',
  single_eval_instructions: '📊',
  pairwise_eval_instructions: '⚖️',
  eval_criteria: '📋',
  combine_instructions: '🔗',
  template_fragment: '🧩',
  logs: 'LOG',
}
