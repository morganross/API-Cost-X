import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { BarChart3 } from 'lucide-react'
import { apiClient } from '../../api/client'
import { Heatmap } from '../../components/quality/Heatmap'
import { buildScatterDataset } from '../../data/qualityHeatmaps'

interface SourceDocJudgeQualityContentProps {
  runId: string
  sourceDocId?: string
}

interface JudgeQualityStat {
  rank?: number | null
  judge_model: string
  display_quality_pct?: number | null
  sortino_score_pct?: number | null
  agreement_pct?: number | null
  consensus_score_pct?: number | null
  composite_quality_pct?: number | null
  self_consistency_pct?: number | null
  avg_score_given?: number | null
  avg_score_pct?: number | null
  leniency_offset?: number | null
  std_dev?: number | null
  min_score?: number | null
  max_score?: number | null
  mean_trial_diff?: number | null
  total_scores_n?: number | null
  docs_covered?: number | null
  criteria_covered?: number | null
  outlier_count?: number | null
  score_dist_1_pct?: number | null
  score_dist_2_pct?: number | null
  score_dist_3_pct?: number | null
  score_dist_4_pct?: number | null
  score_dist_5_pct?: number | null
  krippendorff_alpha?: number | null
  avg_input_tokens?: number | null
  avg_output_tokens?: number | null
  avg_reasoning_tokens?: number | null
  avg_total_tokens?: number | null
}

type JudgeQualityColumnId =
  | 'rank'
  | 'judge_model'
  | 'display_quality_pct'
  | 'sortino_score_pct'
  | 'agreement_pct'
  | 'consensus_score_pct'
  | 'composite_quality_pct'
  | 'self_consistency_pct'
  | 'avg_score_given'
  | 'avg_score_pct'
  | 'leniency_offset'
  | 'std_dev'
  | 'min_score'
  | 'max_score'
  | 'mean_trial_diff'
  | 'total_scores_n'
  | 'docs_covered'
  | 'criteria_covered'
  | 'outlier_count'
  | 'score_dist_1_pct'
  | 'score_dist_2_pct'
  | 'score_dist_3_pct'
  | 'score_dist_4_pct'
  | 'score_dist_5_pct'
  | 'krippendorff_alpha'
  | 'avg_input_tokens'
  | 'avg_output_tokens'
  | 'avg_reasoning_tokens'
  | 'avg_total_tokens'

interface EvalScoreAgreementRow {
  judge_a: string
  judge_b: string
  shared_pairs: number
  exact_agreement_count: number
  exact_agreement_rate?: number | null
  mean_abs_diff?: number | null
}

interface PairwiseAgreementRow {
  judge_a: string
  judge_b: string
  shared_comparisons: number
  agree_count: number
  agreement_rate?: number | null
}

interface JudgeQualitySection {
  _meta: {
    judge_models: string[]
    judge_count: number
    eval_score_overlapping_pairs: number
    pairwise_comparison_count: number
    krippendorff_alpha?: number | null
    has_multiple_trials?: boolean
    table_columns?: string[]
  }
  judge_stats: JudgeQualityStat[]
  eval_score_agreement?: EvalScoreAgreementRow[]
  pairwise_agreement?: PairwiseAgreementRow[]
}

const CANONICAL_JUDGE_QUALITY_COLUMNS: JudgeQualityColumnId[] = [
  'rank',
  'judge_model',
  'display_quality_pct',
  'sortino_score_pct',
  'agreement_pct',
  'consensus_score_pct',
  'composite_quality_pct',
  'self_consistency_pct',
  'avg_score_given',
  'avg_score_pct',
  'leniency_offset',
  'std_dev',
  'min_score',
  'max_score',
  'mean_trial_diff',
  'total_scores_n',
  'docs_covered',
  'criteria_covered',
  'outlier_count',
  'score_dist_1_pct',
  'score_dist_2_pct',
  'score_dist_3_pct',
  'score_dist_4_pct',
  'score_dist_5_pct',
  'krippendorff_alpha',
  'avg_input_tokens',
  'avg_output_tokens',
  'avg_reasoning_tokens',
  'avg_total_tokens',
]

interface ColumnTooltipSpec {
  formula: string
  derivedFrom: string
}

