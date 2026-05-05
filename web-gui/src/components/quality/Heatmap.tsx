import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  HeatmapDirection,
  ScatterDataset,
  ScatterDomainOptions,
  ScatterPointShape,
} from '../../data/qualityHeatmaps'

const PROVIDER_COLORS: Record<string, { fill: string; stroke: string; text: string }> = {
  openai:      { fill: '#3b82f6', stroke: '#2563eb', text: '#ffffff' },
  anthropic:   { fill: '#f97316', stroke: '#ea580c', text: '#ffffff' },
  google:      { fill: '#22c55e', stroke: '#16a34a', text: '#ffffff' },
  openrouter:  { fill: '#a855f7', stroke: '#9333ea', text: '#ffffff' },
  meta:        { fill: '#ef4444', stroke: '#dc2626', text: '#ffffff' },
  mistral:     { fill: '#14b8a6', stroke: '#0d9488', text: '#ffffff' },
  xai:         { fill: '#eab308', stroke: '#ca8a04', text: '#1a1a00' },
  deepseek:    { fill: '#06b6d4', stroke: '#0891b2', text: '#ffffff' },
  perplexity:  { fill: '#8b5cf6', stroke: '#7c3aed', text: '#ffffff' },
  tavily:      { fill: '#ec4899', stroke: '#db2777', text: '#ffffff' },
}
const FALLBACK_COLOR = { fill: '#9ca3af', stroke: '#6b7280', text: '#ffffff' }
const SCORE_CHART_MIN = 3.5
const SCORE_CHART_MAX = 5

function stripLeadingClaudeToken(label: string): string {
  return label.replace(/^claude-/i, '')
}

function stripProviderFromLabel(label: string): string {
  // Handle "model [TYPE]" suffix (e.g. "openai/gpt-4o [FPF]")
  const typeMatch = label.match(/^(.*?)(\s+\[[^\]]+\])$/)
  if (typeMatch) {
    return stripProviderFromLabel(typeMatch[1]) + typeMatch[2]
  }
  // Strip everything up to and including the last '/'
  const slashIdx = label.lastIndexOf('/')
  if (slashIdx !== -1) return stripLeadingClaudeToken(label.slice(slashIdx + 1))
  // Strip routing prefix before ':'
  const colonIdx = label.indexOf(':')
  if (colonIdx !== -1) return stripLeadingClaudeToken(label.slice(colonIdx + 1))
  return stripLeadingClaudeToken(label)
}

function extractProvider(label: string): string {
  const colonIdx = label.indexOf(':')
  if (colonIdx === -1) return label.toLowerCase()
  const routing = label.slice(0, colonIdx).toLowerCase()
  const rest = label.slice(colonIdx + 1)

  if (routing === 'openrouter') {
    const org = rest.split('/')[0].toLowerCase()
    if (org.startsWith('mistral')) return 'mistral'
    if (org.startsWith('meta')) return 'meta'
    if (org.startsWith('google')) return 'google'
    if (org.startsWith('deepseek')) return 'deepseek'
    if (org.startsWith('perplexity')) return 'perplexity'
    if (org.startsWith('xai') || org.startsWith('x-ai')) return 'xai'
    return org
  }
  // handle variants like "openaidp", "googledp"
  if (routing.startsWith('openai')) return 'openai'
  if (routing.startsWith('google')) return 'google'
  return routing
}

function getProviderColor(label: string) {
  return PROVIDER_COLORS[extractProvider(label)] ?? FALLBACK_COLOR
}

