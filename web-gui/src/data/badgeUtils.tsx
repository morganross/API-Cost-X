/**
 * Shared badge rendering utilities for gen-model and judge quality badges.
 * Used in Presets.tsx and the per-panel config views (FpfParamsPanel, etc.).
 */
import React from 'react'
import { GEN_SCORE_ROWS } from './genModelScores'
import { JUDGE_QUALITY_SCORES } from './judgeQualityScores'

// ---------------------------------------------------------------------------
// Quantile helpers
// ---------------------------------------------------------------------------

type Quantiles = [number, number, number, number, number]

function pool(values: (number | null | undefined)[]): Quantiles | null {
  const sorted = values
    .filter((v): v is number => v != null && Number.isFinite(v))
    .sort((a, b) => a - b)
  if (sorted.length < 2) return null
  const q = (p: number) => {
    const pos = p * (sorted.length - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(sorted.length - 1, lo + 1)
    return sorted[lo] * (1 - (pos - lo)) + sorted[hi] * (pos - lo)
  }
  return [q(1 / 6), q(2 / 6), q(3 / 6), q(4 / 6), q(5 / 6)]
}

/**
 * Pre-computed quantile pools from all static score data.
 * All 3 gen types share genScore so colours are comparable across FPF,
 * GPTR, and DR badges on the same card / panel.
 */
export const GEN_BADGE_QUANTILES = (() => {
  const evalVals = Object.values(JUDGE_QUALITY_SCORES)
  return {
    genScore: pool(GEN_SCORE_ROWS.map(r => r.score)),
    evalScore: pool(evalVals.map(v => v.sortino)),
  }
})()

// ---------------------------------------------------------------------------
// Tier colouring
// ---------------------------------------------------------------------------

/**
 * Returns a Tailwind class for one of 6 tiers.
 * qs is [q1/6, q2/6, q3/6, q4/6, q5/6].
 * higherIsBetter=true → high values are green; false → low values are green.
 */
export function tierClass(
  value: number,
  qs: Quantiles | null,
  higherIsBetter: boolean
): string {
  if (!qs || !Number.isFinite(value)) return 'bg-gray-800 text-gray-300'
  let tier: number
  if (higherIsBetter) {
    if (value >= qs[4]) tier = 0
    else if (value >= qs[3]) tier = 1
    else if (value >= qs[2]) tier = 2
    else if (value >= qs[1]) tier = 3
    else if (value >= qs[0]) tier = 4
    else tier = 5
  } else {
    if (value <= qs[0]) tier = 0
    else if (value <= qs[1]) tier = 1
    else if (value <= qs[2]) tier = 2
    else if (value <= qs[3]) tier = 3
    else if (value <= qs[4]) tier = 4
    else tier = 5
  }
  const classes = [
    'bg-green-700 text-white',
    'bg-green-600/60 text-green-100',
    'bg-yellow-600/60 text-yellow-100',
    'bg-yellow-500/40 text-yellow-50',
    'bg-red-600/50 text-red-100',
    'bg-red-700 text-white',
  ]
  return classes[tier]
}

// ---------------------------------------------------------------------------
// Badge renderer
// ---------------------------------------------------------------------------

/**
 * Renders a single value badge.
 * - hasEntry=false → invisible spacer (for column alignment in preset cards)
 * - hasEntry=true, value=null → neutral gray with "—"
 * - fixedWidth: arbitrary Tailwind width class (e.g. 'w-[4rem]') that is the
 *   same across all cards so columns align vertically.
 */
export function renderBadge(
  label: string,
  value: number | null | undefined,
  qs: Quantiles | null,
  higherIsBetter: boolean,
  formatter: (v: number) => string,
  hasEntry: boolean,
  fixedWidth: string,
  key: string
): React.ReactNode {
  const baseClass = `inline-flex items-center justify-between shrink-0 px-1.5 py-0.5 rounded whitespace-nowrap font-mono ${fixedWidth}`
  if (!hasEntry) {
    return (
      <span key={key} className={`${baseClass} invisible`}>
        <span>{label}</span><span>—</span>
      </span>
    )
  }
  const cls = value != null ? tierClass(value, qs, higherIsBetter) : 'bg-gray-800 text-gray-400'
  return (
    <span
      key={key}
      className={`${baseClass} ${cls}`}
      title={value != null ? `${label}: ${formatter(value)}` : `${label}: not yet run`}
    >
      <span className="font-semibold">{label}</span>
      <span>{value != null ? formatter(value) : '—'}</span>
    </span>
  )
}
