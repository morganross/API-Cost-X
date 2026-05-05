import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Github,
  Plus,
  Trash2,
  RefreshCw,
  CheckCircle2,
  XCircle,
  FolderOpen,
  ChevronRight,
  FileText,
  Folder,
  X,
  ArrowLeft,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/modal'
import { cn } from '@/lib/utils'
import { notify } from '@/stores/notifications'
import { githubApi, type GitHubConnectionSummary, type GitHubFileInfo } from '@/api/github'

export default function GitHubConnectionsPanel() {
  const queryClient = useQueryClient()
  const [showAddModal, setShowAddModal] = useState(false)
  const [showBrowseModal, setShowBrowseModal] = useState(false)
  const [browseConnectionId, setBrowseConnectionId] = useState<string | null>(null)
  const [addForm, setAddForm] = useState({
    name: '',
    repo: '',
    branch: 'main',
  })


  const formatActionError = (error: unknown, errorMessage: string): string => {
    return error instanceof Error ? error.message : errorMessage
  }

  const { data: connections, isLoading } = useQuery({
    queryKey: ['github-connections'],
    queryFn: () => githubApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: githubApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['github-connections'] })
      setShowAddModal(false)
      setAddForm({ name: '', repo: '', branch: 'main' })
      notify.success('GitHub connection created')
    },
    onError: (error: unknown) => {
      const message = formatActionError(error, 'Failed to create connection')
      notify.error(`Failed to create connection: ${message}`)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: githubApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['github-connections'] })
      notify.success('Connection deleted')
    },
    onError: (error: unknown) => {
      const message = formatActionError(error, 'Failed to delete connection')
      notify.error(`Failed to delete: ${message}`)
    },
  })

  const testMutation = useMutation({
    mutationFn: githubApi.test,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-connections'] })
      if (result.is_valid) {
        notify.success('Connection test passed!')
      } else {
        notify.error(`Connection test failed: ${result.message}`)
      }
    },
    onError: (error: unknown) => {
      const message = formatActionError(error, 'Connection test failed')
      notify.error(`Test failed: ${message}`)
    },
  })

  const handleCreate = () => {
    if (!addForm.name || !addForm.repo) {
      notify.warning('Please fill in all required fields')
      return
    }
    createMutation.mutate(addForm)
  }

  const handleDelete = (id: string, name: string) => {
    if (confirm(`Delete connection "${name}"? This cannot be undone.`)) {
      deleteMutation.mutate(id)
    }
  }

  const handleBrowse = (id: string) => {
    setBrowseConnectionId(id)
    setShowBrowseModal(true)
  }


  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
      <div className="px-4 py-4 border-b border-gray-700 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Github className="h-6 w-6 text-gray-100" />
          <div>
            <h2 className="font-semibold text-gray-100">GitHub Connections</h2>
            <p className="text-sm text-gray-400">Manage repository connections for importing documents</p>
          </div>
        </div>
        <Button
          variant="primary"
          icon={<Plus className="h-4 w-4" />}
          onClick={() => setShowAddModal(true)}
        >
          Add Connection
        </Button>
      </div>


      {isLoading ? (
        <div className="p-8 text-center text-gray-400">
          <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
          Loading connections...
        </div>
      ) : !connections?.items?.length ? (
        <div className="p-8 text-center">
          <Github className="h-12 w-12 mx-auto mb-3 text-gray-400 opacity-50" />
          <p className="text-gray-400">No GitHub connections yet</p>
          <p className="text-sm text-gray-400 mt-1">Add a connection to import documents from GitHub</p>
          <Button
            variant="outline"
            className="mt-4"
            icon={<Plus className="h-4 w-4" />}
            onClick={() => setShowAddModal(true)}
          >
            Add First Connection
          </Button>
        </div>
      ) : (
        <table className="w-full">
          <thead className="bg-gray-700/30 border-b border-gray-700">
            <tr>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-100">Name</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-100">Repository</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-100">Branch</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-100">Status</th>
              <th className="text-left px-4 py-3 text-sm font-medium text-gray-100">Last Tested</th>
              <th className="text-right px-4 py-3 text-sm font-medium text-gray-100">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {connections.items.map((conn: GitHubConnectionSummary) => (
              <tr key={conn.id} className="hover:bg-gray-700/30 transition-colors">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Github className="h-4 w-4 text-gray-400" />
                    <span className="font-medium text-gray-100">{conn.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-sm text-gray-400 font-mono">{conn.repo}</td>
                <td className="px-4 py-3 text-sm text-gray-400">{conn.branch}</td>
                <td className="px-4 py-3">
                  {conn.is_valid ? (
                    <span className="inline-flex items-center gap-1 text-sm text-green-400">
                      <CheckCircle2 className="h-4 w-4" />
                      Valid
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-sm text-red-400">
                      <XCircle className="h-4 w-4" />
                      Invalid
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-sm text-gray-400">
                  {conn.last_tested_at ? new Date(conn.last_tested_at).toLocaleDateString() : 'Never'}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<FolderOpen className="h-4 w-4" />}
                      onClick={() => handleBrowse(conn.id)}
                    >
                      Browse
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<RefreshCw className={cn('h-4 w-4', testMutation.isPending && 'animate-spin')} />}
                      onClick={() => testMutation.mutate(conn.id)}
                      disabled={testMutation.isPending}
                    >
                      Test
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<Trash2 className="h-4 w-4 text-red-400" />}
                      onClick={() => handleDelete(conn.id, conn.name)}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Modal open={showAddModal} onClose={() => setShowAddModal(false)} panelClassName="max-w-md p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-100">Add GitHub Connection</h2>
              <Button
                variant="ghost"
                size="sm"
                icon={<X className="h-4 w-4" />}
                onClick={() => setShowAddModal(false)}
              />
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-200 mb-1">Connection Name *</label>
                <input
                  type="text"
                  value={addForm.name}
                  onChange={(e) => setAddForm({ ...addForm, name: e.target.value })}
                  placeholder="My Research Repo"
                  className="w-full px-3 py-2 border border-gray-600 rounded-md bg-gray-900 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-200 mb-1">Repository *</label>
                <input
                  type="text"
                  value={addForm.repo}
                  onChange={(e) => {
                    let value = e.target.value
                    const urlMatch = value.match(/github\.com\/([^\/]+\/[^\/]+)/)
                    if (urlMatch) {
                      value = urlMatch[1].replace(/\.git$/, '')
                    }
                    setAddForm({ ...addForm, repo: value })
                  }}
                  placeholder="owner/repository or GitHub URL"
                  className="w-full px-3 py-2 border border-gray-600 rounded-md bg-gray-900 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-blue-500 font-mono"
                />
                <p className="text-xs text-gray-500 mt-1">Format: owner/repo or paste a GitHub URL</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-200 mb-1">Branch</label>
                <input
                  type="text"
                  value={addForm.branch}
                  onChange={(e) => setAddForm({ ...addForm, branch: e.target.value })}
                  placeholder="main"
                  className="w-full px-3 py-2 border border-gray-600 rounded-md bg-gray-900 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-blue-500"
                />
              </div>

              <div className="rounded-md border border-gray-700 bg-gray-900/70 px-3 py-2 text-xs text-gray-400">
                GitHub API access uses <span className="font-mono text-gray-200">GITHUB_TOKEN</span> from the root <span className="font-mono text-gray-200">.env</span> file. Tokens are not entered or stored in the GUI.
              </div>

            </div>

            <div className="flex justify-end gap-2 pt-4 border-t border-gray-700">
              <Button variant="ghost" onClick={() => setShowAddModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={handleCreate} loading={createMutation.isPending}>Add Connection</Button>
            </div>
      </Modal>

      {showBrowseModal && browseConnectionId && (
        <GitHubBrowseModal
          connectionId={browseConnectionId}
          onClose={() => {
            setShowBrowseModal(false)
            setBrowseConnectionId(null)
          }}
        />
      )}
    </div>
  )
}

function GitHubBrowseModal({
  connectionId,
  onClose,
}: {
  connectionId: string
  onClose: () => void
}) {
  const [currentPath, setCurrentPath] = useState('/')
  const [pathHistory, setPathHistory] = useState<string[]>(['/'])

  const { data: browseData, isLoading } = useQuery({
    queryKey: ['github-browse', connectionId, currentPath],
    queryFn: () => githubApi.browse(connectionId, currentPath),
  })

  const navigateTo = (path: string) => {
    setPathHistory([...pathHistory, path])
    setCurrentPath(path)
  }

  const navigateBack = () => {
    if (pathHistory.length > 1) {
      const newHistory = pathHistory.slice(0, -1)
      setPathHistory(newHistory)
      setCurrentPath(newHistory[newHistory.length - 1])
    }
  }

  return (
    <Modal open onClose={onClose} panelClassName="max-w-2xl max-h-[calc(100dvh-2rem)] flex flex-col overflow-hidden">
        <div className="p-4 border-b border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FolderOpen className="h-5 w-5 text-gray-400" />
            <div>
              <h2 className="font-semibold text-gray-100">Browse Repository</h2>
              <p className="text-sm text-gray-400 font-mono">{browseData?.repo}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" icon={<X className="h-4 w-4" />} onClick={onClose} />
        </div>

        <div className="px-4 py-2 border-b border-gray-700 bg-gray-700/30 flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            icon={<ArrowLeft className="h-4 w-4" />}
            onClick={navigateBack}
            disabled={pathHistory.length <= 1}
          />
          <span className="font-mono text-sm text-gray-400">{currentPath}</span>
        </div>

        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="p-8 text-center text-gray-400">
              <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
              Loading...
            </div>
          ) : !browseData?.contents?.length ? (
            <div className="p-8 text-center text-gray-400">Empty directory</div>
          ) : (
            <div className="divide-y divide-gray-700">
              {browseData.contents
                .sort((a: GitHubFileInfo, b: GitHubFileInfo) => {
                  if (a.type === 'dir' && b.type !== 'dir') return -1
                  if (a.type !== 'dir' && b.type === 'dir') return 1
                  return a.name.localeCompare(b.name)
                })
                .map((item: GitHubFileInfo) => (
                  <button
                    key={item.path}
                    onClick={() => {
                      if (item.type === 'dir') {
                        navigateTo(item.path)
                      }
                    }}
                    className={cn(
                      'w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-gray-700/50 transition-colors',
                      item.type === 'dir' && 'cursor-pointer'
                    )}
                  >
                    {item.type === 'dir' ? (
                      <Folder className="h-5 w-5 text-blue-400" />
                    ) : (
                      <FileText className="h-5 w-5 text-gray-400" />
                    )}
                    <span className="flex-1 text-gray-100">{item.name}</span>
                    {item.type === 'dir' && <ChevronRight className="h-4 w-4 text-gray-400" />}
                    {item.type === 'file' && item.size && (
                      <span className="text-xs text-gray-400">{formatFileSize(item.size)}</span>
                    )}
                  </button>
                ))}
            </div>
          )}
        </div>

        <div className="p-4 border-t border-gray-700 bg-gray-700/30">
          <p className="text-xs text-gray-400 text-center">
            Browse repository contents. Use Import from GitHub in Content Library to import files.
          </p>
        </div>
    </Modal>
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
