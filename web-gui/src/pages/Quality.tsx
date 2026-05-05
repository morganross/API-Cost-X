import React from 'react'
import { ScoreTable, type ScoreColumn } from '../components/ui/ScoreTable'
import { Heatmap } from '../components/quality/Heatmap'
import { GEN_SCORE_ROWS, type GenScoreEntry, type GenScoreType } from '../data/genModelScores'
import { JUDGE_QUALITY_COMBINED_ROWS, JudgeQualityCombinedRow } from '../data/judgeQualityCombined'
import { buildScatterDataset, type ScatterPointShape } from '../data/qualityHeatmaps'

const HEATMAP_MIN_SCORE = 3.5

const fmt = (v: number | null, decimals: number, prefix = '') =>
  v == null ? '—' : `${prefix}${v.toFixed(decimals)}`

const fmtPct = (v: number | null, decimals: number) =>
  v == null ? '—' : `${v.toFixed(decimals)}%`

const hasScore = (row: GenScoreEntry) => row.score !== null

const sortByScore = (rows: GenScoreEntry[]) =>
  [...rows].sort((a, b) => (b.score ?? Number.NEGATIVE_INFINITY) - (a.score ?? Number.NEGATIVE_INFINITY))

const EXCLUDED_QUALITY_MODELS = new Set([
  'openrouter:mistralai/mistral-large-2411',
])

const visibleGenRows = GEN_SCORE_ROWS.filter(r => !EXCLUDED_QUALITY_MODELS.has(r.model) && hasScore(r))
const visibleJudgeRows = JUDGE_QUALITY_COMBINED_ROWS.filter(r => !EXCLUDED_QUALITY_MODELS.has(r.judgeModel))

const fpfRows = sortByScore(visibleGenRows.filter(r => r.type === 'fpf'))
const gptrRows = sortByScore(visibleGenRows.filter(r => r.type === 'gptr'))
const drRows = sortByScore(visibleGenRows.filter(r => r.type === 'dr'))
const aiqRows = sortByScore(visibleGenRows.filter(r => r.type === 'aiq'))

const allGenRows = sortByScore(visibleGenRows)

const filterHeatmapRows = (rows: GenScoreEntry[]) =>
  rows.filter(row => row.score !== null && row.score >= HEATMAP_MIN_SCORE)

const COMBINED_CHART_SHAPES: Record<GenScoreType, ScatterPointShape> = {
  fpf: 'circle',
  gptr: 'diamond',
  dr: 'triangle',
  aiq: 'square',
}

const allGenColumns = [
  {
    header: '#', align: 'center' as const,
    cell: (r: GenScoreEntry, idx: number) => r.score !== null
      ? <span className="text-gray-400">{idx + 1}</span>
      : <span className="text-gray-600">—</span>,
  },
  { header: 'Type', align: 'center' as const, cell: (r: GenScoreEntry) => <span className="uppercase text-xs font-mono text-gray-300">{r.type}</span> },
  { header: 'Model', cell: (r: GenScoreEntry) => r.model },
  {
    header: 'Avg Score', align: 'right' as const,
    cell: (r: GenScoreEntry) => r.score !== null
      ? fmt(r.score, 2)
      : <span className="text-gray-600 italic">not yet run</span>,
  },
]

const allGenHeatmap = buildScatterDataset({
  rows: filterHeatmapRows(allGenRows),
  rowKey: row => `${row.type}:${row.model}`,
  rowLabel: row => row.model,
  legend: 'All FPF, GPTR, DR, and AI-Q model results combined. Charts only show models scoring 3.5 or higher. X is rank, Y is average score. Colors show provider and shapes show report type.',
  pointShape: row => COMBINED_CHART_SHAPES[row.type],
  shapeLegend: [
    { label: 'FPF', shape: COMBINED_CHART_SHAPES.fpf },
    { label: 'GPTR', shape: COMBINED_CHART_SHAPES.gptr },
    { label: 'DR', shape: COMBINED_CHART_SHAPES.dr },
    { label: 'AI-Q', shape: COMBINED_CHART_SHAPES.aiq },
  ],
  xMetric: {
    key: 'rank',
    label: 'Rank',
    direction: 'lower_better',
    value: row => allGenRows.findIndex(candidate => candidate === row) + 1,
    format: value => fmt(value, 0),
  },
  yMetric: {
    key: 'score',
    label: 'Avg Score',
    direction: 'higher_better',
    value: row => row.score,
    format: value => fmt(value, 2),
  },
})

