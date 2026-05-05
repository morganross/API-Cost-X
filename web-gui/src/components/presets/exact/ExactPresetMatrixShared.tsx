import { useMemo, type Dispatch, type SetStateAction } from 'react'
import { Check } from 'lucide-react'
import { getGenScore, type GenScoreType } from '@/data/genModelScores'
import { GEN_BADGE_QUANTILES, tierClass } from '@/data/badgeUtils'
import { getJudgeQuality } from '@/data/judgeQualityScores'
import { useConfigStore } from '@/stores/config'
import { useModelCatalog } from '@/stores/modelCatalog'
import { cn } from '@/lib/utils'

export type MatrixSection = 'fpf' | 'gptr' | 'dr' | 'eval' | 'combine'
export type MetricReportType = GenScoreType | 'aiq'

export const METRIC_REPORT_LABELS: Record<MetricReportType, string> = {
  fpf: 'FPF',
  gptr: 'GPT-R',
  dr: 'DR',
  aiq: 'AI-Q',
}

export const METRIC_REPORT_OPTIONS: Array<{ value: MetricReportType; label: string }> = [
  { value: 'fpf', label: 'FPF' },
  { value: 'gptr', label: 'GPT-R' },
  { value: 'dr', label: 'DR' },
  { value: 'aiq', label: 'AI-Q' },
]

export type ProviderFilterKey = 'openai' | 'google' | 'openrouter' | 'perplexity' | 'tavily' | 'anthropic' | 'nvidia' | 'others'

export const PROVIDER_FILTER_ORDER: ProviderFilterKey[] = [
  'openai',
  'google',
  'openrouter',
  'perplexity',
  'tavily',
  'anthropic',
  'nvidia',
  'others',
]

export const PROVIDER_FILTER_LABELS: Record<ProviderFilterKey, string> = {
  openai: 'openai',
  google: 'google',
  openrouter: 'open router',
  perplexity: 'perplexity',
  tavily: 'tavily',
  anthropic: 'anthropic',
  nvidia: 'nvidia',
  others: 'OTHERS',
}

export function extractProviderFilterKey(modelKey: string): ProviderFilterKey {
  const colonIdx = modelKey.indexOf(':')
  if (colonIdx === -1) return 'others'
  const prefix = modelKey.slice(0, colonIdx)
  const rest = modelKey.slice(colonIdx + 1)

  if (prefix === 'openai' || prefix === 'openaidp') return 'openai'
  if (prefix === 'google' || prefix === 'googledp') return 'google'
  if (prefix === 'anthropic') return 'anthropic'
  if (prefix === 'nvidia') return 'nvidia'
  if (prefix === 'tavily') return 'tavily'
  if (prefix === 'perplexity') return 'perplexity'
  if (prefix === 'openrouter') {
    if (rest.startsWith('perplexity/')) return 'perplexity'
    return 'openrouter'
  }

  return 'others'
}

export function extractKeyProvider(modelKey: string): string {
  const colonIdx = modelKey.indexOf(':')
  if (colonIdx === -1) return modelKey
  const prefix = modelKey.slice(0, colonIdx)
  if (prefix === 'openaidp') return 'openai'
  if (prefix === 'googledp') return 'google'
  return prefix
}

