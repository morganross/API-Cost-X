export interface Document {
  id: string
  name: string
  path: string
  content?: string
  wordCount?: number
  createdAt: string
  updatedAt: string
}

export interface DocumentFolder {
  id: string
  name: string
  path: string
  documentCount: number
}

export interface UploadDocumentRequest {
  name: string
  content: string
  path?: string
}

export interface ImportFolderRequest {
  folderPath: string
  recursive?: boolean
}
