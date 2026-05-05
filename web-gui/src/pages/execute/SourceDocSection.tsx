import { ChevronDown, ChevronRight, FileText, Target, Users, CheckCircle, XCircle, Loader2, AlertTriangle, Clock, Calendar, BarChart3, Trophy } from 'lucide-react'
import { useState } from 'react'
import type { SourceDocResult, Run } from '../../api'

// Local tab content for per-document display
import SourceDocEvaluationContent from './SourceDocEvaluationContent'
import SourceDocPairwiseContent from './SourceDocPairwiseContent'
import SourceDocTimelineContent from './SourceDocTimelineContent'
import SourceDocJudgeQualityContent from './SourceDocJudgeQualityContent'

interface SourceDocSectionProps {
  sourceDocId: string
  sourceDocResult: SourceDocResult
  currentRun: Run
  defaultExpanded?: boolean
  hideHeader?: boolean  // For single-doc runs, hide the collapsible header
}

type TabType = 'evaluation' | 'pairwise' | 'timeline' | 'judge_quality'

// Status badge styling
const getStatusBadge = (status: SourceDocResult['status']) => {
  const styles: Record<SourceDocResult['status'], { bg: string; text: string; icon: React.ReactNode }> = {
    pending: { bg: '#374151', text: '#9ca3af', icon: <Clock size={12} /> },
    generating: { bg: '#1e40af', text: '#93c5fd', icon: <Loader2 size={12} className="animate-spin" /> },
    single_eval: { bg: '#7c3aed', text: '#c4b5fd', icon: <Target size={12} /> },
    pairwise_eval: { bg: '#7c3aed', text: '#c4b5fd', icon: <Users size={12} /> },
    combining: { bg: '#0891b2', text: '#a5f3fc', icon: <Loader2 size={12} className="animate-spin" /> },
    post_combine_eval: { bg: '#7c3aed', text: '#c4b5fd', icon: <Target size={12} /> },
    completed: { bg: '#166534', text: '#86efac', icon: <CheckCircle size={12} /> },
    completed_with_errors: { bg: '#ca8a04', text: '#fef08a', icon: <AlertTriangle size={12} /> },
    failed: { bg: '#991b1b', text: '#fca5a5', icon: <XCircle size={12} /> },
    cancelled: { bg: '#525252', text: '#d4d4d4', icon: <XCircle size={12} /> },
    incomplete: { bg: '#78350f', text: '#fbbf24', icon: <AlertTriangle size={12} /> },
  }
  const style = styles[status] || styles.pending
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 8px',
        borderRadius: '12px',
        fontSize: '11px',
        fontWeight: 500,
        backgroundColor: style.bg,
        color: style.text,
        textTransform: 'uppercase',
      }}
    >
      {style.icon}
      {status.replace(/_/g, ' ')}
    </span>
  )
}