function renderScatterSymbol(
  shape: ScatterPointShape | undefined,
  cx: number,
  cy: number,
  size: number,
  fill: string,
  stroke: string,
  strokeWidth: number | string
) {
  switch (shape ?? 'circle') {
    case 'diamond': {
      const radius = size * 1.1
      return (
        <polygon
          points={`${cx},${cy - radius} ${cx + radius},${cy} ${cx},${cy + radius} ${cx - radius},${cy}`}
          fill={fill}
          stroke={stroke}
          strokeWidth={strokeWidth}
          strokeLinejoin="round"
        />
      )
    }
    case 'triangle': {
      const halfWidth = size * 1.08
      const height = size * 1.95
      return (
        <polygon
          points={`${cx},${cy - height / 2} ${cx + halfWidth},${cy + height / 2} ${cx - halfWidth},${cy + height / 2}`}
          fill={fill}
          stroke={stroke}
          strokeWidth={strokeWidth}
          strokeLinejoin="round"
        />
      )
    }
    case 'square': {
      const side = size * 1.8
      return (
        <rect
          x={cx - side / 2}
          y={cy - side / 2}
          width={side}
          height={side}
          fill={fill}
          stroke={stroke}
          strokeWidth={strokeWidth}
          rx={1}
        />
      )
    }
    case 'circle':
    default:
      return <circle cx={cx} cy={cy} r={size} fill={fill} stroke={stroke} strokeWidth={strokeWidth} />
  }
}

interface HeatmapProps {
  title: string
  dataset: ScatterDataset
}

const MOBILE_MIN_CHART_WIDTH = 220
const DEFAULT_CHART_WIDTH = 760

function formatAxisTick(value: number, direction: HeatmapDirection, isMoney: boolean): string {
  if (isMoney) return `$${value.toFixed(value < 1 ? 3 : 2)}`
  if (direction === 'higher_better') return value.toFixed(1)
  return value.toFixed(3)
}

function getPaddedDomain(
  values: number[],
  options: ScatterDomainOptions = {}
): { min: number; max: number; center: number } {
  const { min: hardMin, max: hardMax, padRatio = 0.06, minPad = 0.001 } = options

  if (values.length === 0) {
    const min = hardMin ?? 0
    const max = hardMax ?? Math.max(min + 1, 1)
    return { min, max, center: (min + max) / 2 }
  }

  let min = Math.min(...values)
  let max = Math.max(...values)
  const span = max - min
  const pad = span === 0
    ? Math.max(Math.abs(max || 1) * 0.08, minPad)
    : Math.max(span * padRatio, minPad)

  min -= pad
  max += pad

  if (hardMin !== undefined) {
    min = Math.max(min, hardMin)
  }

  if (hardMax !== undefined) {
    max = Math.min(max, hardMax)
  }

  if (max <= min) {
    const fallbackSpan = Math.max(span, minPad * 10, 0.1)
    max = min + fallbackSpan

    if (hardMax !== undefined && max > hardMax) {
      max = hardMax
      min = hardMin !== undefined ? Math.max(hardMin, max - fallbackSpan) : max - fallbackSpan
    }
  }

  return {
    min,
    max,
    center: (min + max) / 2,
  }
}

