/**
 * GitHub Connection API client
 *
 * For managing GitHub repository connections
 */
import { apiClient } from './client'
import type { ContentDetail } from './contents'

// ============================================================================
// Types
// ============================================================================

export interface GitHubConnectionSummary {
  id: string
  name: string
  repo: string
  branch: string
  is_valid: boolean
  last_tested_at: string | null
  created_at: string
}

export interface GitHubConnectionDetail {
  id: string
  name: string
  repo: string
  branch: string
  is_valid: boolean
  last_tested_at: string | null
  last_error: string | null
  created_at: string
  updated_at: string | null
}

export interface GitHubConnectionList {
  items: GitHubConnectionSummary[]
  total: number
}

export interface GitHubConnectionCreate {
  name: string
  repo: string  // "owner/repo" format
  branch?: string
  token?: string
}

export interface GitHubConnectionUpdate {
  name?: string
  branch?: string
  token?: string  // Ignored; use GITHUB_TOKEN in root .env
}

export interface GitHubConnectionTestResult {
  id: string
  is_valid: boolean
  message: string
  tested_at: string
}

export interface GitHubFileInfo {
  name: string
  path: string
  type: 'file' | 'dir'
  size: number | null
  download_url: string | null
}

export interface GitHubBrowseResponse {
  connection_id: string
  repo: string
  branch: string
  path: string
  contents: GitHubFileInfo[]
}

export interface GitHubFileContent {
  connection_id: string
  path: string
  name: string
  content: string
  size: number
  encoding: string
}

export interface GitHubImportRequest {
  path: string
  content_type: string
  name?: string
  description?: string
  tags?: string[]
}

// ============================================================================
// API Functions
// ============================================================================

export const githubApi = {
  /**
   * List all GitHub connections
   */
  async list(): Promise<GitHubConnectionList> {
    return apiClient.get<GitHubConnectionList>('/github-connections')
  },

  /**
   * Get a single connection by ID
   */
  async get(id: string): Promise<GitHubConnectionDetail> {
    return apiClient.get<GitHubConnectionDetail>(`/github-connections/${id}`)
  },

  /**
   * Create a new GitHub connection
   */
  async create(data: GitHubConnectionCreate): Promise<GitHubConnectionDetail> {
    return apiClient.post<GitHubConnectionDetail>('/github-connections', data)
  },

  /**
   * Update a GitHub connection
   */
  async update(id: string, data: GitHubConnectionUpdate): Promise<GitHubConnectionDetail> {
    return apiClient.put<GitHubConnectionDetail>(`/github-connections/${id}`, data)
  },

  /**
   * Delete a GitHub connection
   */
  async delete(id: string): Promise<void> {
    return apiClient.delete(`/github-connections/${id}`)
  },

  /**
   * Test a GitHub connection
   */
  async test(id: string): Promise<GitHubConnectionTestResult> {
    return apiClient.post<GitHubConnectionTestResult>(`/github-connections/${id}/test`)
  },

  /**
   * Browse files in a GitHub repository
   */
  async browse(connectionId: string, path = '/'): Promise<GitHubBrowseResponse> {
    return apiClient.get<GitHubBrowseResponse>(`/github-connections/${connectionId}/browse`, { path })
  },

  /**
   * Get file content from GitHub
   */
  async getFile(connectionId: string, path: string): Promise<GitHubFileContent> {
    return apiClient.get<GitHubFileContent>(`/github-connections/${connectionId}/file`, { path })
  },

  /**
   * Import a file from GitHub as content
   */
  async importFile(connectionId: string, data: GitHubImportRequest): Promise<ContentDetail> {
    return apiClient.post<ContentDetail>(`/github-connections/${connectionId}/import`, data)
  },

  /**
   * Create a folder in a GitHub repository
   */
  async createFolder(connectionId: string, data: { path: string }): Promise<void> {
    return apiClient.post(`/github-connections/${connectionId}/create-folder`, data)
  },
}