export default function SourceDocSection({
  sourceDocId,
  sourceDocResult,
  currentRun,
  defaultExpanded = true,
  hideHeader = false
}: SourceDocSectionProps) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded || hideHeader)
  const [activeTab, setActiveTab] = useState<TabType>('evaluation')
  const pairwiseEnabled = currentRun.config?.pairwise_enabled ?? false
  const statusPastPairwise = [
    'pairwise_eval',
    'combining',
    'post_combine_eval',
    'completed',
    'completed_with_errors',
  ].includes(sourceDocResult.status)
  const generatedDocCount = sourceDocResult.generated_doc_count ?? sourceDocResult.generated_docs.length
  const singleEvalScoreCount = sourceDocResult.single_eval_score_count ?? Object.keys(sourceDocResult.single_eval_scores || {}).length

  const hasEvalData = generatedDocCount > 0 || singleEvalScoreCount > 0
  const hasPairwiseData = pairwiseEnabled && (
    !!sourceDocResult.winner_doc_id ||
    !!sourceDocResult.pairwise_results?.winner_doc_id ||
    !!sourceDocResult.post_combine_pairwise?.winner_doc_id ||
    statusPastPairwise
  )

  // Format duration
  const formatDuration = (seconds?: number) => {
    if (!seconds) return null
    if (seconds < 60) return `${Math.round(seconds)}s`
    const mins = Math.floor(seconds / 60)
    const secs = Math.round(seconds % 60)
    return `${mins}m ${secs}s`
  }

  return (
    <div
      style={{
        marginBottom: hideHeader ? '0' : '16px',
        backgroundColor: hideHeader ? 'transparent' : '#111827',
        borderRadius: '8px',
        border: hideHeader ? 'none' : '1px solid #374151',
        overflow: 'hidden',
      }}
    >
      {/* Header - Collapsible (hidden for single-doc runs) */}
      {!hideHeader && (
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          style={{
            width: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px',
            backgroundColor: isExpanded ? '#1f2937' : 'transparent',
            border: 'none',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            {isExpanded ? (
              <ChevronDown size={18} style={{ color: '#9ca3af' }} />
            ) : (
              <ChevronRight size={18} style={{ color: '#9ca3af' }} />
            )}
            <FileText size={16} style={{ color: '#60a5fa' }} />
            <span style={{ color: 'white', fontWeight: 500, fontSize: '14px' }}>
              {sourceDocResult.source_doc_name || sourceDocId}
            </span>
            {getStatusBadge(sourceDocResult.status)}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '16px', fontSize: '12px', color: '#9ca3af' }}>
            {sourceDocResult.winner_doc_id && (() => {
              const { post_combine_pairwise, pairwise_results, single_eval_scores } = sourceDocResult
              const winnerTier =
                post_combine_pairwise?.winner_doc_id ? 'POST-COMBINE' :
                pairwise_results?.winner_doc_id      ? 'PAIRWISE' :
                (!pairwiseEnabled || !statusPastPairwise) && singleEvalScoreCount > 0 ? 'SINGLE EVAL' : null
              const tierBg: Record<string, string>    = { 'POST-COMBINE': '#1e3a5f', 'PAIRWISE': '#4c1d95', 'SINGLE EVAL': '#14532d' }
              const tierColor: Record<string, string> = { 'POST-COMBINE': '#60a5fa', 'PAIRWISE': '#c4b5fd', 'SINGLE EVAL': '#86efac' }
              // Extract readable model name from doc_id format: {src}.{src2}.{gen}.{iter}.{provider}_{model}
              const parts = sourceDocResult.winner_doc_id.split('.')
              const shortWinner = parts.length >= 5
                ? parts.slice(4).join('.').replace('_', ':')
                : sourceDocResult.winner_doc_id.slice(0, 20)
              return (
                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Trophy size={12} style={{ color: '#fbbf24' }} />
                  <span style={{ color: '#86efac' }}>{shortWinner}</span>
                  {winnerTier && (
                    <span style={{ fontSize: '10px', padding: '1px 5px', borderRadius: '3px', backgroundColor: tierBg[winnerTier], color: tierColor[winnerTier], fontWeight: 700 }}>
                      {winnerTier}
                    </span>
                  )}
                </span>
              )
            })()}
            {formatDuration(sourceDocResult.duration_seconds) && (
              <span>⏱ {formatDuration(sourceDocResult.duration_seconds)}</span>
            )}
            <span>
              {generatedDocCount} doc{generatedDocCount !== 1 ? 's' : ''}
            </span>
          </div>
        </button>
      )}

      {/* Expanded Content */}
      {isExpanded && (
        <div style={{ padding: '0 16px 16px' }}>
          {/* Error Display */}
          {sourceDocResult.errors.length > 0 && (
            <div
              style={{
                padding: '8px 12px',
                backgroundColor: '#7f1d1d',
                borderRadius: '6px',
                marginBottom: '12px',
                fontSize: '12px',
                color: '#fca5a5',
              }}
            >
              <strong>Errors:</strong>
              <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                {sourceDocResult.errors.map((err: string, i: number) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Inner Tabs */}
          <div
            style={{
              display: 'flex',
              gap: '4px',
              marginBottom: '12px',
              backgroundColor: '#1f2937',
              padding: '4px',
              borderRadius: '6px',
              width: 'fit-content',
            }}
          >
            <button
              onClick={() => setActiveTab('evaluation')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 12px',
                backgroundColor: activeTab === 'evaluation' ? '#374151' : 'transparent',
                border: 'none',
                borderRadius: '4px',
                color: activeTab === 'evaluation' ? 'white' : '#9ca3af',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: activeTab === 'evaluation' ? 500 : 400,
              }}
            >
              <Target size={14} />
              Single Evaluation
              {hasEvalData && <span style={{ color: '#86efac', fontSize: '10px' }}>●</span>}
            </button>
            <button
              onClick={() => setActiveTab('pairwise')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 12px',
                backgroundColor: activeTab === 'pairwise' ? '#374151' : 'transparent',
                border: 'none',
                borderRadius: '4px',
                color: activeTab === 'pairwise' ? 'white' : '#9ca3af',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: activeTab === 'pairwise' ? 500 : 400,
              }}
            >
              <Users size={14} />
              Pairwise
              {hasPairwiseData && <span style={{ color: '#86efac', fontSize: '10px' }}>●</span>}
            </button>
            <button
              onClick={() => setActiveTab('timeline')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 12px',
                backgroundColor: activeTab === 'timeline' ? '#374151' : 'transparent',
                border: 'none',
                borderRadius: '4px',
                color: activeTab === 'timeline' ? 'white' : '#9ca3af',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: activeTab === 'timeline' ? 500 : 400,
              }}
            >
              <Calendar size={14} />
              Timeline
            </button>
            <button
              onClick={() => setActiveTab('judge_quality')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '8px 12px',
                backgroundColor: activeTab === 'judge_quality' ? '#374151' : 'transparent',
                border: 'none',
                borderRadius: '4px',
                color: activeTab === 'judge_quality' ? 'white' : '#9ca3af',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: activeTab === 'judge_quality' ? 500 : 400,
              }}
            >
              <BarChart3 size={14} />
              Judge Quality
              {hasEvalData && <span style={{ color: '#c4b5fd', fontSize: '10px' }}>●</span>}
            </button>
          </div>

          {/* Tab Content */}
          <div
            style={{
              backgroundColor: '#1f2937',
              borderRadius: '8px',
              padding: '16px',
              minHeight: '200px',
            }}
          >
            {activeTab === 'evaluation' && (
              <SourceDocEvaluationContent
                sourceDocResult={sourceDocResult}
                runId={currentRun.id}
                sourceDocId={sourceDocId}
              />
            )}
            {activeTab === 'pairwise' && (
              <SourceDocPairwiseContent
                sourceDocResult={sourceDocResult}
                runId={currentRun.id}
                pairwiseEnabled={currentRun.config?.pairwise_enabled ?? false}
              />
            )}
            {activeTab === 'timeline' && (
              <SourceDocTimelineContent
                runId={currentRun.id}
                sourceDocId={sourceDocId}
              />
            )}
            {activeTab === 'judge_quality' && (
              <SourceDocJudgeQualityContent
                runId={currentRun.id}
                sourceDocId={sourceDocId}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
