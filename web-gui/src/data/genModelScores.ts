// Generation model scores (static)
// - Benchmarked rows: score filled from run data (source: run 04edb7b0 + later additions)
// - Unscored rows:   score: null  (model exists in catalog but no benchmark run yet)
// type: fpf | gptr | dr; model keys use provider:model (slashes preserved as-is)
// Rank is NOT stored — it is derived as position + 1 after sorting score desc, nulls last.

import { normalizeModelKey } from './modelKeyUtils'

export type GenScoreType = 'fpf' | 'gptr' | 'dr' | 'aiq'

export interface GenScoreEntry {
  type: GenScoreType
  model: string
  score: number | null
}

const GEN_SCORE_BASE_ROWS: GenScoreEntry[] = [
  // ── benchmarked rows, sorted by score descending ────────────────────────────
  { type: 'fpf',  model: 'openai:gpt-5.4', score: 4.85 },
  { type: 'fpf',  model: 'openai:gpt-5.2', score: 4.83 },
  { type: 'fpf',  model: 'openai:gpt-5.1', score: 4.68 },
  { type: 'fpf',  model: 'anthropic:claude-opus-4-6', score: 4.64 },
  { type: 'gptr', model: 'openai:gpt-5', score: 4.60 },
  { type: 'fpf',  model: 'openai:gpt-5', score: 4.59 },
  { type: 'fpf',  model: 'anthropic:claude-sonnet-4-6', score: 4.54 },
  { type: 'dr',   model: 'openai:gpt-5-mini', score: 4.46 },
  { type: 'fpf',  model: 'tavily:tvly-mini', score: 4.45 },
  { type: 'gptr', model: 'openai:gpt-5.2', score: 4.44 },
  { type: 'fpf',  model: 'openaidp:o4-mini-deep-research', score: 4.43 },
  { type: 'fpf',  model: 'anthropic:claude-sonnet-4-5', score: 4.39 },
  { type: 'gptr', model: 'openai:gpt-5-mini', score: 4.39 },
  { type: 'fpf',  model: 'openai:o4-mini', score: 4.35 },
  { type: 'fpf',  model: 'anthropic:claude-opus-4-5', score: 4.34 },
  { type: 'fpf',  model: 'anthropic:claude-haiku-4-5', score: 4.29 },
  { type: 'fpf',  model: 'tavily:tvly-pro', score: 4.29 },
  { type: 'dr',   model: 'openrouter:google/gemini-3-pro-preview', score: 4.23 },
  { type: 'fpf',  model: 'googledp:deep-research-pro-preview-12-2025', score: 4.20 },
  { type: 'gptr', model: 'openrouter:google/gemini-3-pro-preview', score: 4.17 },
  { type: 'dr',   model: 'openai:gpt-4.1', score: 4.17 },
  { type: 'fpf',  model: 'openai:gpt-5.4-nano', score: 4.13 },
  { type: 'gptr', model: 'openrouter:google/gemini-2.5-flash', score: 4.07 },
  { type: 'gptr', model: 'openrouter:google/gemini-3-flash-preview', score: 4.06 },
  { type: 'fpf',  model: 'openai:gpt-5.4-mini', score: 4.055 },
  { type: 'gptr', model: 'openai:gpt-4.1', score: 3.99 },
  { type: 'gptr', model: 'openrouter:deepseek/deepseek-r1', score: 3.99 },
  { type: 'gptr', model: 'openrouter:google/gemini-3.1-pro-preview', score: 3.99 },
  { type: 'dr',   model: 'openai:gpt-5.1', score: 3.92 },
  { type: 'fpf',  model: 'google:gemini-2.5-pro', score: 3.90 },
  { type: 'fpf',  model: 'openai:gpt-5-mini', score: 3.89 },
  { type: 'aiq',  model: 'openai:gpt-5.4-nano', score: 4.19 },
  { type: 'aiq',  model: 'openai:gpt-5.4-mini', score: 4.10 },
  { type: 'aiq',  model: 'openai:gpt-5-mini', score: 3.86 },
  { type: 'gptr', model: 'openrouter:google/gemini-2.5-pro', score: 3.87 },
  { type: 'gptr', model: 'openai:o4-mini', score: 3.78 },
  { type: 'fpf',  model: 'google:gemini-3-pro-preview', score: 3.77 },
  { type: 'dr',   model: 'openrouter:google/gemini-2.5-pro', score: 3.74 },
  { type: 'fpf',  model: 'google:gemini-3.1-pro-preview', score: 3.73 },
  { type: 'dr',   model: 'openrouter:openai/gpt-4o', score: 3.17 },
  { type: 'gptr', model: 'openrouter:meta-llama/llama-3.1-70b-instruct', score: 3.08 },
  { type: 'gptr', model: 'openrouter:openai/gpt-4o', score: 2.96 },
  { type: 'gptr', model: 'openrouter:openai/gpt-4o-mini', score: 2.76 },
  { type: 'fpf',  model: 'openrouter:mistralai/mistral-large-2411', score: 2.42 },

  // ── unscored — in catalog but not yet benchmarked ───────────────────────────
  { type: 'fpf',  model: 'google:gemini-2.5-flash', score: null },
  { type: 'fpf',  model: 'google:gemini-2.5-flash-lite', score: null },
  { type: 'fpf',  model: 'google:gemini-3-flash-preview', score: null },
  { type: 'gptr', model: 'google:gemini-3.1-pro-preview', score: null },
  { type: 'fpf',  model: 'openai:gpt-4.1-mini', score: null },
  { type: 'gptr', model: 'openai:gpt-4.1-mini', score: null },
  { type: 'dr',   model: 'openai:gpt-4.1-mini', score: null },
  { type: 'dr',   model: 'openai:gpt-5', score: null },
  { type: 'fpf',  model: 'openai:gpt-5-nano', score: null },
  { type: 'gptr', model: 'openai:gpt-5.1', score: null },
  { type: 'dr',   model: 'openai:gpt-5.2', score: null },
  { type: 'fpf',  model: 'openai:o3', score: null },
  { type: 'gptr', model: 'openai:o3', score: null },
  { type: 'dr',   model: 'openai:o3', score: null },
  { type: 'dr',   model: 'openai:o4-mini', score: null },
  { type: 'fpf',  model: 'openaidp:o3-deep-research', score: null },
  { type: 'fpf',  model: 'openrouter:deepseek/deepseek-r1', score: null },
  { type: 'dr',   model: 'openrouter:deepseek/deepseek-r1', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-2.5-flash', score: null },
  { type: 'dr',   model: 'openrouter:google/gemini-2.5-flash', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-2.5-flash-lite', score: null },
  { type: 'gptr', model: 'openrouter:google/gemini-2.5-flash-lite', score: null },
  { type: 'dr',   model: 'openrouter:google/gemini-2.5-flash-lite', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-2.5-pro', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-3-flash-preview', score: null },
  { type: 'dr',   model: 'openrouter:google/gemini-3-flash-preview', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-3-pro-preview', score: null },
  { type: 'fpf',  model: 'openrouter:google/gemini-3.1-pro-preview', score: null },
  { type: 'dr',   model: 'openrouter:google/gemini-3.1-pro-preview', score: null },
  { type: 'fpf',  model: 'openrouter:meta-llama/llama-3.1-405b-instruct', score: null },
  { type: 'gptr', model: 'openrouter:meta-llama/llama-3.1-405b-instruct', score: null },
  { type: 'dr',   model: 'openrouter:meta-llama/llama-3.1-405b-instruct', score: null },
  { type: 'fpf',  model: 'openrouter:meta-llama/llama-3.1-70b-instruct', score: null },
  { type: 'dr',   model: 'openrouter:meta-llama/llama-3.1-70b-instruct', score: null },
  { type: 'gptr', model: 'openrouter:mistralai/mistral-large-2411', score: null },
  { type: 'dr',   model: 'openrouter:mistralai/mistral-large-2411', score: null },
  { type: 'fpf',  model: 'openrouter:mistralai/mistral-small-3.1-24b-instruct', score: null },
  { type: 'gptr', model: 'openrouter:mistralai/mistral-small-3.1-24b-instruct', score: null },
  { type: 'dr',   model: 'openrouter:mistralai/mistral-small-3.1-24b-instruct', score: null },
  { type: 'dr',   model: 'openrouter:openai/gpt-4o-mini', score: null },
]

export const GEN_SCORE_ROWS: GenScoreEntry[] = GEN_SCORE_BASE_ROWS

const GEN_SCORE_LOOKUP: Record<GenScoreType, Record<string, GenScoreEntry>> = {
  fpf: {},
  gptr: {},
  dr: {},
  aiq: {},
}

for (const row of GEN_SCORE_ROWS) {
  for (const k of normalizeModelKey(row.model)) {
    GEN_SCORE_LOOKUP[row.type][k] = row
  }
}

export function getGenScore(type: GenScoreType, modelKey: string): GenScoreEntry | undefined {
  if (!modelKey) return undefined
  for (const k of normalizeModelKey(modelKey)) {
    const hit = GEN_SCORE_LOOKUP[type][k]
    if (hit) return hit
  }
  return undefined
}

export function getAiqScore(modelKey: string): GenScoreEntry | undefined {
  return getGenScore('aiq', modelKey)
}
