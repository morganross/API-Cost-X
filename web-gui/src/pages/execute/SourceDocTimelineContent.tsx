import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, CheckCircle, Clock, ListTree } from 'lucide-react'
import { apiClient } from '../../api/client'

interface SourceDocTimelineContentProps {
  runId: string
  sourceDocId?: string
}

interface TimelineEvent {
  phase: string
  event_type: string
  description?: string
  model?: string | null
  success?: boolean
  duration_seconds?: number | null
}

interface LlmCallInfo {
  attempt_id: string
  phase: string
  provider: string
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  latency_ms: number
  status: string
}

interface TimelineSection {
  _meta?: {
    event_count?: number
    total_count?: number
    returned_count?: number
    offset?: number
    limit?: number
    has_more?: boolean
    started_at?: string | null
    completed_at?: string | null
    duration_seconds?: number | null
  }
  events: TimelineEvent[]
}

interface LlmCallsSection {
  _meta?: {
    call_count?: number
    returned_count?: number
    offset?: number
    limit?: number
    has_more?: boolean
    total_tokens?: number
  }
  calls: LlmCallInfo[]
}

interface TimelinePayload {
  status: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  timeline?: TimelineSection | null
}

interface LlmCallsPayload {
  status: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  llm_calls?: LlmCallsSection | null
}

const TIMELINE_PAGE_SIZE = 100
const LLM_CALLS_PAGE_SIZE = 50

const fetchTimelinePage = async (
  runId: string,
  sourceDocId: string | undefined,
  offset: number
) =>
  apiClient.get<TimelinePayload>(`/runs/${runId}/sections/timeline`, {
    limit: TIMELINE_PAGE_SIZE,
    offset,
    ...(sourceDocId ? { source_doc_id: sourceDocId } : {}),
  })

const fetchLlmCallsPage = async (
  runId: string,
  sourceDocId: string | undefined,
  offset: number
) =>
  apiClient.get<LlmCallsPayload>(`/runs/${runId}/sections/llm-calls`, {
    limit: LLM_CALLS_PAGE_SIZE,
    offset,
    ...(sourceDocId ? { source_doc_id: sourceDocId } : {}),
  })

const formatDuration = (seconds?: number | null) => {
  if (seconds == null) return null
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const secs = (seconds % 60).toFixed(0)
  return `${mins}m ${secs}s`
}

const phaseColors: Record<string, { bg: string; border: string; text: string }> = {
  initialization: { bg: '#1e3a5f', border: '#3b82f6', text: '#93c5fd' },
  generation: { bg: '#422006', border: '#f59e0b', text: '#fcd34d' },
  single_eval: { bg: '#064e3b', border: '#10b981', text: '#6ee7b7' },
  evaluation: { bg: '#064e3b', border: '#10b981', text: '#6ee7b7' },
  pairwise: { bg: '#3b0764', border: '#a855f7', text: '#d8b4fe' },
  combination: { bg: '#500724', border: '#ec4899', text: '#f9a8d4' },
  combine: { bg: '#500724', border: '#ec4899', text: '#f9a8d4' },
  post_combine_eval: { bg: '#083344', border: '#06b6d4', text: '#67e8f9' },
  completion: { bg: '#083344', border: '#06b6d4', text: '#67e8f9' },
}

const getPhaseColor = (phase: string) => phaseColors[phase.toLowerCase()] || phaseColors.initialization