export function usePresetMatrixModels() {
  const config = useConfigStore()
  const {
    models,
    fpfModels,
    fpfFreeModels,
    gptrModels,
    gptrFreeModels,
    drModels,
    drFreeModels,
    evalModels,
    evalFreeModels,
    combineModels,
    combineFreeModels,
  } = useModelCatalog()

  const fpfEligible = useMemo(() => new Set([...fpfModels, ...fpfFreeModels]), [fpfModels, fpfFreeModels])
  const gptrEligible = useMemo(() => new Set([...gptrModels, ...gptrFreeModels]), [gptrModels, gptrFreeModels])
  const drEligible = useMemo(() => new Set([...drModels, ...drFreeModels]), [drModels, drFreeModels])
  const evalEligible = useMemo(() => new Set([...evalModels, ...evalFreeModels]), [evalModels, evalFreeModels])
  const combineEligible = useMemo(() => new Set([...combineModels, ...combineFreeModels]), [combineModels, combineFreeModels])
  const aiqSectionModels = useMemo(
    () => new Set(Object.keys(models).filter((model) => models[model]?.sections.includes('aiq'))),
    [models]
  )
  const openAiModels = useMemo(
    () => new Set(Object.keys(models).filter((model) => model.startsWith('openai:'))),
    [models]
  )
  const aiqEligible = useMemo(() => {
    return new Set(
      Array.from(new Set([...aiqSectionModels, ...openAiModels])).filter((model) => models[model]?.dr_native !== true)
    )
  }, [aiqSectionModels, openAiModels, models])

  const matrixModels = useMemo(
    () =>
      Array.from(
        new Set([
          ...fpfEligible,
          ...gptrEligible,
          ...drEligible,
          ...aiqSectionModels,
          ...openAiModels,
          ...evalEligible,
          ...combineEligible,
        ])
      ).sort((a, b) => a.localeCompare(b)),
    [aiqSectionModels, combineEligible, drEligible, evalEligible, fpfEligible, gptrEligible, openAiModels]
  )

  const isEligible = (section: MatrixSection | 'aiq', model: string) => {
    if (section === 'aiq') return aiqEligible.has(model)
    if (section === 'fpf') return fpfEligible.has(model)
    if (section === 'gptr') return gptrEligible.has(model)
    if (section === 'dr') return drEligible.has(model)
    if (section === 'eval') return evalEligible.has(model)
    return combineEligible.has(model)
  }

  const isSelected = (section: MatrixSection | 'aiq', model: string) => {
    if (section === 'aiq') return config.aiq.selectedModels.includes(model)
    if (section === 'fpf') return config.fpf.selectedModels.includes(model)
    if (section === 'gptr') return config.gptr.selectedModels.includes(model)
    if (section === 'dr') return config.dr.selectedModels.includes(model)
    if (section === 'eval') return config.eval.judgeModels.includes(model)
    return config.combine.selectedModels.includes(model)
  }

  const handleMatrixToggle = (section: MatrixSection | 'aiq', model: string, checked: boolean) => {
    const toggle = (current: string[]) =>
      checked ? Array.from(new Set([...current, model])) : current.filter((item) => item !== model)

    if (section === 'aiq') {
      const selectedModels = toggle(config.aiq.selectedModels)
      config.updateAiq({ selectedModels, enabled: selectedModels.length > 0 })
      return
    }
    if (section === 'fpf') {
      const selectedModels = toggle(config.fpf.selectedModels)
      config.updateFpf({ selectedModels, enabled: selectedModels.length > 0 })
      return
    }
    if (section === 'gptr') {
      const selectedModels = toggle(config.gptr.selectedModels)
      config.updateGptr({ selectedModels, enabled: selectedModels.length > 0 })
      return
    }
    if (section === 'dr') {
      const selectedModels = toggle(config.dr.selectedModels)
      config.updateDr({ selectedModels, enabled: selectedModels.length > 0 })
      return
    }
    if (section === 'eval') {
      const judgeModels = toggle(config.eval.judgeModels)
      config.updateEval({ judgeModels, enabled: judgeModels.length > 0 })
      return
    }

    const selectedModels = toggle(config.combine.selectedModels)
    config.updateCombine({ selectedModels, enabled: selectedModels.length > 0 })
  }

  return {
    matrixModels,
    isEligible,
    isSelected,
    handleMatrixToggle,
  }
}

export function useMatrixMetrics() {
  const q = GEN_BADGE_QUANTILES
  const generationSections: Array<'fpf' | 'gptr' | 'dr'> = ['fpf', 'gptr', 'dr']

  const bestGenerationScore = (model: string) => {
    const values = generationSections
      .map((section) => getGenScore(section as GenScoreType, model)?.score)
      .filter((value): value is number => value != null)
    return values.length ? Math.max(...values) : undefined
  }

  const evalScore = (model: string) => {
    const judge = getJudgeQuality(model)
    return judge?.sortino != null ? judge.sortino * 100 : undefined
  }

  const genScoreClass = (value?: number) =>
    value != null ? tierClass(value, q.genScore, true) : 'bg-gray-900 text-gray-500'
  const evalScoreClass = (value?: number) =>
    value != null ? tierClass(value, q.evalScore, true) : 'bg-gray-900 text-gray-500'

  return {
    bestGenerationScore,
    evalScore,
    genScoreClass,
    evalScoreClass,
  }
}