export function Heatmap({ title, dataset }: HeatmapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartWidth, setChartWidth] = useState(DEFAULT_CHART_WIDTH)
  const isCompact = chartWidth < 640
  const width = chartWidth
  const height = isCompact
    ? Math.max(Math.round(width * 0.82), 300)
    : Math.max(Math.round(width * 0.5), 460)
  const margin = isCompact
    ? { top: 24, right: 12, bottom: 60, left: 42 }
    : { top: 32, right: 72, bottom: 72, left: 64 }
  const innerWidth = width - margin.left - margin.right
  const innerHeight = height - margin.top - margin.bottom
  const tickFontSize = isCompact ? 10 : 12
  const axisLabelFontSize = isCompact ? 12 : 13
  const pointRadius = isCompact ? 7 : 8.5
  const pointLabelFontSize = isCompact ? 10 : 11.5
  const xValues = dataset.points.map(point => point.x)
  const yValues = dataset.points.map(point => point.y)
  const isCurrencyX = dataset.xLabel.includes('$')
  const isCurrencyY = dataset.yLabel.includes('$')
  const isScoreY = dataset.yLabel.toLowerCase().includes('score') && !dataset.yLabel.includes('%')
  const xDomain = useMemo(
    () => getPaddedDomain(
      xValues,
      dataset.xDomain ?? {
        padRatio: isCurrencyX ? 0.05 : 0.06,
        minPad: isCurrencyX ? 0.0002 : 0.05,
      }
    ),
    [dataset.xDomain, isCurrencyX, xValues]
  )
  const yDomain = useMemo(
    () => getPaddedDomain(
      yValues,
      dataset.yDomain ?? (
        isScoreY
          ? { min: SCORE_CHART_MIN, max: SCORE_CHART_MAX, padRatio: 0.04, minPad: 0.04 }
          : { padRatio: 0.06, minPad: isCurrencyY ? 0.0002 : 0.5 }
      )
    ),
    [dataset.yDomain, isCurrencyY, isScoreY, yValues]
  )

  const providerLegend = useMemo(() => {
    const seen = new Map<string, { fill: string; stroke: string }>()
    dataset.points.forEach(point => {
      const provider = extractProvider(point.label)
      if (!seen.has(provider)) {
        const color = PROVIDER_COLORS[provider] ?? FALLBACK_COLOR
        seen.set(provider, color)
      }
    })
    return Array.from(seen.entries())
  }, [dataset.points])

  useEffect(() => {
    const node = containerRef.current
    if (!node) return

    const updateSize = () => {
      const styles = window.getComputedStyle(node)
      const horizontalPadding = Number.parseFloat(styles.paddingLeft || '0') + Number.parseFloat(styles.paddingRight || '0')
      const nextWidth = Math.max(Math.floor(node.clientWidth - horizontalPadding), MOBILE_MIN_CHART_WIDTH)
      setChartWidth(nextWidth)
    }

    updateSize()

    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => updateSize())
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  const xScale = (value: number) => {
    const ratio = (value - xDomain.min) / (xDomain.max - xDomain.min || 1)
    return margin.left + ratio * innerWidth
  }

  const yScale = (value: number) => {
    const ratio = (value - yDomain.min) / (yDomain.max - yDomain.min || 1)
    return margin.top + innerHeight - ratio * innerHeight
  }

  const xTicks = Array.from({ length: 5 }, (_, idx) => xDomain.min + ((xDomain.max - xDomain.min) / 4) * idx)
  const yTicks = Array.from({ length: 5 }, (_, idx) => yDomain.min + ((yDomain.max - yDomain.min) / 4) * idx)

  return (
    <div className="space-y-3">
      <div className="px-2 sm:px-3">
        <h2 className="text-xl font-semibold text-white">{title}</h2>
        <p className="mt-1 text-xs text-gray-400">{dataset.legend}</p>
        <p className="mt-1 text-xs text-gray-500">
          Dotted crosshairs mark the center of the plotted range on each axis. Purple points rank better on the Y metric.
        </p>
        {dataset.hiddenCount > 0 && (
          <p className="mt-1 text-xs text-gray-500">
            {dataset.hiddenCount} model{dataset.hiddenCount === 1 ? '' : 's'} omitted from the chart because one plotted metric is missing.
          </p>
        )}
      </div>

      <div
        ref={containerRef}
        className={`rounded-lg border border-gray-700 bg-gray-800 p-3 sm:p-4 ${isCompact ? 'overflow-hidden' : 'overflow-x-auto'}`}
      >
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block">
          <rect x="0" y="0" width={width} height={height} fill="#111827" rx="12" />

          {yTicks.map(tick => {
            const y = yScale(tick)
            return (
              <g key={`y-${tick}`}>
                <line x1={margin.left} x2={margin.left + innerWidth} y1={y} y2={y} stroke="#374151" strokeDasharray="4 6" />
                <text x={margin.left - 10} y={y + 4} textAnchor="end" fill="#9ca3af" fontSize={tickFontSize}>
                  {formatAxisTick(tick, dataset.yDirection, isCurrencyY)}
                </text>
              </g>
            )
          })}

          {xTicks.map(tick => {
            const x = xScale(tick)
            return (
              <g key={`x-${tick}`}>
                <line x1={x} x2={x} y1={margin.top} y2={margin.top + innerHeight} stroke="#374151" strokeDasharray="4 6" />
                <text x={x} y={margin.top + innerHeight + (isCompact ? 22 : 28)} textAnchor="middle" fill="#9ca3af" fontSize={tickFontSize}>
                  {formatAxisTick(tick, dataset.xDirection, isCurrencyX)}
                </text>
              </g>
            )
          })}

          <line x1={margin.left} x2={margin.left} y1={margin.top} y2={margin.top + innerHeight} stroke="#d1d5db" strokeWidth="1.2" />
          <line x1={margin.left} x2={margin.left + innerWidth} y1={margin.top + innerHeight} y2={margin.top + innerHeight} stroke="#d1d5db" strokeWidth="1.2" />

          <line
            x1={xScale(xDomain.center)}
            x2={xScale(xDomain.center)}
            y1={margin.top}
            y2={margin.top + innerHeight}
            stroke="#94a3b8"
            strokeDasharray="2 6"
            opacity="0.7"
          />
          <line
            x1={margin.left}
            x2={margin.left + innerWidth}
            y1={yScale(yDomain.center)}
            y2={yScale(yDomain.center)}
            stroke="#94a3b8"
            strokeDasharray="2 6"
            opacity="0.7"
          />

          {dataset.points.map((point, idx) => {
            const cx = xScale(point.x)
            const cy = yScale(point.y)
            const color = getProviderColor(point.label)
            const rawLabel = stripProviderFromLabel(point.label)
            const label = isCompact && rawLabel.length > 16 ? `${rawLabel.slice(0, 16)}…` : rawLabel
            const labelAnchor = isCompact && cx > margin.left + innerWidth * 0.68 ? 'end' : 'start'
            const labelX = labelAnchor === 'end' ? cx - 8 : cx + 8
            let labelY = idx % 2 === 0 ? cy - (isCompact ? 10 : 12) : cy + (isCompact ? 18 : 22)

            if (isCompact && labelY < margin.top + 12) labelY = cy + 18
            if (isCompact && labelY > margin.top + innerHeight - 4) labelY = cy - 10

            return (
              <g key={point.key}>
                {renderScatterSymbol(point.shape, cx, cy, pointRadius, color.fill, color.stroke, '1.75')}
                <text x={labelX} y={labelY} textAnchor={labelAnchor} fill="#f3f4f6" fontSize={pointLabelFontSize} fontFamily="monospace">
                  {label}
                </text>
                <title>{`${point.label} | ${dataset.xLabel}: ${point.xDisplay} | ${dataset.yLabel}: ${point.yDisplay}`}</title>
              </g>
            )
          })}

          <text
            x={margin.left + innerWidth / 2}
            y={height - 24}
            textAnchor="middle"
            fill="#e5e7eb"
            fontSize={axisLabelFontSize}
            fontWeight="600"
          >
            {dataset.xLabel}
          </text>
          <text
            x={20}
            y={margin.top + innerHeight / 2}
            textAnchor="middle"
            fill="#e5e7eb"
            fontSize={axisLabelFontSize}
            fontWeight="600"
            transform={`rotate(-90 20 ${margin.top + innerHeight / 2})`}
          >
            {dataset.yLabel}
          </text>
        </svg>
      </div>

      <div className="rounded-lg bg-gray-800/70 p-3 border border-gray-700 text-xs text-gray-300">
        <span className="font-semibold text-white">Legend:</span>{' '}
        <span className="text-gray-400">dots colored by provider —</span>{' '}
        {providerLegend.map(([provider, color]) => (
          <span key={provider} className="inline-flex items-center gap-1 mr-3">
            <svg width="10" height="10" className="inline-block flex-shrink-0">
              {renderScatterSymbol('circle', 5, 5, 4.5, color.fill, color.stroke, 1)}
            </svg>
            <span>{provider}</span>
          </span>
        ))}
        {dataset.shapeLegend?.length ? (
          <>
            <span className="text-gray-400"> shapes show report type —</span>{' '}
            {dataset.shapeLegend.map((entry) => (
              <span key={entry.label} className="inline-flex items-center gap-1 mr-3">
                <svg width="10" height="10" className="inline-block flex-shrink-0 overflow-visible">
                  {renderScatterSymbol(entry.shape, 5, 5, 4.5, '#e5e7eb', '#cbd5e1', 1)}
                </svg>
                <span>{entry.label}</span>
              </span>
            ))}
          </>
        ) : null}
        <span className="text-gray-500 ml-2">Dotted crosshairs mark the center of the plotted range.</span>
      </div>
    </div>
  )
}
