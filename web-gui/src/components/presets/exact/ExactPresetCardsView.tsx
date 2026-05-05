import { getAiqScore, getGenScore } from '@/data/genModelScores'
import { GEN_BADGE_QUANTILES, tierClass } from '@/data/badgeUtils'
import { cn } from '@/lib/utils'
import {
  extractKeyProvider,
  extractProviderFilterKey,
  sortByOptionalMetric,
  type MatrixSection,
  type MatrixViewSharedProps,
  PROVIDER_FILTER_LABELS,
  METRIC_REPORT_LABELS,
  METRIC_REPORT_OPTIONS,
} from './ExactPresetMatrixShared'
import { usePresetMatrixModels } from './ExactPresetMatrixShared'
import { getJudgeQuality } from '@/data/judgeQualityScores'
import { useModelCatalog } from '@/stores/modelCatalog'

const hasFreeSection = (sections?: string[]) => sections?.includes('free') === true

export default function ExactPresetCardsView({
  matrixModels,
  distinctProviders,
  configuredKeyProviders,
  filterProviders,
  showScored,
  showUnscored,
  showFree,
  hideKeyless,
  cardSortCol,
  cardSortDir,
  metricReportType,
  setMetricReportType,
  setFilterProviders,
  setShowScored,
  setShowUnscored,
  setShowFree,
  setHideKeyless,
  handleColSort,
  clearFilters,
}: MatrixViewSharedProps) {
  const { isEligible, isSelected, handleMatrixToggle } = usePresetMatrixModels()
  const models = useModelCatalog((state) => state.models)

  type ComboSection = MatrixSection | 'aiq'
  const SECTION_META: Record<ComboSection, { label: string; glow: string; onClass: string; offClass: string }> = {
    fpf: {
      label: 'FPF',
      glow: 'shadow-[0_0_24px_rgba(59,130,246,0.2)]',
      onClass: 'border-blue-300/70 bg-gradient-to-br from-blue-400/28 to-blue-700/28 text-blue-50',
      offClass: 'border-blue-900/60 bg-blue-950/20 text-blue-200/70 hover:border-blue-400/40 hover:bg-blue-900/25',
    },
    gptr: {
      label: 'GPT-R',
      glow: 'shadow-[0_0_24px_rgba(147,51,234,0.18)]',
      onClass: 'border-purple-300/70 bg-gradient-to-br from-purple-400/28 to-purple-700/28 text-purple-50',
      offClass: 'border-purple-900/60 bg-purple-950/20 text-purple-200/70 hover:border-purple-400/40 hover:bg-purple-900/25',
    },
    dr: {
      label: 'DR',
      glow: 'shadow-[0_0_24px_rgba(245,158,11,0.16)]',
      onClass: 'border-amber-300/70 bg-gradient-to-br from-amber-300/28 to-amber-700/28 text-amber-50',
      offClass: 'border-amber-900/60 bg-amber-950/20 text-amber-200/70 hover:border-amber-400/40 hover:bg-amber-900/25',
    },
    aiq: {
      label: 'AI-Q',
      glow: 'shadow-[0_0_24px_rgba(34,211,238,0.16)]',
      onClass: 'border-cyan-300/70 bg-gradient-to-br from-cyan-300/28 to-sky-700/28 text-cyan-50',
      offClass: 'border-cyan-900/60 bg-cyan-950/20 text-cyan-200/70 hover:border-cyan-400/40 hover:bg-cyan-900/25',
    },
    eval: {
      label: 'Eval',
      glow: 'shadow-[0_0_24px_rgba(34,197,94,0.16)]',
      onClass: 'border-green-300/70 bg-gradient-to-br from-green-400/28 to-green-700/28 text-green-50',
      offClass: 'border-green-900/60 bg-green-950/20 text-green-200/70 hover:border-green-400/40 hover:bg-green-900/25',
    },
    combine: {
      label: 'Combine',
      glow: 'shadow-[0_0_18px_rgba(156,163,175,0.14)]',
      onClass: 'border-gray-300/60 bg-gradient-to-br from-gray-300/22 to-gray-600/24 text-gray-50',
      offClass: 'border-gray-700/70 bg-gray-900/30 text-gray-200/70 hover:border-gray-400/40 hover:bg-gray-800/30',
    },
  }

  const q = GEN_BADGE_QUANTILES
  const generationSections: Array<'fpf' | 'gptr' | 'dr'> = ['fpf', 'gptr', 'dr']
  const metricLabel = METRIC_REPORT_LABELS[metricReportType]

  const selectedGenerationScore = (model: string) =>
    metricReportType === 'aiq' ? getAiqScore(model)?.score : getGenScore(metricReportType, model)?.score
  const evalScore = (model: string) => {
    const judge = getJudgeQuality(model)
    return judge?.sortino != null ? judge.sortino * 100 : undefined
  }

  const allModels =
    cardSortCol === 'score'
      ? sortByOptionalMetric(matrixModels, selectedGenerationScore, (model) => model, cardSortDir)
      : cardSortCol === 'evalScore'
        ? sortByOptionalMetric(matrixModels, evalScore, (model) => model, cardSortDir)
        : sortByOptionalMetric(matrixModels, (model) => model, (model) => model, cardSortDir)

  const visibleModels = allModels.filter((model) => {
    const isFree = hasFreeSection(models[model]?.sections) || model.includes(':free')
    if (isFree) return showFree

    const provider = extractProviderFilterKey(model)
    if (filterProviders !== null && !filterProviders.has(provider)) return false
    if (hideKeyless && !isFree) {
      const keyProvider = extractKeyProvider(model)
      if (!configuredKeyProviders.has(keyProvider)) return false
    }
    const hasOnlyAiqEligibility =
      isEligible('aiq', model) &&
      !generationSections.some((section) => isEligible(section, model)) &&
      !isEligible('eval', model) &&
      !isEligible('combine', model)
    if (hasOnlyAiqEligibility) return true

    const hasSelectedScore = selectedGenerationScore(model) != null
    if (hasSelectedScore && !showScored) return false
    if (!hasSelectedScore && !showUnscored) return false

    if (generationSections.some((section) => isEligible(section, model))) return true
    if (isEligible('aiq', model)) return true
    if (isEligible('eval', model)) return true
    return isEligible('combine', model)
  })

  const isFiltered =
    !showScored ||
    !showUnscored ||
    !showFree ||
    hideKeyless ||
    (filterProviders !== null && filterProviders.size < distinctProviders.length)

  const hdrBtn = (col: string, label: string, cls: string) => (
    <button
      onClick={() => handleColSort(col)}
      className={`${cls} text-center hover:text-gray-300 transition-colors cursor-pointer ${cardSortCol === col ? 'text-blue-400' : ''}`}
    >
      {label}
      {cardSortCol === col ? (cardSortDir === 'asc' ? ' ↑' : ' ↓') : ''}
    </button>
  )

  const pillOn = 'font-semibold transition-colors border'
  const pillOff = 'font-semibold transition-colors border bg-transparent border-gray-700 text-gray-500'

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden mb-6" data-testid="preset14-view-cards">
      <div className="p-4 border-b border-gray-700">
        <h3 className="font-medium">Model × Section Cards</h3>
        <p className="text-sm text-gray-400">
          Each card is one model in one section. Use the section labels to include or exclude it.
        </p>
      </div>
      <div className="p-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pb-3 mb-3 border-b border-gray-700 text-xs">
          {filterProviders !== null && distinctProviders.length > 0 ? (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Provider</span>
              {distinctProviders.map((provider) => {
                const on = filterProviders.has(provider)
                return (
                  <button
                    key={provider}
                    onClick={() =>
                      setFilterProviders((prev) => {
                        const next = new Set(prev ?? [])
                        on ? next.delete(provider) : next.add(provider)
                        return next
                      })
                    }
                    className={`px-2 py-0.5 rounded-full ${on ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}
                  >
                    {PROVIDER_FILTER_LABELS[provider]}
                  </button>
                )
              })}
            </div>
          ) : null}

          <div className="flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Show</span>
            <button onClick={() => setShowScored((v) => !v)} className={`px-2 py-0.5 rounded-full ${showScored ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}>Scored</button>
            <button onClick={() => setShowUnscored((v) => !v)} className={`px-2 py-0.5 rounded-full ${showUnscored ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}>Unscored</button>
            <button onClick={() => setShowFree((v) => !v)} className={`px-2 py-0.5 rounded-full ${showFree ? `${pillOn} bg-green-700 text-green-100 border-green-600` : pillOff}`}>Free</button>
          </div>

          <div className="flex items-center gap-1.5" data-testid="preset14-metric-selector">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Show Scores for</span>
            {METRIC_REPORT_OPTIONS.map((option) => {
              const on = metricReportType === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setMetricReportType(option.value)}
                  className={`px-2 py-0.5 rounded-full ${on ? `${pillOn} bg-gray-600 text-gray-100 border-gray-500` : pillOff}`}
                  aria-pressed={on}
                >
                  {option.label}
                </button>
              )
            })}
          </div>

          <div className="flex items-center gap-1.5">
            <button onClick={() => setHideKeyless((v) => !v)} className={`px-2 py-0.5 rounded-full ${hideKeyless ? `${pillOn} bg-yellow-700 text-yellow-100 border-yellow-600` : pillOff}`}>BYOK only</button>
          </div>

          {isFiltered ? (
            <button onClick={clearFilters} className="text-[10px] text-gray-500 hover:text-gray-300 underline ml-auto">
              Clear filters
            </button>
          ) : null}
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-gray-700 pb-3 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
          <span className="mr-1 text-gray-400">Sort</span>
          {hdrBtn('model', 'Model', 'rounded-full border border-gray-700 px-2 py-1')}
          {hdrBtn('score', `${metricLabel} Score`, 'rounded-full border border-gray-700 px-2 py-1')}
          {hdrBtn('evalScore', 'Eval Score', 'rounded-full border border-gray-700 px-2 py-1')}
        </div>

        {visibleModels.length === 0 ? (
          <p className="text-sm text-gray-500 py-6 text-center">No cards match the current filters.</p>
        ) : (
          <div className="grid justify-start gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, 23rem)' }}>
            {visibleModels.map((model) => {
              const isFree = hasFreeSection(models[model]?.sections) || model.includes(':free')
              const selectedScore = selectedGenerationScore(model)

              const renderPhaseButton = (section: ComboSection) => {
                const meta = SECTION_META[section]
                const checked = isSelected(section, model)
                const eligible = isEligible(section, model)
                const judgeScore = section === 'eval' ? evalScore(model) : undefined

                return (
                  <button
                    key={`${section}-${model}`}
                    type="button"
                    aria-pressed={checked}
                    disabled={!eligible}
                    onClick={() => eligible && handleMatrixToggle(section, model, !checked)}
                    className={cn(
                      'border text-left transition duration-150 disabled:cursor-not-allowed disabled:opacity-40',
                      checked ? `${meta.onClass} ${meta.glow}` : meta.offClass,
                      section === 'combine'
                        ? 'inline-flex h-12 w-fit min-w-[84px] shrink-0 items-center rounded-xl px-3 py-2'
                        : section === 'eval'
                          ? 'flex h-12 min-w-0 flex-1 items-center gap-2 rounded-xl px-3 py-2'
                          : 'inline-flex h-12 w-20 shrink-0 items-center justify-center rounded-xl px-3 py-2'
                    )}
                    data-testid={`matrix-${section}-${model}`}
                  >
                    {section === 'fpf' || section === 'gptr' || section === 'dr' || section === 'aiq' ? (
                      <div className="flex items-center justify-center gap-2">
                        <div className={cn('h-3 w-3 rounded-full border border-white/20', checked ? 'bg-white/90' : 'bg-white/10')} />
                        <div className="text-[10px] font-semibold uppercase tracking-[0.22em]">{meta.label}</div>
                      </div>
                    ) : (
                      <div className="flex w-full items-center gap-2">
                        <div className={cn('h-3 w-3 rounded-full border border-white/20', checked ? 'bg-white/90' : 'bg-white/10')} />
                        <div className={cn('font-semibold uppercase tracking-[0.22em]', section === 'combine' ? 'text-xs' : 'text-[10px]')}>{meta.label}</div>
                        {section === 'eval' ? (
                          <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
                            <div className={cn('inline-flex h-12 w-12 items-center justify-center rounded-md px-0 text-center text-[10px] leading-tight', judgeScore != null ? tierClass(judgeScore, q.evalScore, true) : 'bg-slate-800 text-gray-400')}>
                              {judgeScore != null ? `${judgeScore.toFixed(0)}%` : '--'}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    )}
                  </button>
                )
              }

              return (
                <div
                  key={model}
                  className="preset4-card w-[23rem] rounded-[24px] border border-gray-700 bg-[linear-gradient(180deg,rgba(31,41,55,0.98)_0%,rgba(17,24,39,0.98)_100%)] transition-transform duration-200"
                >
                  <div className="p-4">
                    <div className="mb-4 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
                      <div className="min-w-0 pr-2">
                        <h4 className="break-words font-mono text-sm text-white">{model}</h4>
                      </div>
                      <div className="ml-auto flex flex-wrap items-center justify-end gap-2 text-[11px] font-mono">
                        <div className={cn('inline-flex h-12 w-12 items-center justify-center rounded-md px-0 text-center text-[10px] leading-tight', selectedScore != null ? tierClass(selectedScore, q.genScore, true) : 'bg-slate-800 text-gray-400')}>
                          {selectedScore != null ? `${((selectedScore / 5) * 100).toFixed(0)}%` : '--'}
                        </div>
                        {isFree ? (
                          <span className="rounded-full border border-emerald-400/30 bg-emerald-400/12 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-100">
                            Free
                          </span>
                        ) : null}
                      </div>
                    </div>

                    <div className="grid gap-2">
                      <div className="flex w-full flex-nowrap items-center justify-between gap-2">
                        {generationSections.map(renderPhaseButton)}
                        {renderPhaseButton('aiq')}
                      </div>
                      <div className="flex w-full flex-nowrap items-center gap-2">
                        {renderPhaseButton('eval')}
                        {renderPhaseButton('combine')}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {visibleModels.length !== allModels.length ? (
          <p className="text-[10px] text-gray-600 text-right mt-2">
            Showing {visibleModels.length} of {allModels.length} model boards
          </p>
        ) : null}
      </div>
    </div>
  )
}
