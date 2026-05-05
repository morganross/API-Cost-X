import { useState } from 'react'
import { Upload, FileText, Folder, Search } from 'lucide-react'

export default function Documents() {
  const [searchQuery, setSearchQuery] = useState('')

  // Placeholder - will be replaced with useQuery
  const documents: unknown[] = []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-foreground">Documents</h1>
        <div className="flex gap-2">
          <button className="inline-flex items-center gap-2 px-4 py-2 border rounded-md text-foreground hover:bg-accent transition-colors">
            <Folder className="h-4 w-4" />
            Import Folder
          </button>
          <button className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
            <Upload className="h-4 w-4" />
            Upload
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search documents..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2 border rounded-md bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {/* Documents List */}
      {documents.length === 0 ? (
        <div className="bg-card border rounded-lg p-12 text-center">
          <FileText className="h-12 w-12 mx-auto mb-3 text-muted-foreground opacity-50" />
          <p className="text-muted-foreground">No documents yet</p>
          <p className="text-sm text-muted-foreground mt-1">
            Upload markdown documents to evaluate them
          </p>
          <div className="flex justify-center gap-2 mt-4">
            <button className="inline-flex items-center gap-2 px-4 py-2 border rounded-md text-foreground hover:bg-accent transition-colors">
              <Folder className="h-4 w-4" />
              Import Folder
            </button>
            <button className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
              <Upload className="h-4 w-4" />
              Upload Files
            </button>
          </div>
        </div>
      ) : (
        <div className="bg-card border rounded-lg divide-y">
          {/* Document items would go here */}
        </div>
      )}

      {/* Drop Zone */}
      <div className="border-2 border-dashed rounded-lg p-8 text-center transition-colors hover:border-primary/50 cursor-pointer">
        <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">
          Drag and drop files here, or click to browse
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Supports .md, .mdx, .txt files
        </p>
      </div>
    </div>
  )
}