const genColumns = [
  {
    header: '#', align: 'center' as const,
    cell: (r: GenScoreEntry, idx: number) => r.score !== null
      ? <span className="text-gray-400">{idx + 1}</span>
      : <span className="text-gray-600">—</span>,
  },
  { header: 'Model', cell: (r: GenScoreEntry) => r.model },
  {
    header: 'Avg Score', align: 'right' as const,
    cell: (r: GenScoreEntry) => r.score !== null
      ? fmt(r.score, 2)
      : <span className="text-gray-600 italic">not yet run</span>,
  },
]

const judgeColumns = [
  { header: '#', align: 'center' as const, cell: (_: JudgeQualityCombinedRow, idx: number) => <span className="text-gray-400">{idx + 1}</span> },
  { header: 'Judge Model', cell: (r: JudgeQualityCombinedRow) => r.judgeModel },
  { header: 'Quality %', align: 'right' as const, cell: (r: JudgeQualityCombinedRow) => fmtPct(r.qualityPct, 1) },
  { header: 'Within 1pt', align: 'right' as const, cell: (r: JudgeQualityCombinedRow) => fmtPct(r.within1Pct, 1) },
  { header: 'Repeatability', align: 'right' as const, cell: (r: JudgeQualityCombinedRow) => fmtPct(r.repeatabilityPct, 1) },
  { header: 'Group Align', align: 'right' as const, cell: (r: JudgeQualityCombinedRow) => fmtPct(r.groupAlignPct, 1) },
  { header: 'α Reliability', align: 'right' as const, cell: (r: JudgeQualityCombinedRow) => fmt(r.alpha, 3) },
]

const buildGenHeatmap = (rows: GenScoreEntry[]) => buildScatterDataset({
  rows: filterHeatmapRows(rows),
  rowKey: row => row.model,
  rowLabel: row => row.model,
  legend: 'XY chart of model points scoring 3.5 or higher. X is rank and Y is average score. Purple indicates stronger average score.',
  xMetric: {
    key: 'rank',
    label: 'Rank',
    direction: 'lower_better',
    value: row => allGenRows.findIndex(candidate => candidate === row) + 1,
    format: value => fmt(value, 0),
  },
  yMetric: {
    key: 'score',
    label: 'Avg Score',
    direction: 'higher_better',
    value: row => row.score,
    format: value => fmt(value, 2),
  },
})

const judgeHeatmap = buildScatterDataset({
  rows: visibleJudgeRows,
  rowKey: row => row.judgeModel,
  rowLabel: row => row.judgeModel,
  legend: 'XY chart of judge-model points. X is rank and Y is judge quality. Purple indicates stronger quality.',
  xMetric: {
    key: 'rank',
    label: 'Rank',
    direction: 'lower_better',
    value: row => row.rank,
    format: value => fmt(value, 0),
  },
  yMetric: {
    key: 'qualityPct',
    label: 'Quality %',
    direction: 'higher_better',
    value: row => row.qualityPct,
    format: value => fmtPct(value, 1),
  },
})