export default function SourceDocTimelineContent({ runId, sourceDocId }: SourceDocTimelineContentProps) {
  const [timelineData, setTimelineData] = useState<TimelinePayload | null>(null)
  const [llmCallsData, setLlmCallsData] = useState<LlmCallsPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [timelineError, setTimelineError] = useState<string | null>(null)
  const [llmCallsError, setLlmCallsError] = useState<string | null>(null)
  const [timelineLoadingMore, setTimelineLoadingMore] = useState(false)
  const [llmCallsLoadingMore, setLlmCallsLoadingMore] = useState(false)
  const [timelineLoadError, setTimelineLoadError] = useState<string | null>(null)
  const [llmCallsLoadError, setLlmCallsLoadError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId) return

    let cancelled = false
    setLoading(true)
    setTimelineError(null)
    setLlmCallsError(null)
    setTimelineLoadError(null)
    setLlmCallsLoadError(null)
    setTimelineData(null)
    setLlmCallsData(null)

    Promise.allSettled([
      fetchTimelinePage(runId, sourceDocId, 0),
      fetchLlmCallsPage(runId, sourceDocId, 0),
    ])
      .then(([timelineResult, llmCallsResult]) => {
        if (cancelled) return

        if (timelineResult.status === 'fulfilled') {
          setTimelineData(timelineResult.value)
        } else {
          setTimelineError(
            timelineResult.reason instanceof Error ? timelineResult.reason.message : String(timelineResult.reason)
          )
        }

        if (llmCallsResult.status === 'fulfilled') {
          setLlmCallsData(llmCallsResult.value)
        } else {
          setLlmCallsError(
            llmCallsResult.reason instanceof Error ? llmCallsResult.reason.message : String(llmCallsResult.reason)
          )
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [runId, sourceDocId])

  const events = useMemo(() => timelineData?.timeline?.events || [], [timelineData])
  const llmCalls = useMemo(() => llmCallsData?.llm_calls?.calls || [], [llmCallsData])
  const timelineMeta = timelineData?.timeline?._meta
  const llmCallsMeta = llmCallsData?.llm_calls?._meta

  const handleLoadMoreTimeline = async () => {
    if (!runId || timelineLoadingMore || !timelineMeta?.has_more) return
    setTimelineLoadingMore(true)
    setTimelineLoadError(null)
    try {
      const payload = await fetchTimelinePage(runId, sourceDocId, events.length)
      setTimelineData((current) => {
        if (!current) return payload
        return {
          ...payload,
          timeline: {
            ...(payload.timeline || {}),
            events: [...(current.timeline?.events || []), ...(payload.timeline?.events || [])],
          },
        }
      })
    } catch (reason: unknown) {
      setTimelineLoadError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setTimelineLoadingMore(false)
    }
  }

  const handleLoadMoreLlmCalls = async () => {
    if (!runId || llmCallsLoadingMore || !llmCallsMeta?.has_more) return
    setLlmCallsLoadingMore(true)
    setLlmCallsLoadError(null)
    try {
      const payload = await fetchLlmCallsPage(runId, sourceDocId, llmCalls.length)
      setLlmCallsData((current) => {
        if (!current) return payload
        return {
          ...payload,
          llm_calls: {
            ...(payload.llm_calls || {}),
            calls: [...(current.llm_calls?.calls || []), ...(payload.llm_calls?.calls || [])],
          },
        }
      })
    } catch (reason: unknown) {
      setLlmCallsLoadError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLlmCallsLoadingMore(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '32px', textAlign: 'center', color: '#6b7280' }}>
        <Clock size={32} style={{ opacity: 0.4, marginBottom: '8px' }} />
        <p style={{ fontSize: '13px' }}>Loading timeline…</p>
      </div>
    )
  }

  if (timelineError || !timelineData) {
    return (
      <div style={{ padding: '32px', textAlign: 'center', color: '#ef4444', fontSize: '13px' }}>
        Failed to load timeline: {timelineError}
      </div>
    )
  }

  const totalTokens = llmCallsMeta?.total_tokens ?? llmCalls.reduce((sum, event) => sum + (event.total_tokens ?? 0), 0)
  const callCount = llmCallsMeta?.call_count ?? llmCalls.length

  return (
    <div style={{ color: '#e5e7eb' }}>
      {llmCallsError && (
        <div
          style={{
            marginBottom: '12px',
            padding: '10px 12px',
            borderRadius: '8px',
            backgroundColor: 'rgba(120, 53, 15, 0.25)',
            border: '1px solid rgba(251, 191, 36, 0.35)',
            color: '#fde68a',
            fontSize: '12px',
          }}
        >
          Detailed LLM call data could not be loaded for this section. The timeline below is still showing the saved execution events that were available.
        </div>
      )}
      <div
        style={{
          display: 'flex',
          gap: '20px',
          marginBottom: '18px',
          padding: '12px 16px',
          backgroundColor: '#0f172a',
          borderRadius: '8px',
          flexWrap: 'wrap',
          border: '1px solid #1e293b',
        }}
      >
        {timelineData.started_at && (
          <div>
            <div style={{ color: '#6b7280', fontSize: '11px', marginBottom: '2px' }}>Started</div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{new Date(timelineData.started_at).toLocaleTimeString()}</div>
          </div>
        )}
        {timelineData.completed_at && (
          <div>
            <div style={{ color: '#6b7280', fontSize: '11px', marginBottom: '2px' }}>Completed</div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{new Date(timelineData.completed_at).toLocaleTimeString()}</div>
          </div>
        )}
        {timelineData.duration_seconds != null && (
          <div>
            <div style={{ color: '#6b7280', fontSize: '11px', marginBottom: '2px' }}>Duration</div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{formatDuration(timelineData.duration_seconds)}</div>
          </div>
        )}
        {totalTokens > 0 && (
          <div>
            <div style={{ color: '#6b7280', fontSize: '11px', marginBottom: '2px' }}>Total Tokens</div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{totalTokens.toLocaleString()}</div>
          </div>
        )}
        {callCount > 0 && (
          <div>
            <div style={{ color: '#6b7280', fontSize: '11px', marginBottom: '2px' }}>LLM Calls</div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{callCount}</div>
          </div>
        )}
      </div>

      <h4 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px', color: '#10b981' }}>
        <Clock size={15} /> Execution Timeline
      </h4>

      {events.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#4b5563', padding: '28px 0', fontSize: '13px' }}>
          <Clock size={28} style={{ opacity: 0.4, display: 'block', margin: '0 auto 8px' }} />
          No timeline events recorded.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginBottom: '24px' }}>
          {events.map((event, index) => {
            const colors = getPhaseColor(event.phase)
            return (
              <div
                key={`${event.phase}-${event.event_type}-${index}`}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '10px',
                  padding: '9px 12px',
                  borderRadius: '6px',
                  backgroundColor: colors.bg,
                  borderLeft: `3px solid ${colors.border}`,
                }}
              >
                <div
                  style={{
                    flexShrink: 0,
                    width: '22px',
                    height: '22px',
                    borderRadius: '50%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: colors.border,
                    color: 'white',
                    fontWeight: 700,
                    fontSize: '10px',
                  }}
                >
                  {index + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: '11px', color: colors.text, textTransform: 'uppercase' }}>
                      {event.phase}
                    </span>
                    <span style={{ fontSize: '11px', padding: '1px 6px', borderRadius: '4px', backgroundColor: 'rgba(0,0,0,0.25)', color: '#d1d5db' }}>
                      {event.event_type}
                    </span>
                    {event.success === false ? (
                      <AlertCircle size={13} style={{ color: '#ef4444' }} />
                    ) : (
                      <CheckCircle size={13} style={{ color: '#22c55e' }} />
                    )}
                  </div>
                  {event.description && (
                    <p style={{ fontSize: '12px', color: '#d1d5db', margin: '3px 0 0' }}>{event.description}</p>
                  )}
                </div>
                <div style={{ flexShrink: 0, textAlign: 'right', fontSize: '11px', color: '#9ca3af', whiteSpace: 'nowrap' }}>
                  {event.model && <div style={{ fontFamily: 'monospace' }}>{event.model}</div>}
                  {event.duration_seconds != null && <div>{event.duration_seconds.toFixed(2)}s</div>}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {timelineMeta?.has_more && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
          <button
            onClick={handleLoadMoreTimeline}
            disabled={timelineLoadingMore}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid #334155',
              backgroundColor: timelineLoadingMore ? '#111827' : '#0f172a',
              color: '#e5e7eb',
              cursor: timelineLoadingMore ? 'not-allowed' : 'pointer',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            {timelineLoadingMore ? 'Loading more…' : 'Load More Timeline Events'}
          </button>
          <span style={{ fontSize: '11px', color: '#9ca3af' }}>
            Showing {events.length} of {timelineMeta.total_count ?? events.length}
          </span>
        </div>
      )}
      {timelineLoadError && (
        <div style={{ marginTop: '-16px', marginBottom: '24px', color: '#fca5a5', fontSize: '12px' }}>
          Failed to load more timeline events: {timelineLoadError}
        </div>
      )}

      {llmCalls.length > 0 && (
        <div style={{ marginBottom: '24px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 600, marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '8px', color: '#f59e0b' }}>
            <ListTree size={15} /> Individual LLM Calls ({callCount})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  {['#', 'Phase', 'Provider', 'Model', 'Status', 'Latency', 'Tokens In', 'Tokens Out', 'Total Tokens'].map((heading) => (
                    <th
                      key={heading}
                      style={{
                        padding: '9px 10px',
                        fontSize: '11px',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                        whiteSpace: 'nowrap',
                        color: '#9ca3af',
                        backgroundColor: '#1e2533',
                        borderBottom: '2px solid #374151',
                        textAlign: heading === 'Phase' || heading === 'Provider' || heading === 'Model' || heading === 'Status' ? 'left' : 'right',
                      }}
                    >
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {llmCalls.map((event, index) => {
                  const colors = getPhaseColor(event.phase)
                  const status = event.status === 'success' ? 'ok' : event.status
                  return (
                    <tr key={event.attempt_id} style={{ borderBottom: '1px solid #1e293b', backgroundColor: index % 2 === 0 ? '#0f172a' : '#111827' }}>
                      <td style={{ padding: '7px 10px', textAlign: 'right', color: '#6b7280', fontFamily: 'monospace', fontSize: '11px' }}>
                        {index + 1}
                      </td>
                      <td style={{ padding: '7px 10px' }}>
                        <span style={{ padding: '2px 6px', borderRadius: '4px', fontSize: '10px', fontWeight: 700, backgroundColor: colors.bg, color: colors.text, border: `1px solid ${colors.border}44`, textTransform: 'uppercase', whiteSpace: 'nowrap' }}>
                          {event.phase}
                        </span>
                      </td>
                      <td style={{ padding: '7px 10px', fontSize: '11px', color: '#d1d5db', fontFamily: 'monospace' }}>
                        {event.provider}
                      </td>
                      <td style={{ padding: '7px 10px', fontSize: '11px', color: '#e5e7eb', fontFamily: 'monospace' }}>
                        {event.model}
                      </td>
                      <td style={{ padding: '7px 10px', whiteSpace: 'nowrap', color: event.status === 'success' ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                        {status}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'right', fontFamily: 'monospace', color: '#fcd34d' }}>
                        {event.latency_ms >= 1000 ? `${(event.latency_ms / 1000).toFixed(1)}s` : `${event.latency_ms}ms`}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'right', fontFamily: 'monospace', color: '#d1d5db' }}>
                        {event.input_tokens.toLocaleString()}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'right', fontFamily: 'monospace', color: '#d1d5db' }}>
                        {event.output_tokens.toLocaleString()}
                      </td>
                      <td style={{ padding: '7px 10px', textAlign: 'right', fontFamily: 'monospace', color: '#d1d5db' }}>
                        {event.total_tokens.toLocaleString()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {llmCallsMeta?.has_more && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '12px' }}>
              <button
                onClick={handleLoadMoreLlmCalls}
                disabled={llmCallsLoadingMore}
                style={{
                  padding: '8px 12px',
                  borderRadius: '6px',
                  border: '1px solid #334155',
                  backgroundColor: llmCallsLoadingMore ? '#111827' : '#0f172a',
                  color: '#e5e7eb',
                  cursor: llmCallsLoadingMore ? 'not-allowed' : 'pointer',
                  fontSize: '12px',
                  fontWeight: 600,
                }}
              >
                {llmCallsLoadingMore ? 'Loading more…' : 'Load More LLM Calls'}
              </button>
              <span style={{ fontSize: '11px', color: '#9ca3af' }}>
                Showing {llmCalls.length} of {callCount}
              </span>
            </div>
          )}
          {llmCallsLoadError && (
            <div style={{ marginTop: '8px', color: '#fca5a5', fontSize: '12px' }}>
              Failed to load more LLM calls: {llmCallsLoadError}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
