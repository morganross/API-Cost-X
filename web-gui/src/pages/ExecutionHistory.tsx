import { Link } from 'react-router-dom'
import { Search, Filter, Clock, CheckCircle2, XCircle, ChevronRight, AlertCircle, Loader2, Trash2, Pause, Play } from 'lucide-react'
import { useState, useEffect } from 'react'
import { runsApi, type Run } from '../api'
import { notify } from '@/stores/notifications'

// Normalize ISO timestamp to UTC by appending Z if missing
function normalizeUtcTime(t: string): string {
  return t && !t.endsWith('Z') ? t + 'Z' : t
}

function formatDuration(start: string, end?: string): string {
  const startTime = new Date(normalizeUtcTime(start))
  const endTime = end ? new Date(normalizeUtcTime(end)) : new Date()
  const diff = endTime.getTime() - startTime.getTime()
  if (diff < 0) return '0m' // Guard against negative durations
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(minutes / 60)
  if (hours > 0) return `${hours}h ${minutes % 60}m`
  return `${minutes}m`
}

function formatEstimateCalls(total: number | undefined): string {
  if (!total || total <= 0) return '—'
  return total.toLocaleString()
}

export default function ExecutionHistory() {
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [runs, setRuns] = useState<Run[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [bulkDeleteTarget, setBulkDeleteTarget] = useState<null | 'completedFailed' | 'failed'>(null)
  const [resuming, setResuming] = useState<string | null>(null)

  // Fetch runs from API; only poll when active runs exist
  useEffect(() => {
    let intervalId: number | null = null

    const fetchRuns = async (showLoader = false) => {
      if (showLoader) setLoading(true)
      setError(null)
      try {
        const data = await runsApi.list({ limit: 100 })
        setRuns(data)
        // Only keep polling if there are runs in an active state
        const hasActive = data.some((r: Run) => r.status === 'pending' || r.status === 'running')
        if (hasActive && !intervalId) {
          intervalId = window.setInterval(() => fetchRuns(false), 10_000)
        } else if (!hasActive && intervalId) {
          clearInterval(intervalId)
          intervalId = null
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load runs')
      } finally {
        if (showLoader) setLoading(false)
      }
    }

    fetchRuns(true)

    return () => {
      if (intervalId) {
        clearInterval(intervalId)
      }
    }
  }, [])

  const handleDeleteClick = (e: React.MouseEvent, runId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setDeleteConfirm(runId)
  }

  const handleDeleteConfirm = async () => {
    if (!deleteConfirm) return

    setDeleting(deleteConfirm)
    try {
      await runsApi.delete(deleteConfirm)
      // Only update UI after confirmed deletion - refetch to ensure consistency
      const updatedRuns = await runsApi.list({ limit: 100 })
      setRuns(updatedRuns)
      notify.success('Run deleted successfully')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete run'
      setError(message)
      notify.error(message)
      // Refetch to ensure UI matches actual state
      try {
        const freshRuns = await runsApi.list({ limit: 100 })
        setRuns(freshRuns)
      } catch { /* ignore refetch errors */ }
    } finally {
      setDeleting(null)
      setDeleteConfirm(null)
    }
  }

  const handleDeleteCancel = () => {
    setDeleteConfirm(null)
  }

  const handleResumeClick = async (e: React.MouseEvent, runId: string) => {
    e.preventDefault()
    e.stopPropagation()
    if (resuming) return
    setResuming(runId)
    try {
      await runsApi.resume(runId)
      const updatedRuns = await runsApi.list({ limit: 100 })
      setRuns(updatedRuns)
      notify.success('Run resumed')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to resume run'
      notify.error(message)
    } finally {
      setResuming(null)
    }
  }

  const handleBulkDelete = async (target: 'completedFailed' | 'failed') => {
    if (bulkDeleteTarget) return
    if (deleting) return

    const statuses = target === 'failed' ? ['failed', 'cancelled'] : ['completed', 'failed', 'cancelled']
    const runIdsToDelete = runs
      .filter((r) => statuses.includes(r.status))
      .map((r) => r.id)

    if (runIdsToDelete.length === 0) {
      notify.info(
        target === 'failed'
          ? 'No failed runs to delete'
          : 'No completed or failed runs to delete'
      )
      return
    }

    const ok = window.confirm(
      target === 'failed'
        ? `Delete ${runIdsToDelete.length} failed run(s)? This cannot be undone.`
        : `Delete ${runIdsToDelete.length} completed/failed run(s)? This cannot be undone.`
    )
    if (!ok) return

    setBulkDeleteTarget(target)
    setError(null)

    try {
      const apiTarget = target === 'failed' ? 'failed' : 'completed_failed'
      const result = await runsApi.bulkDelete(apiTarget)
      const deletedCount = result?.deleted ?? 0

      if (deletedCount > 0) {
        // Refetch to get actual state from server instead of optimistic update
        const updatedRuns = await runsApi.list({ limit: 100 })
        setRuns(updatedRuns)
        const label = target === 'failed' ? 'failed run(s)' : 'completed/failed run(s)'
        notify.success(`Deleted ${deletedCount} ${label}`)
      } else {
        notify.info('No runs were deleted (they may have been removed already)')
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Bulk delete failed'
      setError(message)
      notify.error(message)
      // Refetch to ensure UI matches actual state after error
      try {
        const freshRuns = await runsApi.list({ limit: 100 })
        setRuns(freshRuns)
      } catch { /* ignore refetch errors */ }
    } finally {
      setBulkDeleteTarget(null)
    }
  }

  const filteredRuns = runs.filter(run => {
    if (statusFilter !== 'all' && run.status !== statusFilter) return false
    if (searchQuery) {
      const title = (run.title || run.name || '').toLowerCase()
      if (!title.includes(searchQuery.toLowerCase())) return false
    }
    return true
  })

  return (
    <div className="text-gray-100">
      <header className="sticky top-0 z-20 mb-6 max-[782px]:static max-[782px]:z-auto">
        <div className="border-b border-gray-700 bg-gray-800">
          <div className="max-w-7xl mx-auto px-4 py-4 space-y-4">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <h1 className="text-2xl font-bold">Execution History</h1>
              <p className="text-sm text-gray-400">View past preset executions and results</p>
            </div>

            <div className="flex flex-col gap-3 sm:flex-row">
              <button
                onClick={() => handleBulkDelete('failed')}
                disabled={loading || !!deleting || !!bulkDeleteTarget}
                className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-red-500 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
                title="Delete all failed runs"
              >
                {bulkDeleteTarget === 'failed' ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Deleting…
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete Failed
                  </>
                )}
              </button>
              <button
                onClick={() => handleBulkDelete('completedFailed')}
                disabled={loading || !!deleting || !!bulkDeleteTarget}
                className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-700 disabled:text-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
                title="Delete all completed and failed runs"
              >
                {bulkDeleteTarget === 'completedFailed' ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Deleting…
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete Completed/Failed
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Filters */}
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
              <input
                type="text"
                placeholder="Search by preset name..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 border border-gray-700 rounded-lg bg-gray-800 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="relative">
              <Filter className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full pl-10 pr-8 py-2 border border-gray-700 rounded-lg bg-gray-800 text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 appearance-none cursor-pointer sm:w-auto"
              >
                <option value="all">All Status</option>
                <option value="running">Running</option>
                <option value="paused">Paused</option>
                <option value="completed">Completed</option>
                <option value="failed">Failed</option>
              </select>
            </div>
          </div>
        </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6">

        {/* Executions List */}
        {loading ? (
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-12 text-center">
            <Loader2 className="h-12 w-12 mx-auto mb-3 text-blue-500 animate-spin" />
            <p className="text-gray-400">Loading runs...</p>
          </div>
        ) : error ? (
          <div className="bg-gray-800 border border-red-700 rounded-lg p-12 text-center">
            <AlertCircle className="h-12 w-12 mx-auto mb-3 text-red-500" />
            <p className="text-red-400">{error}</p>
          </div>
        ) : filteredRuns.length === 0 ? (
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-12 text-center">
            <Clock className="h-12 w-12 mx-auto mb-3 text-gray-600" />
            <p className="text-gray-400">No executions found</p>
            <p className="text-sm text-gray-500 mt-1">
              Go to the Build Preset page to start a new run.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredRuns.map((run) => {
              const startedAt = run.started_at || run.created_at || run.createdAt || ''
              const completedAt = run.completed_at || run.completedAt
              const docCount = run.config?.documents?.length || run.documentCount || 0
              const modelCount = run.config?.models?.length || run.modelCount || 0
              const totalTasks = run.progress?.total_tasks || 1
              const completedTasks = run.progress?.completed_tasks || 0
              const title = run.title || run.name || `Run ${run.id.slice(0, 8)}`

              return (
                <Link
                  key={run.id}
                  to={`/execute/${run.id}`}
                  className="block overflow-hidden rounded-lg border border-gray-700 bg-gray-800 p-4 transition-colors hover:border-gray-600 hover:bg-gray-750"
                >
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <div className="flex min-w-0 items-start gap-4">
                      {/* Status Icon */}
                      {run.status === 'completed' && (
                        <CheckCircle2 className="w-8 h-8 text-green-500" />
                      )}
                      {(run.status === 'failed' || run.status === 'cancelled') && (
                        <XCircle className="w-8 h-8 text-red-500" />
                      )}
                      {(run.status === 'running' || run.status === 'pending') && (
                        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                      )}
                      {run.status === 'paused' && (
                        <Pause className="w-8 h-8 text-orange-500" />
                      )}

                      {/* Info */}
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-base font-medium sm:text-lg">{title}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-gray-400">
                          <span className="font-mono text-xs text-gray-500" title={run.id}>
                            Run {run.id.slice(0, 8)}
                          </span>
                          <span>•</span>
                          {startedAt && (
                            <>
                              <span>{new Date(normalizeUtcTime(startedAt)).toLocaleDateString()} {new Date(normalizeUtcTime(startedAt)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                              <span>•</span>
                              <span>{formatDuration(startedAt, completedAt)}</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex w-full flex-wrap items-start gap-4 sm:gap-6 xl:w-auto xl:flex-nowrap xl:items-center xl:justify-end">
                      {/* Stats */}
                      <div className="min-w-[7rem] flex-1 text-left sm:flex-none sm:text-right">
                        <div className="text-sm text-gray-400">Documents × Models</div>
                        <div className="font-mono">{docCount} × {modelCount}</div>
                      </div>
                      <div className="min-w-[7rem] flex-1 text-left sm:flex-none sm:text-right">
                        <div className="text-sm text-gray-400">Est. Calls</div>
                        <div className="font-mono">{formatEstimateCalls(run.run_estimate?.total)}</div>
                      </div>
                      <div className="min-w-[6rem] flex-1 text-left sm:flex-none sm:text-right">
                        <div className="text-sm text-gray-400">Progress</div>
                        <div className="font-mono">{completedTasks}/{totalTasks}</div>
                      </div>
                      <div className="ml-auto flex items-center gap-2 self-end xl:self-auto">
                        {/* Resume button for paused/failed runs */}
                        {(run.status === 'paused' || run.status === 'failed') && (
                          <button
                            onClick={(e) => handleResumeClick(e, run.id)}
                            disabled={!!resuming}
                            className="p-2 text-green-400 hover:text-green-300 hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50"
                            title="Resume run"
                          >
                            {resuming === run.id
                              ? <Loader2 className="w-5 h-5 animate-spin" />
                              : <Play className="w-5 h-5" />
                            }
                          </button>
                        )}
                        {/* Delete button - only show for non-running runs */}
                        {run.status !== 'running' && run.status !== 'pending' && (
                          <button
                            onClick={(e) => handleDeleteClick(e, run.id)}
                            className="p-2 text-gray-400 hover:text-red-500 hover:bg-gray-700 rounded-lg transition-colors"
                            title="Delete run"
                          >
                            <Trash2 className="w-5 h-5" />
                          </button>
                        )}
                        <ChevronRight className="w-5 h-5 flex-shrink-0 text-gray-500" />
                      </div>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  <div className="mt-3 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className={`h-full transition-all ${
                        run.status === 'completed' ? 'bg-green-500' :
                        run.status === 'failed' || run.status === 'cancelled' ? 'bg-red-500' :
                        run.status === 'paused' ? 'bg-orange-500' :
                        'bg-blue-500'
                      }`}
                      style={{ width: `${totalTasks > 0 ? (completedTasks / totalTasks) * 100 : 0}%` }}
                    />
                  </div>
                </Link>
              )
            })}
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-white mb-2">Delete Run?</h3>
            <p className="text-gray-400 mb-6">
              This will permanently delete this run and all its results. This action cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={handleDeleteCancel}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition-colors"
                disabled={!!deleting}
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors flex items-center gap-2"
                disabled={!!deleting}
              >
                {deleting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Delete
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