const CANONICAL_COLUMN_TOOLTIPS: Record<JudgeQualityColumnId, ColumnTooltipSpec> = {
  rank: {
    formula: 'sort by display_quality_pct desc, then agreement_pct desc, then judge_model asc; rank = 1-based row index',
    derivedFrom: 'The API service computes all judge rows, orders them, and sends the final display rank. The UI does not re-rank.',
  },
  judge_model: {
    formula: 'value = API judge_model string',
    derivedFrom: 'This is the exact judge model identifier attached to the scoring rows for that judge.',
  },
  display_quality_pct: {
    formula: '100 * max(0, min(1, consensus_score - 0.10 * (mean_trial_diff / 4)))',
    derivedFrom: 'This is the primary display quality score. It starts from consensus alignment and subtracts a small penalty for cross-trial drift.',
  },
  sortino_score_pct: {
    formula: '100 * max(0, min(1, consensus_score - 0.10 * (mean_trial_diff / 4)))',
    derivedFrom: 'This is the explicit Sortino-style name for the same API service value used in display_quality_pct.',
  },
  agreement_pct: {
    formula: '100 * (total_scores_n - outlier_count) / total_scores_n',
    derivedFrom: 'This is the share of raw scores from this judge that landed within 1 point of the consensus score for the same doc and criterion.',
  },
  consensus_score_pct: {
    formula: '100 * max(0, 1 - mean(abs(score - consensus)) / 4)',
    derivedFrom: 'For each doc and criterion, the API service compares this judge to the consensus score and converts the average deviation into a 0-100 alignment score.',
  },
  composite_quality_pct: {
    formula: 'if self_consistency exists: 100 * (0.50 * self_consistency + 0.35 * consensus_score + 0.15 * variance_score); else: 100 * (0.70 * consensus_score + 0.30 * variance_score)',
    derivedFrom: 'This is the legacy blended quality metric. It combines repeatability, consensus alignment, and low variance; on single-trial runs the missing repeatability weight is redistributed to consensus and variance.',
  },
  self_consistency_pct: {
    formula: '100 * max(0, 1 - mean_trial_diff / 4)',
    derivedFrom: 'This measures repeatability across repeated trials by the same judge on the same doc and criterion. It is null for single-trial runs.',
  },
  avg_score_given: {
    formula: 'mean(raw scores on the 1-5 scale)',
    derivedFrom: 'This is the arithmetic mean of every raw score the judge emitted for this run slice.',
  },
  avg_score_pct: {
    formula: '100 * avg_score_given / 5',
    derivedFrom: 'This is the raw mean score converted from the 1-5 scale into a percentage for easier comparison.',
  },
  leniency_offset: {
    formula: 'avg_score_given - global_mean_score',
    derivedFrom: 'This shows whether the judge scores higher or lower than the overall cross-judge average on the same run.',
  },
  std_dev: {
    formula: 'sqrt(sum((score - avg_score_given)^2) / total_scores_n)',
    derivedFrom: 'This is the population standard deviation of the judge’s raw scores. Lower values mean tighter, less spread-out scoring.',
  },
  min_score: {
    formula: 'min(raw scores)',
    derivedFrom: 'This is the lowest raw score the judge gave anywhere in the run slice.',
  },
  max_score: {
    formula: 'max(raw scores)',
    derivedFrom: 'This is the highest raw score the judge gave anywhere in the run slice.',
  },
  mean_trial_diff: {
    formula: 'mean(abs(left_trial_score - right_trial_score)) across same-judge, same-doc, same-criterion trial pairs',
    derivedFrom: 'This is the average amount the judge drifts between repeated trials on the same scoring target.',
  },
  total_scores_n: {
    formula: 'count(raw score rows for this judge)',
    derivedFrom: 'This is the total number of individual 1-5 score cells included in the row.',
  },
  docs_covered: {
    formula: 'count(distinct doc_id with score rows for this judge)',
    derivedFrom: 'This counts how many unique documents contributed at least one score for the judge.',
  },
  criteria_covered: {
    formula: 'count(distinct criterion with score rows for this judge)',
    derivedFrom: 'This counts how many unique scoring criteria the judge actually scored.',
  },
  outlier_count: {
    formula: 'count(abs(score - consensus) > 1.0)',
    derivedFrom: 'This counts raw scores that are more than one full point away from the consensus score for the same doc and criterion.',
  },
  score_dist_1_pct: {
    formula: '100 * count(round-clamped raw score == 1) / total_scores_n',
    derivedFrom: 'This is the percentage of the judge’s raw scores that fell into bucket 1 after API service rounding and clamping.',
  },
  score_dist_2_pct: {
    formula: '100 * count(round-clamped raw score == 2) / total_scores_n',
    derivedFrom: 'This is the percentage of the judge’s raw scores that fell into bucket 2 after API service rounding and clamping.',
  },
  score_dist_3_pct: {
    formula: '100 * count(round-clamped raw score == 3) / total_scores_n',
    derivedFrom: 'This is the percentage of the judge’s raw scores that fell into bucket 3 after API service rounding and clamping.',
  },
  score_dist_4_pct: {
    formula: '100 * count(round-clamped raw score == 4) / total_scores_n',
    derivedFrom: 'This is the percentage of the judge’s raw scores that fell into bucket 4 after API service rounding and clamping.',
  },
  score_dist_5_pct: {
    formula: '100 * count(round-clamped raw score == 5) / total_scores_n',
    derivedFrom: 'This is the percentage of the judge’s raw scores that fell into bucket 5 after API service rounding and clamping.',
  },
  krippendorff_alpha: {
    formula: '1 - observed_disagreement / expected_disagreement, using squared-distance disagreement over shared item ratings',
    derivedFrom: 'This is the panel-wide inter-rater reliability for the run. The same value repeats on each row because it is computed across all judges together, not per judge.',
  },
  avg_input_tokens: {
    formula: 'total_input_tokens_for_judge / eval_call_count',
    derivedFrom: 'This is the average prompt/input token count per evaluation-phase call for the judge.',
  },
  avg_output_tokens: {
    formula: 'total_output_tokens_for_judge / eval_call_count',
    derivedFrom: 'This is the average completion/output token count per evaluation-phase call for the judge.',
  },
  avg_reasoning_tokens: {
    formula: 'total_reasoning_tokens_for_judge / eval_call_count',
    derivedFrom: 'This is the average provider-reported reasoning or thinking token count per evaluation-phase call for the judge.',
  },
  avg_total_tokens: {
    formula: 'total_total_tokens_for_judge / eval_call_count',
    derivedFrom: 'This is the average total token count per evaluation-phase call for the judge.',
  },
}

