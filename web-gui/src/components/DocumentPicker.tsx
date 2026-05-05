import { useState } from 'react'
import { FileText, Folder, Check, Search } from 'lucide-react'

interface Document {
  id: string
  name: string
  path: string
  type: 'file' | 'folder'
}

export default function DocumentPicker() {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedDocs, setSelectedDocs] = useState<string[]>([])

  // Placeholder - will be replaced with useQuery
  const documents: Document[] = []

  const toggleDocument = (id: string) => {
    setSelectedDocs((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]
    )
  }

  const selectAll = () => {
    setSelectedDocs(documents.map((d) => d.id))
  }

  const clearSelection = () => {
    setSelectedDocs([])
  }

  return (
    <div className="space-y-4 pt-4">
      {/* Search and Actions */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search documents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border rounded-md bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <button
          type="button"
          onClick={selectAll}
          className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors text-foreground"
        >
          Select All
        </button>
        <button
          type="button"
          onClick={clearSelection}
          className="px-3 py-2 text-sm border rounded-md hover:bg-accent transition-colors text-foreground"
        >
          Clear
        </button>
      </div>

      {/* Selection Summary */}
      <div className="text-sm text-muted-foreground">
        {selectedDocs.length} of {documents.length} documents selected
      </div>

      {/* Document List */}
      {documents.length === 0 ? (
        <div className="border rounded-md p-8 text-center">
          <FileText className="h-8 w-8 mx-auto mb-2 text-muted-foreground opacity-50" />
          <p className="text-sm text-muted-foreground">
            No documents available
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Upload documents in the Documents tab first
          </p>
        </div>
      ) : (
        <div className="border rounded-md divide-y max-h-64 overflow-auto">
          {documents
            .filter((doc) =>
              doc.name.toLowerCase().includes(searchQuery.toLowerCase())
            )
            .map((doc) => (
              <label
                key={doc.id}
                className="flex items-center gap-3 p-3 hover:bg-accent cursor-pointer transition-colors"
              >
                <div
                  className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                    selectedDocs.includes(doc.id)
                      ? 'bg-primary border-primary'
                      : 'border-input'
                  }`}
                >
                  {selectedDocs.includes(doc.id) && (
                    <Check className="h-3 w-3 text-primary-foreground" />
                  )}
                </div>
                <input
                  type="checkbox"
                  checked={selectedDocs.includes(doc.id)}
                  onChange={() => toggleDocument(doc.id)}
                  className="sr-only"
                />
                {doc.type === 'folder' ? (
                  <Folder className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <FileText className="h-4 w-4 text-muted-foreground" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground truncate">
                    {doc.name}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">
                    {doc.path}
                  </p>
                </div>
              </label>
            ))}
        </div>
      )}
    </div>
  )
}