const genSections = [
  {
    title: 'FilePromptForge (FPF) Performance',
    sourceLabel: 'genModelScores.ts',
    rows: fpfRows,
    heatmapTitle: 'FPF Performance Chart',
    heatmap: buildGenHeatmap(fpfRows),
  },
  {
    title: 'GPT-Researcher (GPTR) Performance',
    sourceLabel: 'genModelScores.ts',
    rows: gptrRows,
    heatmapTitle: 'GPTR Performance Chart',
    heatmap: buildGenHeatmap(gptrRows),
  },
  {
    title: 'Deep Research (DR) Performance',
    sourceLabel: 'genModelScores.ts',
    rows: drRows,
    heatmapTitle: 'DR Performance Chart',
    heatmap: buildGenHeatmap(drRows),
  },
  {
    title: 'AI-Q Performance',
    sourceLabel: 'genModelScores.ts',
    rows: aiqRows,
    heatmapTitle: 'AI-Q Performance Chart',
    heatmap: buildGenHeatmap(aiqRows),
  },
]

function CollapsibleScoreTable<T>({
  rows,
  rowKey,
  columns,
}: {
  rows: T[]
  rowKey: (row: T, idx: number) => string | number
  columns: ScoreColumn<T>[]
}) {
  const [expanded, setExpanded] = React.useState(false)
  const shouldCollapse = rows.length > 5

  return (
    <div className="space-y-3">
      <div className="relative">
        <div className={shouldCollapse && !expanded ? 'max-h-[20rem] overflow-hidden rounded-lg' : ''}>
          <ScoreTable rows={rows} rowKey={rowKey} columns={columns} />
        </div>
        {shouldCollapse && !expanded && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-28 rounded-b-lg bg-gradient-to-t from-gray-900 via-gray-900/95 to-transparent" />
        )}
      </div>
      {shouldCollapse && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="rounded-lg border border-gray-600 bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-200 transition-colors hover:bg-gray-700"
          >
            {expanded ? 'Collapse table' : 'Click to expand table'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function Quality() {
  return (
    <div className="mx-auto space-y-10 px-3 text-white sm:px-5 lg:px-6">
      <div className="space-y-4">
        <div className="max-w-7xl mx-auto">
          <div className="px-2 sm:px-3">
            <h1 className="text-3xl font-bold mb-1 text-white">All Generation Models — Combined</h1>
            <p className="text-sm text-gray-400 mb-4">
              FPF, GPTR, DR, and AI-Q results together, ranked by avg score. Source: <code className="font-mono text-xs bg-gray-700 px-1 py-0.5 rounded text-gray-200">genModelScores.ts</code>
            </p>
          </div>
        </div>
        <div className="max-w-7xl mx-auto">
          <Heatmap title="All Generation Models — Combined Chart" dataset={allGenHeatmap} />
        </div>
        <div className="max-w-7xl mx-auto">
          <CollapsibleScoreTable rows={allGenRows} rowKey={(_, idx) => idx} columns={allGenColumns} />
        </div>
      </div>

      {genSections.map(section => (
        <div key={section.title} className="space-y-4">
          <div className="max-w-7xl mx-auto">
            <div className="px-2 sm:px-3">
              <h1 className="text-3xl font-bold mb-1 text-white">{section.title}</h1>
              <p className="text-sm text-gray-400 mb-4">
                Ranked by avg score (1–5). Source: <code className="font-mono text-xs bg-gray-700 px-1 py-0.5 rounded text-gray-200">{section.sourceLabel}</code>
              </p>
            </div>
            <Heatmap title={section.heatmapTitle} dataset={section.heatmap} />
            <CollapsibleScoreTable rows={section.rows} rowKey={(_, idx) => idx} columns={genColumns} />
          </div>
        </div>
      ))}

      <div className="space-y-4">
        <div className="max-w-7xl mx-auto">
          <div className="px-2 sm:px-3">
            <h1 className="text-3xl font-bold mb-1 text-white">Judge Model Performance</h1>
            <p className="text-sm text-gray-400 mb-4">
              Averaged across last 3 runs. Source: <code className="font-mono text-xs bg-gray-700 px-1 py-0.5 rounded text-gray-200">judgeQualityCombined.ts</code>
            </p>
            <Heatmap title="Judge Model Performance Chart" dataset={judgeHeatmap} />
            <CollapsibleScoreTable rows={visibleJudgeRows} rowKey={(_, idx) => idx} columns={judgeColumns} />
          </div>
        </div>
      </div>
    </div>
  )
}
