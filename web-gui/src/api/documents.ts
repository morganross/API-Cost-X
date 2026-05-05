import { apiClient } from './client'

// Types matching API schemas
export interface Document {
  id: string
  name: string
  path: string
  content?: string
  size_bytes: number
  word_count: number
  created_at: string
  updated_at?: string
}

export interface AddDocumentRequest {
  name: string
  path: string
  content?: string
}

export const documentsApi = {
  // List all documents
  async list(params?: { search?: string }): Promise<Document[]> {
    const query = new URLSearchParams()
    if (params?.search) query.set('search', params.search)
    const queryString = query.toString()
    return apiClient.get<Document[]>(`/documents${queryString ? `?${queryString}` : ''}`)
  },

  // Get a single document by ID
  async get(id: string): Promise<Document> {
    return apiClient.get<Document>(`/documents/${id}`)
  },

  // Add a document
  async add(data: AddDocumentRequest): Promise<Document> {
    return apiClient.post<Document>('/documents', data)
  },

  // Delete a document
  async delete(id: string): Promise<void> {
    return apiClient.delete<void>(`/documents/${id}`)
  },

  // Get document content
  async content(id: string): Promise<{ content: string }> {
    return apiClient.get<{ content: string }>(`/documents/${id}/content`)
  },
}
