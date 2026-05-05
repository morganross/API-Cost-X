/**
 * Judge Quality Scores (Combined / Averaged)
 *
 * This module contains NO hardcoded scores.
 * It is derived from src/data/judgeQualityCombined.ts (generated from the combined spreadsheet export).
 *
 * Key format: provider:model (e.g. openai:gpt-5.1).
 */

import { JUDGE_QUALITY_COMBINED_ROWS } from './judgeQualityCombined'
import { normalizeModelKey } from './modelKeyUtils'

export interface JudgeQualityEntry {
  model: string
  sortino: number
  agreement: number
  consensus: number
  selfConsistency: number
  avgScore: number
  rank: number
}

function pctToUnit(pct: number | null | undefined): number {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return 0
  return pct / 100
}

export const JUDGE_QUALITY_SCORES: Record<string, JudgeQualityEntry> = Object.fromEntries(
  JUDGE_QUALITY_COMBINED_ROWS.map(r => {
    const entry: JudgeQualityEntry = {
      model: r.judgeModel,
      sortino: pctToUnit(r.qualityPct),
      agreement: pctToUnit(r.within1Pct),
      consensus: pctToUnit(r.groupAlignPct),
      selfConsistency: pctToUnit(r.repeatabilityPct),
      avgScore: r.avgGiven ?? 0,
      rank: r.rank,
    }
    return [r.judgeModel, entry]
  })
)

/**
 * Look up judge quality entry for a model.
 * The model key in the catalog uses provider/model format (e.g. openai/gpt-5.1)
 * but evaluator_list uses provider:model format (e.g. openai:gpt-5.1).
 */
export function getJudgeQuality(modelKey: string): JudgeQualityEntry | undefined {
  for (const k of normalizeModelKey(modelKey)) {
    if (JUDGE_QUALITY_SCORES[k]) return JUDGE_QUALITY_SCORES[k]
  }
  return undefined
}

export function getSortedJudgeScores(): JudgeQualityEntry[] {
  return Object.values(JUDGE_QUALITY_SCORES).sort((a, b) => a.rank - b.rank)
}

export function getSortinoColor(score: number): { bg: string; text: string; dot: string } {
  if (score >= 0.85) return { bg: '#166534', text: '#86efac', dot: '🟢' }
  if (score >= 0.70) return { bg: '#854d0e', text: '#fef08a', dot: '🟡' }
  return { bg: '#991b1b', text: '#fca5a5', dot: '🔴' }
}