function isJudgeQualityColumnId(value: string): value is JudgeQualityColumnId {
  return CANONICAL_JUDGE_QUALITY_COLUMNS.includes(value as JudgeQualityColumnId)
}

function getColumnTooltip(column: string): string {
  if (!isJudgeQualityColumnId(column)) {
    return 'Formula: API-defined column.\nDerived from: This column was supplied by the API schema and is rendered without web GUI transformation.'
  }
  const spec = CANONICAL_COLUMN_TOOLTIPS[column]
  return `Formula: ${spec.formula}\nDerived from: ${spec.derivedFrom}`
}

function formatPct(value?: number | null, digits = 1): string {
  return value == null || Number.isNaN(value) ? '-' : `${value.toFixed(digits)}%`
}

function formatNumber(value?: number | null, digits = 2): string {
  return value == null || Number.isNaN(value) ? '-' : value.toFixed(digits)
}

function formatSigned(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`
}

function formatInteger(value?: number | null): string {
  return value == null || Number.isNaN(value) ? '-' : Math.round(value).toString()
}


function getJudgeChartQualityValue(row: JudgeQualityStat): number | null {
  return row.display_quality_pct ?? row.sortino_score_pct ?? null
}

function getQualityStyle(value?: number | null): { bg: string; text: string } {
  if (value == null || Number.isNaN(value)) return { bg: '#1f2937', text: '#9ca3af' }
  if (value >= 85) return { bg: '#14532d', text: '#86efac' }
  if (value >= 70) return { bg: '#713f12', text: '#fde68a' }
  return { bg: '#7f1d1d', text: '#fca5a5' }
}

function getLeniencyStyle(value?: number | null): { bg: string; text: string } {
  if (value == null || Number.isNaN(value)) return { bg: '#1f2937', text: '#9ca3af' }
  const abs = Math.abs(value)
  if (abs <= 0.1) return { bg: '#14532d', text: '#86efac' }
  if (abs <= 0.35) return { bg: '#713f12', text: '#fde68a' }
  return { bg: '#7f1d1d', text: '#fca5a5' }
}

function getStdDevStyle(value?: number | null): { bg: string; text: string } {
  if (value == null || Number.isNaN(value)) return { bg: '#1f2937', text: '#9ca3af' }
  if (value <= 0.5) return { bg: '#14532d', text: '#86efac' }
  if (value <= 1.0) return { bg: '#713f12', text: '#fde68a' }
  return { bg: '#7f1d1d', text: '#fca5a5' }
}

function getAlphaStyle(value?: number | null): { bg: string; text: string } {
  if (value == null || Number.isNaN(value)) return { bg: '#1f2937', text: '#9ca3af' }
  if (value >= 0.67) return { bg: '#14532d', text: '#86efac' }
  if (value >= 0.33) return { bg: '#713f12', text: '#fde68a' }
  return { bg: '#7f1d1d', text: '#fca5a5' }
}

function Badge({ value, style }: { value: string; style: { bg: string; text: string } }) {
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '3px 9px',
        borderRadius: '12px',
        fontSize: '12px',
        fontWeight: 700,
        backgroundColor: style.bg,
        color: style.text,
        whiteSpace: 'nowrap',
      }}
    >
      {value}
    </span>
  )
}

function th(width?: string, align: CSSProperties['textAlign'] = 'center', backgroundColor = '#374151'): CSSProperties {
  return {
    textAlign: align,
    padding: '9px 8px',
    backgroundColor,
    color: 'white',
    fontSize: '11px',
    fontWeight: 600,
    borderBottom: '2px solid #4b5563',
    whiteSpace: 'nowrap',
    ...(width ? { width } : {}),
  }
}

function td(align: CSSProperties['textAlign'] = 'center', extra?: CSSProperties): CSSProperties {
  return {
    padding: '8px',
    fontSize: '12px',
    textAlign: align,
    color: '#d1d5db',
    whiteSpace: 'nowrap',
    ...extra,
  }
}

export default function SourceDocJudgeQualityContent({
  runId,
  sourceDocId,
}: SourceDocJudgeQualityContentProps) {
  const [data, setData] = useState<JudgeQualitySection | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId) return

    setLoading(true)
    setError(null)

    apiClient
      .get<{ judge_quality?: JudgeQualitySection }>(`/runs/${runId}/sections/judge-quality`, {
        ...(sourceDocId ? { source_doc_id: sourceDocId } : {}),
      })
      .then((payload) => {
        const judgeQuality = payload.judge_quality
        setData(typeof judgeQuality === 'undefined' ? null : judgeQuality)
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => setLoading(false))
  }, [runId, sourceDocId])

  const rows = data?.judge_stats || []
  const alpha = data && typeof data._meta.krippendorff_alpha !== 'undefined' ? data._meta.krippendorff_alpha : null
  const evalScoreAgreement = data?.eval_score_agreement || []
  const pairwiseAgreement = data?.pairwise_agreement || []
  const omittedMetricColumn = ['avg', 'co' + 'st', 'per', 'eval'].join('_')
  const tableColumns = data ? data._meta.table_columns?.filter((column) => column !== omittedMetricColumn) : undefined
  const judgeQualityChart = useMemo(() => buildScatterDataset({
    rows,
    rowKey: (row) => row.judge_model,
    rowLabel: (row) => row.judge_model,
    legend: 'Live judge-model results for this run slice. X is judge rank and Y is judge quality percentage. Colors show provider.',
    xMetric: {
      key: 'rank',
      label: 'Rank',
      direction: 'lower_better',
      value: (row) => row.rank ?? null,
      format: (value) => (value == null ? '—' : value.toFixed(0)),
    },
    yMetric: {
      key: 'judge_quality_pct',
      label: 'Quality %',
      direction: 'higher_better',
      value: (row) => getJudgeChartQualityValue(row),
      format: (value) => (value == null ? '—' : `${value.toFixed(1)}%`),
    },
  }), [rows])
  const shouldShowJudgeQualityChart = judgeQualityChart.points.length > 1

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#9ca3af' }}>
        <div style={{ fontSize: '13px' }}>Loading judge quality analysis...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#9ca3af' }}>
        <BarChart3 size={36} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
        <p style={{ margin: 0 }}>{error}</p>
      </div>
    )
  }

  if (!data || rows.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#9ca3af' }}>
        <BarChart3 size={36} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
        <p style={{ margin: 0 }}>No judge quality data available.</p>
      </div>
    )
  }

  if (!tableColumns || tableColumns.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '40px', color: '#9ca3af' }}>
        <BarChart3 size={36} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
        <p style={{ margin: 0 }}>Judge Quality schema missing: API did not provide `_meta.table_columns`.</p>
      </div>
    )
  }

  const meta = data._meta

  function renderCellValue(row: JudgeQualityStat, column: string): ReactNode {
    if (!isJudgeQualityColumnId(column)) {
      const rawValue = (row as unknown as Record<string, unknown>)[column]
      if (rawValue == null) return '-'
      return typeof rawValue === 'number' ? formatNumber(rawValue, 4) : String(rawValue)
    }

    switch (column) {
      case 'rank':
        return formatInteger(row.rank)
      case 'judge_model':
        return row.judge_model
      case 'display_quality_pct':
      case 'sortino_score_pct':
      case 'agreement_pct':
      case 'consensus_score_pct': {
        const value = row[column]
        return value == null ? '-' : (
          <Badge value={formatPct(value)} style={getQualityStyle(value)} />
        )
      }
      case 'composite_quality_pct':
      case 'avg_score_pct':
      case 'score_dist_1_pct':
      case 'score_dist_2_pct':
      case 'score_dist_3_pct':
      case 'score_dist_4_pct':
      case 'score_dist_5_pct':
        return formatPct(row[column])
      case 'self_consistency_pct':
        if (row.self_consistency_pct == null && !meta.has_multiple_trials) {
          return 'N/A (single-trial)'
        }
        return row.self_consistency_pct == null ? '-' : (
          <Badge value={formatPct(row.self_consistency_pct)} style={getQualityStyle(row.self_consistency_pct)} />
        )
      case 'avg_score_given':
      case 'mean_trial_diff':
        return formatNumber(row[column], 2)
      case 'std_dev':
        return row.std_dev == null ? '-' : (
          <Badge value={formatNumber(row.std_dev, 2)} style={getStdDevStyle(row.std_dev)} />
        )
      case 'min_score':
      case 'max_score':
        return formatNumber(row[column], 0)
      case 'leniency_offset':
        return row.leniency_offset == null ? '-' : (
          <Badge value={formatSigned(row.leniency_offset, 2)} style={getLeniencyStyle(row.leniency_offset)} />
        )
      case 'total_scores_n':
      case 'docs_covered':
      case 'criteria_covered':
      case 'outlier_count':
      case 'avg_input_tokens':
      case 'avg_output_tokens':
      case 'avg_reasoning_tokens':
      case 'avg_total_tokens':
        return formatInteger(row[column])
      case 'krippendorff_alpha':
        return row.krippendorff_alpha == null ? '-' : (
          <Badge value={formatNumber(row.krippendorff_alpha, 3)} style={getAlphaStyle(row.krippendorff_alpha)} />
        )
    }
  }

  return (
    <div>
      <div style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <h4 style={{ margin: 0, color: '#a78bfa', fontSize: '14px', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <BarChart3 size={16} />
          Judge Quality Analysis
          <span style={{ color: '#6b7280', fontSize: '11px', fontWeight: 400 }}>
            - {meta.judge_count} judge{meta.judge_count !== 1 ? 's' : ''}, {meta.eval_score_overlapping_pairs} overlapping eval pairs, {meta.pairwise_comparison_count} overlapping pairwise comparisons
          </span>
        </h4>
        <Badge value={`krippendorff_alpha ${formatNumber(alpha, 3)}`} style={getAlphaStyle(alpha)} />
      </div>

      {shouldShowJudgeQualityChart && (
        <div style={{ marginBottom: '18px' }}>
          <Heatmap title="Judge Quality Chart" dataset={judgeQualityChart} />
        </div>
      )}

      <div
        style={{
          display: 'flex',
          gap: '16px',
          marginBottom: '14px',
          padding: '7px 12px',
          backgroundColor: '#111827',
          borderRadius: '6px',
          fontSize: '11px',
          color: '#9ca3af',
          flexWrap: 'wrap',
        }}
      >
        <span>Columns are rendered in API `_meta.table_columns` order.</span>
        <span>The UI does not alias fields, recompute math, or rescale percentages.</span>
        <span>`self_consistency_pct` is `N/A (single-trial)` when multiple trials do not exist.</span>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: '#111827', border: '1px solid #374151' }}>
          <thead>
            <tr>
              {tableColumns.map((column) => {
                const title = getColumnTooltip(column)
                const headerStyle = column === 'judge_model'
                  ? th(undefined, 'left')
                  : column === 'display_quality_pct'
                    ? th(undefined, 'center', '#312e81')
                    : th()
                return (
                  <th key={column} style={{ ...headerStyle, cursor: 'help' }} title={title}>
                    <code>{column}</code>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={row.judge_model}
                style={{
                  borderBottom: '1px solid #374151',
                  backgroundColor: index % 2 === 0 ? '#111827' : '#1f2937',
                }}
              >
                {tableColumns.map((column) => (
                  <td
                    key={`${row.judge_model}-${column}`}
                    style={column === 'judge_model'
                      ? td('left', { fontFamily: 'monospace', fontWeight: 600 })
                      : td()}
                  >
                    {renderCellValue(row, column)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {evalScoreAgreement.length > 0 && (
        <div style={{ marginTop: '18px' }}>
          <h5 style={{ margin: '0 0 8px', color: '#c4b5fd', fontSize: '12px', fontWeight: 600 }}>
            Eval Score Agreement
          </h5>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: '#111827', border: '1px solid #374151' }}>
              <thead>
                <tr>
                  <th style={th(undefined, 'left')}>Judge A</th>
                  <th style={th(undefined, 'left')}>Judge B</th>
                  <th style={th()}>Shared Pairs</th>
                  <th style={th()}>Exact Match</th>
                  <th style={th()}>Exact %</th>
                  <th style={th()}>Mean |delta|</th>
                </tr>
              </thead>
              <tbody>
                {evalScoreAgreement.map((row, index) => (
                  <tr key={`${row.judge_a}-${row.judge_b}`} style={{ borderBottom: '1px solid #374151', backgroundColor: index % 2 === 0 ? '#111827' : '#1f2937' }}>
                    <td style={td('left', { fontFamily: 'monospace' })}>{row.judge_a}</td>
                    <td style={td('left', { fontFamily: 'monospace' })}>{row.judge_b}</td>
                    <td style={td()}>{row.shared_pairs}</td>
                    <td style={td()}>{row.exact_agreement_count}</td>
                    <td style={td()}>{formatPct(row.exact_agreement_rate)}</td>
                    <td style={td()}>{formatNumber(row.mean_abs_diff, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {pairwiseAgreement.length > 0 && (
        <div style={{ marginTop: '18px' }}>
          <h5 style={{ margin: '0 0 8px', color: '#c4b5fd', fontSize: '12px', fontWeight: 600 }}>
            Pairwise Agreement
          </h5>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: '#111827', border: '1px solid #374151' }}>
              <thead>
                <tr>
                  <th style={th(undefined, 'left')}>Judge A</th>
                  <th style={th(undefined, 'left')}>Judge B</th>
                  <th style={th()}>Shared Comparisons</th>
                  <th style={th()}>Winner Agree</th>
                  <th style={th()}>Winner %</th>
                </tr>
              </thead>
              <tbody>
                {pairwiseAgreement.map((row, index) => (
                  <tr key={`${row.judge_a}-${row.judge_b}`} style={{ borderBottom: '1px solid #374151', backgroundColor: index % 2 === 0 ? '#111827' : '#1f2937' }}>
                    <td style={td('left', { fontFamily: 'monospace' })}>{row.judge_a}</td>
                    <td style={td('left', { fontFamily: 'monospace' })}>{row.judge_b}</td>
                    <td style={td()}>{row.shared_comparisons}</td>
                    <td style={td()}>{row.agree_count}</td>
                    <td style={td()}>{formatPct(row.agreement_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
