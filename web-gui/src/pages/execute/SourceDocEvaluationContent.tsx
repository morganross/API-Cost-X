import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, FileText, Loader2, Target, X } from 'lucide-react'
import { apiClient } from '../../api/client'
import { runsApi } from '../../api/runs'
import type { SourceDocResult } from '../../api'
import { Heatmap } from '../../components/quality/Heatmap'
import { buildScatterDataset, type ScatterPointShape } from '../../data/qualityHeatmaps'
import { getScoreBadgeStyle } from './utils'

interface SourceDocEvaluationContentProps {
  sourceDocResult: SourceDocResult
  runId: string
  sourceDocId: string
}

interface DocViewerState {
  isOpen: boolean
  docId: string
  model: string
  content: string | null
  loading: boolean
  error: string | null
}

interface EvalHeatmapCell {
  avg_score: number | null
  trial_count: number
  judge_scores: Record<string, number>
  judge_reasons?: Record<string, string>
}

interface EvalHeatmapRow {
  doc_id: string
  source_doc_id: string
  generator: string
  model: string
  iteration: number
  cells: Record<string, EvalHeatmapCell>
  overall_avg: number | null
}

interface EvalHeatmapSection {
  _meta: {
    criteria: string[]
    judge_models: string[]
    doc_count: number
    criterion_count: number
  }
  rows: EvalHeatmapRow[]
}

interface EvaluationPageMeta {
  total_count: number
  returned_count: number
  offset: number
  limit: number
  has_more: boolean
}

const EXECUTE_GENERATOR_SHAPES: Record<string, ScatterPointShape> = {
  fpf: 'circle',
  gptr: 'diamond',
  dr: 'triangle',
  aiq: 'square',
}

function normalizeGeneratorKey(generator: string | null | undefined): string {
  return String(generator || '').trim().toLowerCase()
}

function normalizeScore(score: number | undefined): number | undefined {
  if (typeof score !== 'number' || Number.isNaN(score)) return undefined
  return score > 1 ? score / 5 : score
}

function getScoreCellStyle(score: number | null | undefined): { bg: string; text: string } {
  if (score == null || Number.isNaN(score)) return { bg: '#111827', text: '#6b7280' }
  if (score >= 4.5) return { bg: '#14532d', text: '#4ade80' }
  if (score >= 3.75) return { bg: '#166534', text: '#86efac' }
  if (score >= 3.0) return { bg: '#713f12', text: '#fde68a' }
  if (score >= 2.0) return { bg: '#7c2d12', text: '#fdba74' }
  return { bg: '#7f1d1d', text: '#fca5a5' }
}