export interface MatrixViewSharedProps {
  matrixModels: string[]
  distinctProviders: ProviderFilterKey[]
  configuredKeyProviders: Set<string>
  filterProviders: Set<ProviderFilterKey> | null
  showScored: boolean
  showUnscored: boolean
  showFree: boolean
  hideKeyless: boolean
  cardSortCol: string | null
  cardSortDir: 'asc' | 'desc'
  metricReportType: MetricReportType
  setMetricReportType: Dispatch<SetStateAction<MetricReportType>>
  setFilterProviders: Dispatch<SetStateAction<Set<ProviderFilterKey> | null>>
  setShowScored: Dispatch<SetStateAction<boolean>>
  setShowUnscored: Dispatch<SetStateAction<boolean>>
  setShowFree: Dispatch<SetStateAction<boolean>>
  setHideKeyless: Dispatch<SetStateAction<boolean>>
  handleColSort: (col: string) => void
  clearFilters: () => void
}

export function sortByOptionalMetric<T>(
  items: T[],
  getMetric: (item: T) => number | string | null | undefined,
  getLabel: (item: T) => string,
  direction: 'asc' | 'desc'
): T[] {
  const ranked: Array<{ item: T; metric: number | string }> = []
  const missing: T[] = []

  for (const item of items) {
    const metric = getMetric(item)
    if (typeof metric === 'number') {
      if (Number.isFinite(metric)) {
        ranked.push({ item, metric })
      } else {
        missing.push(item)
      }
      continue
    }
    if (typeof metric === 'string') {
      ranked.push({ item, metric })
      continue
    }
    missing.push(item)
  }

  ranked.sort((a, b) => {
    const av = a.metric
    const bv = b.metric
    const cmp =
      typeof av === 'string' && typeof bv === 'string'
        ? av.localeCompare(bv)
        : (av as number) - (bv as number)
    if (cmp !== 0) return direction === 'asc' ? cmp : -cmp
    return getLabel(a.item).localeCompare(getLabel(b.item))
  })

  missing.sort((a, b) => getLabel(a).localeCompare(getLabel(b)))

  return [...ranked.map((entry) => entry.item), ...missing]
}

export function MatrixCheckbox({
  checked,
  eligible,
  onToggle,
  tone,
  large = false,
}: {
  checked: boolean
  eligible: boolean
  onToggle: () => void
  tone: 'gen' | 'eval' | 'combine'
  large?: boolean
}) {
  const baseTone =
    tone === 'gen'
      ? checked
        ? 'border-blue-300 bg-gradient-to-br from-cyan-400 via-blue-500 to-indigo-600 shadow-[0_0_0_2px_rgba(59,130,246,0.22),0_0_24px_rgba(56,189,248,0.35)]'
        : 'border-blue-700/70 bg-blue-950/70 hover:border-cyan-400/70 hover:bg-blue-900/80'
      : tone === 'eval'
        ? checked
          ? 'border-emerald-300 bg-gradient-to-br from-lime-400 via-emerald-500 to-green-700 shadow-[0_0_0_2px_rgba(16,185,129,0.22),0_0_24px_rgba(74,222,128,0.35)]'
          : 'border-emerald-700/70 bg-emerald-950/70 hover:border-lime-400/70 hover:bg-emerald-900/80'
        : checked
          ? 'border-fuchsia-300 bg-gradient-to-br from-pink-400 via-fuchsia-500 to-violet-700 shadow-[0_0_0_2px_rgba(217,70,239,0.2),0_0_24px_rgba(244,114,182,0.32)]'
          : 'border-fuchsia-700/70 bg-fuchsia-950/70 hover:border-pink-400/70 hover:bg-fuchsia-900/80'

  return (
    <button
      type="button"
      aria-pressed={checked}
      disabled={!eligible}
      onClick={onToggle}
      className={cn(
        'inline-flex items-center justify-center rounded-xl border transition duration-150 disabled:cursor-not-allowed disabled:opacity-35',
        large ? 'h-9 w-9' : 'h-8 w-8',
        eligible ? baseTone : 'border-dashed border-gray-800 bg-gray-950/20'
      )}
    >
      <div className={cn('relative shrink-0 overflow-visible', large ? 'h-4 w-4' : 'h-3.5 w-3.5')}>
        <div className="absolute inset-0 rounded-full border border-white/30 bg-white/20 shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]" />
        {checked ? (
          <>
            <Check
              className={cn('pointer-events-none absolute z-20 text-black', large ? '-left-3 -top-3 h-10 w-10' : '-left-2.5 -top-2.5 h-8 w-8')}
              strokeWidth={5}
            />
            <Check
              className={cn('pointer-events-none absolute z-[21] text-white', large ? '-left-3 -top-3 h-10 w-10' : '-left-2.5 -top-2.5 h-8 w-8')}
              strokeWidth={3.5}
            />
          </>
        ) : null}
      </div>
    </button>
  )
}
