import { useEffect, useMemo, useState } from 'react'
import { FileText, Trophy, Users, ExternalLink, X, GitMerge } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { SourceDocResult } from '../../api'

interface SourceDocPairwiseContentProps {
  sourceDocResult: SourceDocResult
  runId: string
  pairwiseEnabled?: boolean
}

interface DocViewerState {
  isOpen: boolean
  docId: string
  model: string
  content: string | null
  loading: boolean
  error: string | null
}

interface PairwiseRankingRow {
  doc_id: string
  wins: number
  losses: number
  elo: number
}

interface PairwiseComparisonRow {
  doc_id_a: string
  doc_id_b: string
  winner: string
  judge_model: string
  trial?: number
  reason: string
  score_a?: number | null
  score_b?: number | null
}

interface PairwiseSection {
  _meta?: {
    comparison_type?: 'pre_combine' | 'post_combine'
    total_count?: number
    returned_count?: number
    offset?: number
    limit?: number
    has_more?: boolean
  }
  winner_doc_id?: string | null
  rankings: PairwiseRankingRow[]
  comparisons: PairwiseComparisonRow[]
}

interface PairwisePayload {
  status: string
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  pairwise?: PairwiseSection | null
}

const PAIRWISE_PAGE_SIZE = 50

export default function SourceDocPairwiseContent({
  sourceDocResult,
  runId,
  pairwiseEnabled = false,
}: SourceDocPairwiseContentProps) {
  const initialPairwiseData = sourceDocResult.pairwise_results ?? null
  const initialPostCombinePairwiseData = sourceDocResult.post_combine_pairwise ?? null
  const [pairwisePayload, setPairwisePayload] = useState<PairwisePayload | null>(null)
  const [pairwiseLoading, setPairwiseLoading] = useState(false)
  const [pairwiseError, setPairwiseError] = useState<string | null>(null)
  const [pairwiseLoadingMore, setPairwiseLoadingMore] = useState(false)
  const [pairwiseLoadError, setPairwiseLoadError] = useState<string | null>(null)
  const [postCombinePairwisePayload, setPostCombinePairwisePayload] = useState<PairwisePayload | null>(null)
  const [postCombinePairwiseLoading, setPostCombinePairwiseLoading] = useState(false)
  const [postCombinePairwiseError, setPostCombinePairwiseError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId || !sourceDocResult.source_doc_id) return

    let cancelled = false
    setPairwiseLoading(true)
    setPairwiseError(null)
    setPairwiseLoadError(null)
    setPairwisePayload(null)

    apiClient
      .get<PairwisePayload>(`/runs/${runId}/sections/pairwise`, {
        source_doc_id: sourceDocResult.source_doc_id,
        limit: PAIRWISE_PAGE_SIZE,
      })
      .then((payload) => {
        if (!cancelled) {
          setPairwisePayload(payload)
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setPairwiseError(reason instanceof Error ? reason.message : String(reason))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPairwiseLoading(false)
        }
      })

    setPostCombinePairwiseLoading(true)
    setPostCombinePairwiseError(null)
    setPostCombinePairwisePayload(null)

    apiClient
      .get<PairwisePayload>(`/runs/${runId}/sections/pairwise`, {
        source_doc_id: sourceDocResult.source_doc_id,
        comparison_type: 'post_combine',
        limit: PAIRWISE_PAGE_SIZE,
      })
      .then((payload) => {
        if (!cancelled) {
          setPostCombinePairwisePayload(payload)
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setPostCombinePairwiseError(reason instanceof Error ? reason.message : String(reason))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setPostCombinePairwiseLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [runId, sourceDocResult.source_doc_id])

  const pairwiseData = pairwisePayload?.pairwise ?? null
  const postCombinePairwiseData = postCombinePairwisePayload?.pairwise ?? initialPostCombinePairwiseData ?? null
  const comparisons = pairwiseData?.comparisons ?? initialPairwiseData?.comparisons ?? []
  const winnerDocId = pairwiseData?.winner_doc_id ?? initialPairwiseData?.winner_doc_id ?? sourceDocResult.winner_doc_id ?? null
  const postCombineComparisons = postCombinePairwiseData?.comparisons ?? []
  const postCombineWinnerDocId = postCombinePairwiseData?.winner_doc_id ?? null

  const rankings = useMemo(
    () => [...(pairwiseData?.rankings ?? initialPairwiseData?.rankings ?? [])].sort((left, right) => (right.elo ?? 0) - (left.elo ?? 0)),
    [pairwiseData]
  )
  const postCombineRankings = useMemo(
    () => [...(postCombinePairwiseData?.rankings ?? [])].sort((left, right) => (right.elo ?? 0) - (left.elo ?? 0)),
    [postCombinePairwiseData]
  )

  const [docViewer, setDocViewer] = useState<DocViewerState>({
    isOpen: false,
    docId: '',
    model: '',
    content: null,
    loading: false,
    error: null,
  })

  const openDocViewer = async (docId: string, model: string) => {
    if (!runId) return

    setDocViewer({
      isOpen: true,
      docId,
      model,
      content: null,
      loading: true,
      error: null,
    })

    try {
      const data = await apiClient.get<{ content: string }>(`/runs/${runId}/generated/${encodeURIComponent(docId)}`)
      setDocViewer((previous) => ({
        ...previous,
        content: data.content,
        loading: false,
      }))
    } catch (error) {
      setDocViewer((previous) => ({
        ...previous,
        loading: false,
        error: error instanceof Error ? error.message : 'Failed to load document',
      }))
    }
  }

  const closeDocViewer = () => {
    setDocViewer({
      isOpen: false,
      docId: '',
      model: '',
      content: null,
      loading: false,
      error: null,
    })
  }

  const handleLoadMore = async () => {
    if (!runId || !sourceDocResult.source_doc_id || pairwiseLoadingMore || !pairwiseData?._meta?.has_more) return

    setPairwiseLoadingMore(true)
    setPairwiseLoadError(null)
    try {
      const payload = await apiClient.get<PairwisePayload>(`/runs/${runId}/sections/pairwise`, {
        source_doc_id: sourceDocResult.source_doc_id,
        ...(pairwiseData?._meta?.comparison_type ? { comparison_type: pairwiseData._meta.comparison_type } : {}),
        limit: PAIRWISE_PAGE_SIZE,
        offset: comparisons.length,
      })
      setPairwisePayload((current) => {
        if (!current) return payload
        return {
          ...payload,
          pairwise: {
            ...(payload.pairwise || { rankings: [], comparisons: [] }),
            comparisons: [
              ...(current.pairwise?.comparisons || []),
              ...(payload.pairwise?.comparisons || []),
            ],
          },
        }
      })
    } catch (reason: unknown) {
      setPairwiseLoadError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPairwiseLoadingMore(false)
    }
  }

  const getDocDisplayName = (docId: string): string => {
    if (docId.includes('combined')) {
      return 'Combined'
    }

    const parts = docId.split('.')
    if (parts.length >= 5) {
      return parts.slice(4).join('.').replace('_', ':')
    }

    return docId
  }

  const getEloForDoc = (docId: string, rankingRows: PairwiseRankingRow[]): number | undefined =>
    rankingRows.find((item) => item.doc_id === docId)?.elo

  const getNetCellStyle = (netScore: number): { bg: string; text: string } => {
    if (netScore > 0) {
      if (netScore >= 3) return { bg: '#16a34a', text: 'white' }
      if (netScore >= 2) return { bg: '#4ade80', text: '#064e3b' }
      return { bg: 'rgba(134, 239, 172, 0.25)', text: '#86efac' }
    }
    if (netScore < 0) {
      if (netScore <= -3) return { bg: '#dc2626', text: 'white' }
      if (netScore <= -2) return { bg: '#f87171', text: '#7f1d1d' }
      return { bg: 'rgba(252, 165, 165, 0.20)', text: '#fca5a5' }
    }
    return { bg: '#111827', text: '#9ca3af' }
  }

  const hasPrimaryPairwiseData = rankings.length > 0 || comparisons.length > 0
  const hasPostCombinePairwiseData = postCombineRankings.length > 0 || postCombineComparisons.length > 0

  if (pairwiseLoading && postCombinePairwiseLoading && !initialPairwiseData && !initialPostCombinePairwiseData) {
    return (
      <div style={{ textAlign: 'center', padding: '32px', color: '#9ca3af' }}>
        <Users size={48} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
        <p>Loading pairwise comparison data...</p>
      </div>
    )
  }

  if (!hasPrimaryPairwiseData && !hasPostCombinePairwiseData) {
    return (
      <div style={{ textAlign: 'center', padding: '32px', color: '#9ca3af' }}>
        <Users size={48} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
        {pairwiseError ? (
          <>
            <p>Pairwise results could not be loaded for this section.</p>
            <p style={{ fontSize: '12px', marginTop: '8px', color: '#fca5a5' }}>
              Failed to load pairwise section: {pairwiseError}
            </p>
          </>
        ) : pairwiseEnabled ? (
          <>
            <p>No pairwise comparison data available yet.</p>
            <p style={{ fontSize: '12px', marginTop: '8px' }}>
              Pairwise data will appear here once comparisons have run.
            </p>
          </>
        ) : (
          <>
            <p>Pairwise evaluation was not enabled for this preset.</p>
            <p style={{ fontSize: '12px', marginTop: '8px' }}>
              Enable pairwise comparison in the preset configuration to compare generated documents head-to-head.
            </p>
          </>
        )}
      </div>
    )
  }

  const docIds = Array.from(
    new Set([
      ...rankings.map((item) => item.doc_id),
      ...comparisons.map((item) => item.doc_id_a),
      ...comparisons.map((item) => item.doc_id_b),
    ])
  )
  const postCombineDocIds = Array.from(
    new Set([
      ...postCombineRankings.map((item) => item.doc_id),
      ...postCombineComparisons.map((item) => item.doc_id_a),
      ...postCombineComparisons.map((item) => item.doc_id_b),
    ])
  )

  return (
    <div>
      {pairwiseError && (
        <div
          style={{
            marginBottom: '12px',
            padding: '10px 12px',
            borderRadius: '8px',
            backgroundColor: 'rgba(127, 29, 29, 0.25)',
            border: '1px solid rgba(248, 113, 113, 0.35)',
            color: '#fecaca',
            fontSize: '12px',
          }}
        >
          Pairwise section reload failed. Showing the last saved pairwise summary that is still available on this page.
        </div>
      )}
      <div
        style={{
          padding: '12px 16px',
          backgroundColor: '#111827',
          borderRadius: '8px',
          marginBottom: '16px',
          fontSize: '13px',
          color: '#d1d5db',
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          border: '1px solid #374151',
          flexWrap: 'wrap',
        }}
      >
        <Trophy size={16} style={{ color: '#fbbf24' }} />
        <span>
          <strong>Pairwise results:</strong> head-to-head document comparisons.
        </span>
        {winnerDocId && (
          <span
            style={{
              marginLeft: 'auto',
              padding: '4px 12px',
              borderRadius: '16px',
              backgroundColor: '#166534',
              color: '#86efac',
              fontSize: '12px',
              fontWeight: 700,
            }}
          >
            Winner: {getDocDisplayName(winnerDocId)}
          </span>
        )}
      </div>

      {pairwiseError && (
        <div style={{ marginBottom: '12px', fontSize: '12px', color: '#fca5a5' }}>
          Failed to refresh pairwise comparisons: {pairwiseError}
        </div>
      )}

      {hasPrimaryPairwiseData && rankings.length > 0 && (
        <div style={{ overflowX: 'auto', marginBottom: '24px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Rank', 'Document', 'Wins', 'Losses', 'ELO'].map((heading) => (
                  <th
                    key={heading}
                    style={{
                      textAlign: heading === 'Document' ? 'left' : 'center',
                      padding: '10px 12px',
                      backgroundColor: '#1f2937',
                      color: '#9ca3af',
                      fontSize: '11px',
                      fontWeight: 700,
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      borderBottom: '2px solid #374151',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rankings.map((item, index) => {
                const isWinner = item.doc_id === winnerDocId
                return (
                  <tr
                    key={item.doc_id}
                    style={{
                      borderBottom: '1px solid #1f2937',
                      backgroundColor: isWinner
                        ? 'rgba(74, 222, 128, 0.06)'
                        : index % 2 === 0
                          ? '#0f172a'
                          : '#111827',
                    }}
                  >
                    <td style={{ padding: '10px 12px', textAlign: 'center', fontWeight: 700, color: '#9ca3af' }}>
                      {index + 1}
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <button
                          onClick={() => openDocViewer(item.doc_id, getDocDisplayName(item.doc_id))}
                          style={{
                            fontFamily: 'monospace',
                            fontSize: '12px',
                            color: '#60a5fa',
                            background: 'none',
                            border: 'none',
                            padding: 0,
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                          title={item.doc_id}
                        >
                          {getDocDisplayName(item.doc_id)}
                          <ExternalLink size={11} />
                        </button>
                        {isWinner && (
                          <span
                            style={{
                              fontSize: '10px',
                              padding: '1px 5px',
                              borderRadius: '3px',
                              backgroundColor: '#166534',
                              color: '#86efac',
                              fontWeight: 700,
                            }}
                          >
                            WIN
                          </span>
                        )}
                      </div>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'center', color: '#d1d5db' }}>{item.wins}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'center', color: '#d1d5db' }}>{item.losses}</td>
                    <td style={{ padding: '10px 12px', textAlign: 'center', color: '#fcd34d', fontWeight: 700 }}>
                      {item.elo != null ? item.elo.toFixed(0) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {comparisons.length > 0 && docIds.length > 1 && (
        <div style={{ marginTop: '24px', paddingTop: '16px', borderTop: '1px solid #374151' }}>
          <h4
            style={{
              color: '#60a5fa',
              fontSize: '14px',
              fontWeight: 600,
              marginBottom: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <FileText size={16} />
            Head-to-Head Comparison Matrix
          </h4>

          {(() => {
            const pairResults: Record<string, Record<string, { rowWins: number; colWins: number }>> = {}
            for (const docId of docIds) {
              pairResults[docId] = {}
              for (const otherId of docIds) {
                pairResults[docId][otherId] = { rowWins: 0, colWins: 0 }
              }
            }

            for (const comparison of comparisons) {
              if (!comparison?.doc_id_a || !comparison?.doc_id_b) continue
              if (comparison.winner === comparison.doc_id_a) {
                pairResults[comparison.doc_id_a][comparison.doc_id_b].rowWins++
                pairResults[comparison.doc_id_b][comparison.doc_id_a].colWins++
              } else if (comparison.winner === comparison.doc_id_b) {
                pairResults[comparison.doc_id_a][comparison.doc_id_b].colWins++
                pairResults[comparison.doc_id_b][comparison.doc_id_a].rowWins++
              }
            }

            return (
              <div style={{ overflowX: 'auto' }}>
                <table
                  style={{
                    borderCollapse: 'collapse',
                    backgroundColor: '#0b1220',
                    border: '1px solid #374151',
                  }}
                >
                  <thead>
                    <tr>
                      <th
                        style={{
                          padding: '8px 10px',
                          textAlign: 'left',
                          fontSize: '12px',
                          color: '#86efac',
                          borderBottom: '2px solid #16a34a',
                          minWidth: '140px',
                          backgroundColor: '#111827',
                        }}
                      >
                        Row vs Column →
                      </th>
                      {docIds.map((columnId) => {
                        const elo = getEloForDoc(columnId, rankings)
                        const isWinner = winnerDocId && columnId === winnerDocId
                        return (
                          <th
                            key={columnId}
                            title={columnId}
                            style={{
                              padding: '6px 4px',
                              textAlign: 'center',
                              fontSize: '11px',
                              color: '#fca5a5',
                              borderBottom: '2px solid #dc2626',
                              borderLeft: '1px solid #374151',
                              writingMode: 'vertical-rl',
                              transform: 'rotate(180deg)',
                              height: '150px',
                              width: '46px',
                              backgroundColor: isWinner ? 'rgba(74, 222, 128, 0.08)' : '#111827',
                            }}
                          >
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                              {getDocDisplayName(columnId)}
                              {elo !== undefined && <span style={{ color: '#fcd34d' }}>#{Math.round(elo)}</span>}
                            </span>
                          </th>
                        )
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {docIds.map((rowId) => (
                      <tr key={rowId}>
                        <td
                          title={rowId}
                          style={{
                            padding: '8px 10px',
                            fontSize: '11px',
                            color: '#d1d5db',
                            borderBottom: '1px solid #374151',
                            borderRight: '2px solid #16a34a',
                            backgroundColor: winnerDocId === rowId ? 'rgba(74, 222, 128, 0.08)' : '#111827',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {getDocDisplayName(rowId)}
                        </td>
                        {docIds.map((columnId) => {
                          if (rowId === columnId) {
                            return (
                              <td
                                key={`${rowId}-${columnId}`}
                                style={{
                                  padding: '8px',
                                  textAlign: 'center',
                                  color: '#4b5563',
                                  borderBottom: '1px solid #374151',
                                  borderLeft: '1px solid #374151',
                                  backgroundColor: '#111827',
                                  fontWeight: 700,
                                }}
                              >
                                —
                              </td>
                            )
                          }

                          const record = pairResults[rowId][columnId]
                          const net = record.rowWins - record.colWins
                          const cell = getNetCellStyle(net)

                          return (
                            <td
                              key={`${rowId}-${columnId}`}
                              style={{
                                padding: '8px',
                                textAlign: 'center',
                                color: cell.text,
                                backgroundColor: cell.bg,
                                borderBottom: '1px solid #374151',
                                borderLeft: '1px solid #374151',
                                fontFamily: 'monospace',
                                fontWeight: 700,
                              }}
                              title={`${getDocDisplayName(rowId)} vs ${getDocDisplayName(columnId)}: ${record.rowWins}-${record.colWins}`}
                            >
                              {record.rowWins}-{record.colWins}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          })()}
          {pairwiseLoadError && (
            <div style={{ marginTop: '12px', fontSize: '12px', color: '#fca5a5' }}>
              Failed to load more comparisons: {pairwiseLoadError}
            </div>
          )}
          {pairwiseData?._meta?.has_more && (
            <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'center' }}>
              <button
                onClick={handleLoadMore}
                disabled={pairwiseLoadingMore}
                style={{
                  padding: '8px 14px',
                  borderRadius: '8px',
                  border: '1px solid #374151',
                  backgroundColor: pairwiseLoadingMore ? '#111827' : '#1f2937',
                  color: pairwiseLoadingMore ? '#6b7280' : '#d1d5db',
                  cursor: pairwiseLoadingMore ? 'default' : 'pointer',
                  fontSize: '12px',
                  fontWeight: 600,
                }}
              >
                {pairwiseLoadingMore ? 'Loading...' : 'Load More Comparisons'}
              </button>
            </div>
          )}
        </div>
      )}

      {hasPostCombinePairwiseData && (
        <div style={{ marginTop: '24px', paddingTop: '16px', borderTop: '3px solid #059669' }}>
          <h4
            style={{
              color: '#10b981',
              fontSize: '16px',
              fontWeight: 600,
              marginBottom: '12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <GitMerge size={18} />
            Post-Combine Pairwise: Combined Document vs Original Winner
          </h4>

          <div
            style={{
              padding: '12px 16px',
              backgroundColor: '#064e3b',
              borderRadius: '8px',
              marginBottom: '16px',
              fontSize: '13px',
              color: '#a7f3d0',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              flexWrap: 'wrap',
              border: '1px solid #059669',
            }}
          >
            <span>
              <strong>Combined Document Comparison:</strong> after combine, the merged document is compared against the original winner.
            </span>
            {postCombineWinnerDocId && (
              <span
                style={{
                  marginLeft: 'auto',
                  padding: '4px 12px',
                  borderRadius: '16px',
                  backgroundColor: '#059669',
                  color: 'white',
                  fontSize: '12px',
                  fontWeight: 700,
                }}
              >
                Winner: {getDocDisplayName(postCombineWinnerDocId)}
              </span>
            )}
          </div>

          {postCombinePairwiseError && (
            <div style={{ marginBottom: '12px', fontSize: '12px', color: '#fca5a5' }}>
              Failed to refresh post-combine pairwise comparisons: {postCombinePairwiseError}
            </div>
          )}

          {postCombineRankings.length > 0 && (
            <div style={{ overflowX: 'auto', marginBottom: '24px' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {['Rank', 'Document', 'Wins', 'Losses', 'ELO'].map((heading) => (
                      <th
                        key={heading}
                        style={{
                          textAlign: heading === 'Document' ? 'left' : 'center',
                          padding: '10px 12px',
                          backgroundColor: '#059669',
                          color: 'white',
                          fontSize: '11px',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                          borderBottom: '2px solid #047857',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {postCombineRankings.map((item, index) => {
                    const isWinner = item.doc_id === postCombineWinnerDocId
                    const isCombined = item.doc_id.includes('combined')
                    return (
                      <tr
                        key={item.doc_id}
                        style={{
                          borderBottom: '1px solid #1f2937',
                          backgroundColor: isWinner
                            ? 'rgba(16, 185, 129, 0.14)'
                            : index % 2 === 0
                              ? '#0f172a'
                              : '#111827',
                        }}
                      >
                        <td style={{ padding: '10px 12px', textAlign: 'center', fontWeight: 700, color: '#9ca3af' }}>
                          {index + 1}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <button
                              onClick={() => openDocViewer(item.doc_id, isCombined ? 'Combined' : getDocDisplayName(item.doc_id))}
                              style={{
                                fontFamily: 'monospace',
                                fontSize: '12px',
                                color: '#60a5fa',
                                background: 'none',
                                border: 'none',
                                padding: 0,
                                cursor: 'pointer',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                              }}
                              title={item.doc_id}
                            >
                              {getDocDisplayName(item.doc_id)}
                              <ExternalLink size={11} />
                            </button>
                            {isWinner && (
                              <span
                                style={{
                                  fontSize: '10px',
                                  padding: '1px 5px',
                                  borderRadius: '3px',
                                  backgroundColor: '#059669',
                                  color: 'white',
                                  fontWeight: 700,
                                }}
                              >
                                WIN
                              </span>
                            )}
                            {isCombined && (
                              <span
                                style={{
                                  fontSize: '10px',
                                  padding: '1px 5px',
                                  borderRadius: '3px',
                                  backgroundColor: '#0f766e',
                                  color: '#ccfbf1',
                                  fontWeight: 700,
                                }}
                              >
                                COMBINED
                              </span>
                            )}
                          </div>
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'center', color: '#d1d5db' }}>{item.wins}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'center', color: '#d1d5db' }}>{item.losses}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'center', color: '#fcd34d', fontWeight: 700 }}>
                          {item.elo != null ? item.elo.toFixed(0) : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {postCombineComparisons.length > 0 && postCombineDocIds.length > 1 && (
            <div style={{ marginTop: '24px', paddingTop: '16px', borderTop: '1px solid #059669' }}>
              <h4
                style={{
                  color: '#10b981',
                  fontSize: '14px',
                  fontWeight: 600,
                  marginBottom: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}
              >
                <GitMerge size={16} />
                Head-to-Head Comparison Matrix
              </h4>

              {(() => {
                const pairResults: Record<string, Record<string, { rowWins: number; colWins: number }>> = {}
                for (const docId of postCombineDocIds) {
                  pairResults[docId] = {}
                  for (const otherId of postCombineDocIds) {
                    pairResults[docId][otherId] = { rowWins: 0, colWins: 0 }
                  }
                }

                for (const comparison of postCombineComparisons) {
                  if (!comparison?.doc_id_a || !comparison?.doc_id_b) continue
                  if (comparison.winner === comparison.doc_id_a) {
                    pairResults[comparison.doc_id_a][comparison.doc_id_b].rowWins++
                    pairResults[comparison.doc_id_b][comparison.doc_id_a].colWins++
                  } else if (comparison.winner === comparison.doc_id_b) {
                    pairResults[comparison.doc_id_a][comparison.doc_id_b].colWins++
                    pairResults[comparison.doc_id_b][comparison.doc_id_a].rowWins++
                  }
                }

                return (
                  <div style={{ overflowX: 'auto' }}>
                    <table
                      style={{
                        borderCollapse: 'collapse',
                        backgroundColor: '#0b1220',
                        border: '1px solid #374151',
                      }}
                    >
                      <thead>
                        <tr>
                          <th
                            style={{
                              padding: '8px 10px',
                              textAlign: 'left',
                              fontSize: '12px',
                              color: '#10b981',
                              borderBottom: '2px solid #16a34a',
                              minWidth: '140px',
                              backgroundColor: '#111827',
                            }}
                          >
                            Row vs Column →
                          </th>
                          {postCombineDocIds.map((columnId) => {
                            const elo = getEloForDoc(columnId, postCombineRankings)
                            const isWinner = postCombineWinnerDocId && columnId === postCombineWinnerDocId
                            return (
                              <th
                                key={columnId}
                                title={columnId}
                                style={{
                                  padding: '6px 4px',
                                  textAlign: 'center',
                                  fontSize: '11px',
                                  color: '#6ee7b7',
                                  borderBottom: '2px solid #10b981',
                                  borderLeft: '1px solid #374151',
                                  writingMode: 'vertical-rl',
                                  transform: 'rotate(180deg)',
                                  height: '150px',
                                  width: '46px',
                                  backgroundColor: isWinner ? 'rgba(16, 185, 129, 0.12)' : '#111827',
                                }}
                              >
                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                  {getDocDisplayName(columnId)}
                                  {elo !== undefined && <span style={{ color: '#fcd34d' }}>#{Math.round(elo)}</span>}
                                </span>
                              </th>
                            )
                          })}
                        </tr>
                      </thead>
                      <tbody>
                        {postCombineDocIds.map((rowId) => (
                          <tr key={rowId}>
                            <td
                              title={rowId}
                              style={{
                                padding: '8px 10px',
                                fontSize: '11px',
                                color: '#d1d5db',
                                borderBottom: '1px solid #374151',
                                borderRight: '2px solid #16a34a',
                                backgroundColor: postCombineWinnerDocId === rowId ? 'rgba(16, 185, 129, 0.12)' : '#111827',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {getDocDisplayName(rowId)}
                            </td>
                            {postCombineDocIds.map((columnId) => {
                              if (rowId === columnId) {
                                return (
                                  <td
                                    key={`${rowId}-${columnId}`}
                                    style={{
                                      padding: '8px',
                                      textAlign: 'center',
                                      color: '#4b5563',
                                      borderBottom: '1px solid #374151',
                                      borderLeft: '1px solid #374151',
                                      backgroundColor: '#111827',
                                      fontWeight: 700,
                                    }}
                                  >
                                    —
                                  </td>
                                )
                              }

                              const record = pairResults[rowId][columnId]
                              const net = record.rowWins - record.colWins
                              const cell = getNetCellStyle(net)

                              return (
                                <td
                                  key={`${rowId}-${columnId}`}
                                  style={{
                                    padding: '8px',
                                    textAlign: 'center',
                                    color: cell.text,
                                    backgroundColor: cell.bg,
                                    borderBottom: '1px solid #374151',
                                    borderLeft: '1px solid #374151',
                                    fontFamily: 'monospace',
                                    fontWeight: 700,
                                  }}
                                  title={`${getDocDisplayName(rowId)} vs ${getDocDisplayName(columnId)}: ${record.rowWins}-${record.colWins}`}
                                >
                                  {record.rowWins}-{record.colWins}
                                </td>
                              )
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              })()}
            </div>
          )}
        </div>
      )}

      {docViewer.isOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
          onClick={closeDocViewer}
        >
          <div
            style={{
              width: 'min(1000px, 100%)',
              maxHeight: '90vh',
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: '12px',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
            onClick={(event) => event.stopPropagation()}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '16px 20px',
                borderBottom: '1px solid #374151',
              }}
            >
              <div>
                <h4 style={{ margin: 0, color: '#f9fafb', fontSize: '16px' }}>{docViewer.model}</h4>
                <div style={{ color: '#9ca3af', fontSize: '12px', marginTop: '4px', fontFamily: 'monospace' }}>
                  {docViewer.docId}
                </div>
              </div>
              <button
                onClick={closeDocViewer}
                style={{ background: 'none', border: 'none', color: '#9ca3af', cursor: 'pointer' }}
              >
                <X size={20} />
              </button>
            </div>
            <div style={{ padding: '20px', overflowY: 'auto' }}>
              {docViewer.loading ? (
                <div style={{ textAlign: 'center', color: '#9ca3af' }}>Loading document...</div>
              ) : docViewer.error ? (
                <div style={{ color: '#fca5a5' }}>{docViewer.error}</div>
              ) : (
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    color: '#e5e7eb',
                    fontSize: '12px',
                    lineHeight: 1.6,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                  }}
                >
                  {docViewer.content ?? ''}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
