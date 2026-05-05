import { useState, useEffect, useRef, useCallback } from 'react'
import { ChevronDown, ChevronUp, Terminal, RefreshCw, Download, Maximize2, X, ChevronRight } from 'lucide-react'
import { apiClient } from '../../api'

interface LogViewerProps {
  runId: string | null
  isRunning: boolean
  initiallyExpanded?: boolean
  allowFullscreen?: boolean
  title?: string
  className?: string
  bodyHeightClass?: string
}

interface LogEntry {
  id: number
  timestamp: string
  classification: "EVENT" | "DETAIL"
  source: "apicostx" | "fpf" | "gptr" | "dr"
  level: string
  event_type: string | null
  message: string
  payload: string | null
}

interface LogResponse {
  run_id: string
  total: number
  offset: number
  limit: number
  save_run_logs?: boolean
  entries: LogEntry[]
}

interface LogCountResponse {
  run_id: string
  total: number
  save_run_logs?: boolean
}

const SOURCE_COLORS: Record<string, string> = {
  apicostx: 'bg-emerald-700 text-emerald-100',
  fpf: 'bg-blue-700 text-blue-100',
  gptr: 'bg-purple-700 text-purple-100',
  dr: 'bg-orange-700 text-orange-100',
}

const LEVEL_COLORS: Record<string, string> = {
  ERROR: 'bg-red-700 text-red-100',
  WARNING: 'bg-amber-700 text-amber-100',
  INFO: 'bg-green-700 text-green-100',
  DEBUG: 'bg-gray-600 text-gray-200',
}