export default function SourceDocEvaluationContent({
  sourceDocResult,
  runId,
  sourceDocId,
}: SourceDocEvaluationContentProps) {
  const [evaluationData, setEvaluationData] = useState<SourceDocResult | null>(null)
  const [evaluationMeta, setEvaluationMeta] = useState<EvaluationPageMeta | null>(null)
  const [evaluationLoading, setEvaluationLoading] = useState(false)
  const [evaluationError, setEvaluationError] = useState<string | null>(null)
  const [evaluationLoadingMore, setEvaluationLoadingMore] = useState(false)
  const [docViewer, setDocViewer] = useState<DocViewerState>({
    isOpen: false,
    docId: '',
    model: '',
    content: null,
    loading: false,
    error: null,
  })
  const [heatmap, setHeatmap] = useState<EvalHeatmapSection | null>(null)
  const [heatmapLoading, setHeatmapLoading] = useState(false)
  const [heatmapError, setHeatmapError] = useState<string | null>(null)

  const mergeEvaluationPage = (incoming: SourceDocResult, append: boolean) => {
    setEvaluationData((previous) => {
      if (!append || !previous) return incoming
      return {
        ...incoming,
        generated_docs: [...(previous.generated_docs || []), ...(incoming.generated_docs || [])],
        single_eval_scores: {
          ...(previous.single_eval_scores || {}),
          ...(incoming.single_eval_scores || {}),
        },
        post_combine_eval_scores: {
          ...(previous.post_combine_eval_scores || {}),
          ...(incoming.post_combine_eval_scores || {}),
        },
      }
    })
  }

  useEffect(() => {
    if (!runId || !sourceDocId) return

    setEvaluationLoading(true)
    setEvaluationError(null)
    setEvaluationMeta(null)

    runsApi
      .getSourceDocEvaluation(runId, sourceDocId, { limit: 25, offset: 0 })
      .then((payload) => {
        if (payload.evaluation) {
          setEvaluationData(payload.evaluation)
        } else {
          setEvaluationData(null)
        }
        setEvaluationMeta(payload.meta)
      })
      .catch((reason: unknown) => {
        setEvaluationError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => setEvaluationLoading(false))
  }, [runId, sourceDocId])

  useEffect(() => {
    if (!runId || !sourceDocId) return

    setHeatmapLoading(true)
    setHeatmapError(null)

    apiClient
      .get<{ eval_heatmap?: EvalHeatmapSection }>(`/runs/${runId}/sections/eval-heatmap`, {
        source_doc_id: sourceDocId,
      })
      .then((payload) => setHeatmap(payload.eval_heatmap ?? null))
      .catch((reason: unknown) => {
        setHeatmapError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => setHeatmapLoading(false))
  }, [runId, sourceDocId])

  const openDocViewer = async (docId: string, model: string) => {
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

  const loadMoreEvaluationDocs = async () => {
    if (!runId || !sourceDocId || !evaluationMeta?.has_more || evaluationLoadingMore) return
    setEvaluationLoadingMore(true)
    setEvaluationError(null)
    try {
      const payload = await runsApi.getSourceDocEvaluation(runId, sourceDocId, {
        limit: evaluationMeta.limit,
        offset: evaluationMeta.offset + evaluationMeta.returned_count,
      })
      if (payload.evaluation) {
        mergeEvaluationPage(payload.evaluation, true)
      }
      setEvaluationMeta(payload.meta)
    } catch (reason) {
      setEvaluationError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setEvaluationLoadingMore(false)
    }
  }

  const evaluation = evaluationData ?? sourceDocResult

  const {
    generated_docs,
    single_eval_scores,
    combined_doc,
    combined_docs,
    post_combine_eval_scores,
  } = evaluation

  const orderedDocs = useMemo(
    () =>
      [...generated_docs].sort(
        (left, right) => (single_eval_scores[right.id] ?? Number.NEGATIVE_INFINITY) - (single_eval_scores[left.id] ?? Number.NEGATIVE_INFINITY)
      ),
    [generated_docs, single_eval_scores]
  )

  const combinedItems = combined_docs?.length ? combined_docs : combined_doc ? [combined_doc] : []
  const postCombineEntries = Object.entries(post_combine_eval_scores || {})
    .map(([judgeModel, value]) => [judgeModel, Number(value)] as const)
    .filter(([, value]) => !Number.isNaN(value))
  const heatmapRows = (heatmap?.rows || []).filter((row) => row.source_doc_id === sourceDocId)
  const criteria = heatmap?._meta?.criteria || []
  const judgeModels = heatmap?._meta?.judge_models || []
  const visibleJudgeModels = judgeModels.length > 0 ? judgeModels : []
  const generationQualityChart = useMemo(() => {
    const presentGenerators = Array.from(
      new Set(
        heatmapRows
          .map((row) => normalizeGeneratorKey(row.generator))
          .filter((generator) => generator in EXECUTE_GENERATOR_SHAPES)
      )
    )

    return buildScatterDataset({
      rows: heatmapRows,
      rowKey: (row) => row.doc_id,
      rowLabel: (row) => row.model,
      legend: 'Live generation results for this source document. X is document order and Y is average single-evaluation score. Colors show provider and shapes show report type.',
      pointShape: (row) => EXECUTE_GENERATOR_SHAPES[normalizeGeneratorKey(row.generator)],
      shapeLegend: presentGenerators.map((generator) => ({
        label: generator.toUpperCase(),
        shape: EXECUTE_GENERATOR_SHAPES[generator],
      })),
      xMetric: {
        key: 'document_index',
        label: 'Document',
        direction: 'higher_better',
        value: (row) => heatmapRows.findIndex((item) => item.doc_id === row.doc_id) + 1,
        format: (value) => (value == null ? '—' : value.toFixed(0)),
      },
      yMetric: {
        key: 'overall_avg',
        label: 'Avg Score',
        direction: 'higher_better',
        value: (row) => row.overall_avg,
        format: (value) => (value == null ? '—' : value.toFixed(2)),
      },
      yDomain: {
        min: 1,
        max: 5,
        padRatio: 0.04,
        minPad: 0.04,
      },
    })
  }, [heatmapRows])
  const shouldShowGenerationQualityChart =
    generated_docs.length > 1 && generationQualityChart.points.length > 1

  if (evaluationLoading && !evaluationData) {
    return (
      <div style={{ padding: '24px', color: '#9ca3af', fontSize: '13px' }}>
        <Loader2 size={16} className="animate-spin" style={{ display: 'inline', marginRight: '8px' }} />
        Loading evaluation details...
      </div>
    )
  }

  if (evaluationError && !evaluationData) {
    return (
      <div style={{ padding: '24px', color: '#fca5a5', fontSize: '13px' }}>
        Failed to load evaluation details: {evaluationError}
      </div>
    )
  }

  if (generated_docs.length === 0 && Object.keys(single_eval_scores).length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '32px', color: '#9ca3af' }}>
        <Target size={48} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
        <p>No evaluation data available yet.</p>
        <p style={{ fontSize: '12px', marginTop: '8px' }}>
          Evaluations will appear here once document generation completes.
        </p>
      </div>
    )
  }

  const getCombinedDocDisplayName = (docId: string): string => {
    const parts = docId.split('.')
    if (parts.length >= 4) {
      const modelPart = parts.slice(3).join('.').replace('_', ':')
      const fileUuid = parts[2].slice(0, 8)
      return `${modelPart} [${fileUuid}]`
    }
    return docId
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h4 style={{ color: '#60a5fa', fontSize: '14px', fontWeight: 600, margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Target size={16} />
          Generated Documents ({evaluationMeta?.total_count ?? generated_docs.length})
        </h4>
      </div>

      {heatmapLoading && (
        <div style={{ padding: '12px', color: '#9ca3af', fontSize: '12px' }}>
          <Loader2 size={14} className="animate-spin" style={{ display: 'inline', marginRight: '6px' }} />
          Loading evaluation heatmap...
        </div>
      )}

      {!heatmapLoading && heatmapError && (
        <div style={{ padding: '12px', color: '#fca5a5', fontSize: '12px' }}>
          Failed to load evaluation heatmap: {heatmapError}
        </div>
      )}

      {evaluationError && evaluationData && (
        <div style={{ padding: '12px', color: '#fca5a5', fontSize: '12px' }}>
          Evaluation detail refresh failed: {evaluationError}
        </div>
      )}

      {!heatmapLoading && shouldShowGenerationQualityChart && (
        <div style={{ marginBottom: '18px' }}>
          <Heatmap title="Generation Quality Chart" dataset={generationQualityChart} />
        </div>
      )}

      {!heatmapLoading && heatmapRows.length > 0 && (
        <div style={{ marginBottom: '18px' }}>
          <div
            style={{
              marginBottom: '10px',
              padding: '8px 12px',
              borderRadius: '8px',
              fontSize: '12px',
              backgroundColor: '#111827',
              border: '1px solid #374151',
              color: '#9ca3af',
            }}
          >
            <strong style={{ color: '#d1d5db' }}>Criteria Heatmap:</strong> {heatmapRows.length} docs × {criteria.length} criteria
            {judgeModels.length > 0 && (
              <span style={{ marginLeft: '10px', display: 'inline-flex', flexWrap: 'wrap', gap: '8px' }}>
                Judges: {judgeModels.join(', ')}
              </span>
            )}
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '11px' }}>
              <thead>
                <tr>
                  <th style={{ padding: '6px 8px', textAlign: 'left', color: '#9ca3af', borderBottom: '1px solid #374151', minWidth: '180px' }}>
                    Document
                  </th>
                  <th style={{ padding: '6px 8px', textAlign: 'center', color: '#a78bfa', borderBottom: '1px solid #374151', minWidth: '56px' }}>
                    Avg
                  </th>
                  {criteria.map((criterion) => (
                    <th
                      key={criterion}
                      title={criterion}
                      style={{ padding: '4px 6px', textAlign: 'center', color: '#6b7280', borderBottom: '1px solid #374151', minWidth: '88px' }}
                    >
                      {criterion.length > 10 ? `${criterion.slice(0, 10)}…` : criterion}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmapRows.map((row) => {
                  const overallStyle = getScoreCellStyle(row.overall_avg)
                  return (
                    <tr key={row.doc_id} style={{ borderBottom: '1px solid #1f2937' }}>
                      <td style={{ padding: '6px 8px', color: '#d1d5db', backgroundColor: '#111827' }}>
                        <button
                          onClick={() => openDocViewer(row.doc_id, row.model)}
                          title={row.doc_id}
                          style={{
                            color: '#60a5fa',
                            background: 'none',
                            border: 'none',
                            padding: 0,
                            cursor: 'pointer',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            font: 'inherit',
                            fontSize: '11px',
                            fontFamily: 'monospace',
                            fontWeight: 600,
                          }}
                        >
                          {row.model}
                          <ExternalLink size={10} />
                        </button>
                      </td>
                      <td
                        style={{
                          padding: '6px 8px',
                          textAlign: 'center',
                          fontWeight: 700,
                          color: overallStyle.text,
                          backgroundColor: overallStyle.bg,
                        }}
                      >
                        {row.overall_avg != null ? row.overall_avg.toFixed(2) : '—'}
                      </td>
                      {criteria.map((criterion) => {
                        const cell = row.cells[criterion]
                        if (!cell || Object.keys(cell.judge_scores || {}).length === 0) {
                          return (
                            <td key={criterion} style={{ padding: '4px 3px', textAlign: 'center', color: '#4b5563' }}>
                              —
                            </td>
                          )
                        }

                        const avgStyle = getScoreCellStyle(cell.avg_score)
                        const title = judgeModels
                          .map((judge) => {
                            const score = cell.judge_scores[judge]
                            const reason = cell.judge_reasons?.[judge]
                            if (score === undefined) return `${judge}: —`
                            return reason ? `${judge}: ${score.toFixed(1)}\n\n${reason}` : `${judge}: ${score.toFixed(1)}`
                          })
                          .join('\n\n')

                        return (
                          <td
                            key={criterion}
                            title={title}
                            style={{
                              padding: '2px',
                              textAlign: 'center',
                              backgroundColor: '#0b1120',
                              color: avgStyle.text,
                              cursor: 'default',
                            }}
                          >
                            <div
                              style={{
                                display: 'grid',
                                gridTemplateRows: `repeat(${Math.max(visibleJudgeModels.length, 1)}, minmax(0, 1fr))`,
                                gap: '2px',
                              }}
                            >
                              {visibleJudgeModels.map((judge) => {
                                const score = cell.judge_scores[judge]
                                const judgeStyle = getScoreCellStyle(score ?? null)
                                return (
                                  <div
                                    key={`${criterion}-${judge}`}
                                    style={{
                                      backgroundColor: judgeStyle.bg,
                                      color: judgeStyle.text,
                                      fontWeight: 700,
                                      fontSize: '10px',
                                      lineHeight: 1.1,
                                      padding: '4px 2px',
                                      borderRadius: '3px',
                                    }}
                                  >
                                    {score != null ? score.toFixed(1) : '—'}
                                  </div>
                                )
                              })}
                            </div>
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {orderedDocs.map((document, index) => {
          const rawScore = single_eval_scores[document.id]
          const normalized = normalizeScore(rawScore)
          const badge = normalized !== undefined ? getScoreBadgeStyle(normalized) : null
          return (
            <div
              key={document.id}
              style={{
                padding: '14px 16px',
                backgroundColor: index % 2 === 0 ? '#111827' : '#0f172a',
                border: '1px solid #374151',
                borderRadius: '8px',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div style={{ minWidth: 0 }}>
                  <button
                    onClick={() => openDocViewer(document.id, document.model)}
                    style={{
                      color: '#60a5fa',
                      background: 'none',
                      border: 'none',
                      padding: 0,
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontSize: '13px',
                      fontWeight: 600,
                      fontFamily: 'monospace',
                    }}
                    title={document.id}
                  >
                    {document.model}
                    <ExternalLink size={12} />
                  </button>
                  <div style={{ marginTop: '6px', color: '#9ca3af', fontSize: '12px', fontFamily: 'monospace' }}>
                    {document.id}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                  {badge && (
                    <span
                      style={{
                        padding: '6px 10px',
                        borderRadius: '999px',
                        backgroundColor: badge.bg,
                        color: badge.text,
                        fontWeight: 700,
                        fontSize: '12px',
                      }}
                    >
                      Score: {rawScore.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {evaluationMeta?.has_more && (
        <div style={{ marginTop: '14px', display: 'flex', justifyContent: 'center' }}>
          <button
            onClick={loadMoreEvaluationDocs}
            disabled={evaluationLoadingMore}
            style={{
              padding: '8px 12px',
              borderRadius: '6px',
              border: '1px solid #374151',
              backgroundColor: evaluationLoadingMore ? '#1f2937' : '#111827',
              color: evaluationLoadingMore ? '#6b7280' : '#d1d5db',
              cursor: evaluationLoadingMore ? 'not-allowed' : 'pointer',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            {evaluationLoadingMore ? 'Loading…' : 'Load More Documents'}
          </button>
        </div>
      )}

      {combinedItems.length > 0 && (
        <div style={{ marginTop: '24px', paddingTop: '16px', borderTop: '1px solid #374151' }}>
          <h4 style={{ color: '#a78bfa', fontSize: '14px', fontWeight: 600, marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Target size={16} />
            Combined Documents ({combinedItems.length})
          </h4>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {combinedItems.map((item, index) => (
              <div
                key={item.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '12px',
                  padding: '12px 16px',
                  backgroundColor: index % 2 === 0 ? '#111827' : '#0f172a',
                  borderRadius: '8px',
                  border: '1px solid #374151',
                  flexWrap: 'wrap',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <FileText size={14} style={{ color: '#a78bfa' }} />
                  <button
                    onClick={() => openDocViewer(item.id, getCombinedDocDisplayName(item.id))}
                    style={{
                      color: '#a78bfa',
                      background: 'none',
                      border: 'none',
                      padding: 0,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '13px',
                    }}
                  >
                    {getCombinedDocDisplayName(item.id)}
                    <ExternalLink size={12} />
                  </button>
                </div>
                {postCombineEntries.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {postCombineEntries.map(([judgeModel, score]) => {
                      const badge = getScoreBadgeStyle(normalizeScore(score) ?? score)
                      return (
                        <span
                          key={judgeModel}
                          style={{
                            padding: '6px 10px',
                            borderRadius: '999px',
                            backgroundColor: badge.bg,
                            color: badge.text,
                            fontWeight: 'bold',
                            fontSize: '12px',
                            fontFamily: 'monospace',
                          }}
                          title={judgeModel}
                        >
                          {judgeModel}: {score.toFixed(2)}
                        </span>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
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
                <div style={{ textAlign: 'center', color: '#9ca3af' }}>
                  <Loader2 size={16} className="animate-spin" style={{ display: 'inline', marginRight: '6px' }} />
                  Loading document...
                </div>
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
