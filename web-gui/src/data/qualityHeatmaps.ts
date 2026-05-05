export type HeatmapDirection = 'higher_better' | 'lower_better'
export type ScatterPointShape = 'circle' | 'diamond' | 'triangle' | 'square'

type ScatterRawValue = number | string | null | undefined

export interface ScatterMetric<Row> {
  key: string
  label: string
  direction: HeatmapDirection
  value: (row: Row) => ScatterRawValue
  format?: (value: number | null, raw: ScatterRawValue) => string
}

export interface ScatterPoint {
  key: string
  label: string
  x: number
  y: number
  xDisplay: string
  yDisplay: string
  toneValue: number
  shape?: ScatterPointShape
}

export interface ScatterShapeLegendEntry {
  label: string
  shape: ScatterPointShape
}

export interface ScatterDomainOptions {
  min?: number
  max?: number
  padRatio?: number
  minPad?: number
}

export interface ScatterDataset {
  points: ScatterPoint[]
  xLabel: string
  yLabel: string
  xDirection: HeatmapDirection
  yDirection: HeatmapDirection
  legend: string
  hiddenCount: number
  shapeLegend?: ScatterShapeLegendEntry[]
  xDomain?: ScatterDomainOptions
  yDomain?: ScatterDomainOptions
}

interface BuildScatterDatasetArgs<Row> {
  rows: Row[]
  rowKey: (row: Row, idx: number) => string
  rowLabel: (row: Row, idx: number) => string
  xMetric: ScatterMetric<Row>
  yMetric: ScatterMetric<Row>
  legend: string
  pointShape?: (row: Row, idx: number) => ScatterPointShape | undefined
  shapeLegend?: ScatterShapeLegendEntry[]
  xDomain?: ScatterDomainOptions
  yDomain?: ScatterDomainOptions
}

export function parseHeatmapNumber(raw: ScatterRawValue): number | null {
  if (raw == null) return null
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null

  const trimmed = raw.trim()
  if (!trimmed || trimmed === '—') return null
  if (/^free$/i.test(trimmed)) return 0

  const normalized = trimmed
    .replace(/\$/g, '')
    .replace(/%/g, '')
    .replace(/,/g, '')
    .replace(/\/1M$/i, '')
    .trim()

  const parsed = Number.parseFloat(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function defaultDisplay(value: number | null, raw: ScatterRawValue): string {
  if (typeof raw === 'string') return raw
  if (value == null) return '—'
  return `${value}`
}

export function buildScatterDataset<Row>({
  rows,
  rowKey,
  rowLabel,
  xMetric,
  yMetric,
  legend,
  pointShape,
  shapeLegend,
  xDomain,
  yDomain,
}: BuildScatterDatasetArgs<Row>): ScatterDataset {
  const points: ScatterPoint[] = []
  let hiddenCount = 0

  rows.forEach((row, idx) => {
    const rawX = xMetric.value(row)
    const rawY = yMetric.value(row)
    const x = parseHeatmapNumber(rawX)
    const y = parseHeatmapNumber(rawY)

    if (x == null || y == null) {
      hiddenCount += 1
      return
    }

    points.push({
      key: rowKey(row, idx),
      label: rowLabel(row, idx),
      x,
      y,
      xDisplay: xMetric.format ? xMetric.format(x, rawX) : defaultDisplay(x, rawX),
      yDisplay: yMetric.format ? yMetric.format(y, rawY) : defaultDisplay(y, rawY),
      toneValue: y,
      shape: pointShape?.(row, idx),
    })
  })

  return {
    points,
    xLabel: xMetric.label,
    yLabel: yMetric.label,
    xDirection: xMetric.direction,
    yDirection: yMetric.direction,
    legend,
    hiddenCount,
    shapeLegend,
    xDomain,
    yDomain,
  }
}

function quantile(values: number[], p: number): number {
  const pos = p * (values.length - 1)
  const lo = Math.floor(pos)
  const hi = Math.min(values.length - 1, lo + 1)
  const t = pos - lo
  return values[lo] * (1 - t) + values[hi] * t
}

export function getToneThresholds(values: number[]): number[] | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b)
  if (sorted.length < 2 || sorted[0] === sorted[sorted.length - 1]) return null
  return [1, 2, 3, 4, 5, 6].map(step => quantile(sorted, step / 7))
}

export function getToneBucket(value: number, thresholds: number[] | null, direction: HeatmapDirection): number {
  if (!thresholds) return 3

  let bucket = thresholds.findIndex(threshold => value <= threshold)
  if (bucket === -1) bucket = thresholds.length

  if (direction === 'higher_better') return 6 - bucket
  return bucket
}

export const IR_HEATMAP_BUCKET_CLASSES = [
  { fill: '#6a51a3', stroke: '#4c3c7c', text: '#ffffff' },
  { fill: '#8073ac', stroke: '#5e5481', text: '#ffffff' },
  { fill: '#9e9ac8', stroke: '#7e7aaa', text: '#160f2a' },
  { fill: '#c7b9d8', stroke: '#a99ac0', text: '#2b1938' },
  { fill: '#d9a8b8', stroke: '#ba8396', text: '#311724' },
  { fill: '#f1c27d', stroke: '#d9a45a', text: '#4a2b12' },
  { fill: '#fff3b0', stroke: '#e1cf6c', text: '#5a470f' },
] as const