function PayloadBlock({ payload }: { payload: string }) {
  const [open, setOpen] = useState(false)
  let formatted = payload
  try {
    formatted = JSON.stringify(JSON.parse(payload), null, 2)
  } catch { /* not JSON, show raw */ }

  return (
    <div className="mt-1">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1"
      >
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? 'rotate-90' : ''}`} />
        payload
      </button>
      {open && (
        <pre className="mt-1 p-2 bg-gray-950 rounded text-xs whitespace-pre-wrap break-words max-h-64 overflow-y-auto">
          {formatted}
        </pre>
      )}
    </div>
  )
}

export default function LogViewer({
  runId,
  isRunning,
  initiallyExpanded = false,
  allowFullscreen = true,
  title = 'Execution Logs',
  className = '',
  bodyHeightClass = 'h-96',
}: LogViewerProps) {
  const [expanded, setExpanded] = useState(initiallyExpanded)
  const [fullscreen, setFullscreen] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [saveRunLogs, setSaveRunLogs] = useState<boolean | null>(null)
  const [isPolling, setIsPolling] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const knownCountRef = useRef(0)

  const classification = showDetails ? 'all' : 'event'

  // Scroll to bottom when new entries arrive
  useEffect(() => {
    if (expanded && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [entries, expanded])

  // Fetch all entries for current classification
  const fetchEntries = useCallback(async () => {
    if (!runId) return
    try {
      const resp = await apiClient.get<LogResponse>(`/runs/${runId}/logs`, {
        classification,
        offset: 0,
        limit: 5000,
      })
      setEntries(resp.entries)
      setTotal(resp.total)
      setSaveRunLogs(resp.save_run_logs ?? null)
      knownCountRef.current = resp.total
    } catch (err) {
      console.error('Failed to fetch logs:', err)
    }
  }, [runId, classification])

  // Lightweight poll: check count, only fetch if changed
  const pollCount = useCallback(async () => {
    if (!runId) return
    try {
      const resp = await apiClient.get<LogCountResponse>(`/runs/${runId}/logs/count`, {
        classification,
      })
      setSaveRunLogs(resp.save_run_logs ?? null)
      if (resp.total !== knownCountRef.current) {
        await fetchEntries()
      }
    } catch (err) {
      console.error('Failed to poll log count:', err)
    }
  }, [runId, classification, fetchEntries])

  // Track previous isRunning to detect completion
  const prevIsRunningRef = useRef(isRunning)

  // Polling while running
  useEffect(() => {
    if (isRunning && expanded) {
      setIsPolling(true)
      fetchEntries()
      pollIntervalRef.current = setInterval(pollCount, 2000)
    } else {
      setIsPolling(false)
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
      // Final fetch after run completes
      if (prevIsRunningRef.current && !isRunning && expanded && runId) {
        setTimeout(() => fetchEntries(), 500)
      }
    }
    prevIsRunningRef.current = isRunning

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [isRunning, expanded, runId, fetchEntries, pollCount])

  // Fetch when expanded or classification changes
  useEffect(() => {
    if (expanded && runId) {
      fetchEntries()
    }
  }, [expanded, runId, classification])

  useEffect(() => {
    if (!runId || expanded) {
      return
    }

    pollCount()

    if (!isRunning) {
      return
    }

    const intervalId = setInterval(() => {
      pollCount()
    }, 2000)

    return () => {
      clearInterval(intervalId)
    }
  }, [expanded, isRunning, pollCount, runId, classification])

  // Reset when run changes
  useEffect(() => {
    setEntries([])
    setTotal(0)
    setSaveRunLogs(null)
    knownCountRef.current = 0
  }, [runId])

  useEffect(() => {
    setExpanded(initiallyExpanded)
  }, [runId])

  useEffect(() => {
    if (initiallyExpanded) {
      setExpanded(true)
    }
  }, [initiallyExpanded])

  const handleDownloadJSON = () => {
    const blob = new Blob([JSON.stringify(entries, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `run_${runId}_logs.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleDownloadText = () => {
    const text = entries
      .map(e => `${e.timestamp} [${e.source}] [${e.level}] ${e.message}`)
      .join('\n')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `run_${runId}_logs.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  if (!runId) {
    return null
  }

  const containerClass = fullscreen
    ? 'fixed inset-0 z-50 bg-gray-900 text-gray-100 flex flex-col'
    : `mt-4 border rounded-lg bg-gray-900 text-gray-100 ${className}`.trim()

  const emptyMessage =
    saveRunLogs === false
      ? 'Saved run logging was turned off for this run, so there are no stored log entries to show here.'
      : 'No log entries yet...'

  return (
    <div className={containerClass}>
      {/* Header */}
      <button
        onClick={() => !fullscreen && setExpanded(!expanded)}
        className={`w-full px-4 py-3 flex items-center justify-between hover:bg-gray-800 transition-colors ${fullscreen ? '' : 'rounded-t-lg'}`}
      >
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <span className="font-medium">{title}</span>
          {isPolling && (
            <RefreshCw className="w-3 h-3 text-blue-400 animate-spin" />
          )}
          <span className="text-xs text-gray-500 ml-2">
            {total} entries
          </span>
        </div>
        <div className="flex items-center gap-2">
          {fullscreen ? (
            <button onClick={() => setFullscreen(false)} className="p-1 hover:bg-gray-700 rounded">
              <X className="w-4 h-4" />
            </button>
          ) : expanded ? (
            <>
              {allowFullscreen && (
                <button onClick={(e) => { e.stopPropagation(); setFullscreen(true); }} className="p-1 hover:bg-gray-700 rounded" title="Fullscreen">
                  <Maximize2 className="w-4 h-4" />
                </button>
              )}
              <ChevronDown className="w-4 h-4" />
            </>
          ) : (
            <ChevronUp className="w-4 h-4" />
          )}
        </div>
      </button>

      {/* Content */}
      {(expanded || fullscreen) && (
        <div className={`border-t border-gray-700 flex flex-col ${fullscreen ? 'flex-1 overflow-hidden' : ''}`}>
          {/* Controls bar */}
          <div className="flex items-center gap-2 px-4 py-2 bg-gray-800 border-b border-gray-700">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={showDetails}
                onChange={(e) => setShowDetails(e.target.checked)}
                className="w-4 h-4 accent-purple-500"
              />
              <span className="text-purple-400 font-medium">Show Details</span>
            </label>
            <span className="text-xs text-gray-500">
              {showDetails ? 'All entries (events + verbose detail)' : 'Events only (lifecycle milestones)'}
            </span>
            <div className="flex-1" />
            <button
              onClick={handleDownloadText}
              disabled={entries.length === 0}
              className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded disabled:opacity-50 disabled:cursor-not-allowed"
              title="Download as text"
            >
              <Download className="w-4 h-4 inline mr-1" />
              TXT
            </button>
            <button
              onClick={handleDownloadJSON}
              disabled={entries.length === 0}
              className="px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded disabled:opacity-50 disabled:cursor-not-allowed"
              title="Download as JSON"
            >
              <Download className="w-4 h-4 inline mr-1" />
              JSON
            </button>
          </div>

          {/* Log entries */}
          <div className={`overflow-y-auto p-4 font-mono text-xs space-y-1 ${fullscreen ? 'flex-1' : bodyHeightClass}`}>
            {entries.length > 0 ? (
              entries.map((entry) => (
                <div key={entry.id} className="flex items-start gap-2 py-0.5">
                  <span className="text-gray-500 whitespace-nowrap shrink-0">
                    {entry.timestamp.replace('T', ' ').replace('Z', '')}
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap shrink-0 ${SOURCE_COLORS[entry.source] || 'bg-gray-600 text-gray-200'}`}>
                    {entry.source}
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap shrink-0 ${LEVEL_COLORS[entry.level] || 'bg-gray-600 text-gray-200'}`}>
                    {entry.level}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="whitespace-pre-wrap break-words">{entry.message}</span>
                    {entry.payload && <PayloadBlock payload={entry.payload} />}
                  </div>
                </div>
              ))
            ) : (
              <span className="text-gray-500">{emptyMessage}</span>
            )}
            <div ref={logsEndRef} />
          </div>
        </div>
      )}
    </div>
  )
}
