import { getGenScore } from '@/data/genModelScores'

export type ReportType = 'fpf' | 'gptr' | 'dr'
export type SelectorRow = { provider: string; model: string }
export type GenerationRow = { reportType: ReportType; provider: string; model: string }

export function splitModelKey(modelKey: string): { provider: string; model: string } {
  const [provider, ...rest] = modelKey.split(':')
  return {
    provider: provider || '',
    model: rest.join(':'),
  }
}

export function groupModelsByProvider(models: string[]): Record<string, string[]> {
  return models.reduce<Record<string, string[]>>((acc, modelKey) => {
    const { provider } = splitModelKey(modelKey)
    if (!provider) return acc
    if (!acc[provider]) acc[provider] = []
    acc[provider].push(modelKey)
    return acc
  }, {})
}

export function providerLabel(provider: string): string {
  return provider.replace(/[-_]/g, ' ')
}

export function modelLabel(modelKey: string): string {
  return splitModelKey(modelKey).model || modelKey
}

export function truncateLabel(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength - 1)}…`
}

export function formatPercent(value?: number): string {
  if (value == null) return '--%'
  return `${Math.round((value / 5) * 100)}%`
}

export function generationModelOptionLabel(reportType: ReportType, modelKey: string): string {
  const metrics = getGenScore(reportType, modelKey)
  const name = truncateLabel(modelLabel(modelKey), 34)
  const score = formatPercent(metrics?.score ?? undefined).padStart(4, ' ')
  return `${name.padEnd(36, ' ')}${score}`
}

export function sectionRowsFromModels(models: string[]): SelectorRow[] {
  if (models.length === 0) return [{ provider: '', model: '' }]
  return models.map((modelKey) => ({
    provider: splitModelKey(modelKey).provider,
    model: modelKey,
  }))
}

export function generationRowsFromModels(
  fpfModels: string[],
  gptrModels: string[],
  drModels: string[]
): GenerationRow[] {
  const rows = [
    ...fpfModels.map((model) => ({ reportType: 'fpf' as const, provider: splitModelKey(model).provider, model })),
    ...gptrModels.map((model) => ({ reportType: 'gptr' as const, provider: splitModelKey(model).provider, model })),
    ...drModels.map((model) => ({ reportType: 'dr' as const, provider: splitModelKey(model).provider, model })),
  ]

  return rows.length > 0 ? rows : [{ reportType: 'fpf', provider: '', model: '' }]
}
