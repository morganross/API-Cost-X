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
  usePresetMatrixModels,
} from './ExactPresetMatrixShared'
import { getJudgeQuality } from '@/data/judgeQualityScores'
import { useModelCatalog } from '@/stores/modelCatalog'

const hasFreeSection = (sections?: string[]) => sections?.includes('free') === true

export default function ExactPresetTableView({
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
  const q = GEN_BADGE_QUANTILES
  const generationSections: Array<'fpf' | 'gptr' | 'dr'> = ['fpf', 'gptr', 'dr']
  const metricLabel = METRIC_REPORT_LABELS[metricReportType]

  const selectedGenerationScore = (model: string) =>
    metricReportType === 'aiq' ? getAiqScore(model)?.score : getGenScore(metricReportType, model)?.score
  const evalScore = (model: string) => {
    const judge = getJudgeQuality(model)
    return judge?.sortino != null ? judge.sortino * 100 : undefined
  }

  const allRows =
    cardSortCol === 'score'
      ? sortByOptionalMetric(matrixModels, selectedGenerationScore, (model) => model, cardSortDir)
      : cardSortCol === 'evalScore'
        ? sortByOptionalMetric(matrixModels, evalScore, (model) => model, cardSortDir)
        : sortByOptionalMetric(matrixModels, (model) => model, (model) => model, cardSortDir)

  const visibleRows = allRows.filter((model) => {
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
    <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden mb-6" data-testid="preset14-view-table">
      <div className="p-4 border-b border-gray-700">
        <h3 className="font-medium">Phase-Banded Model Table</h3>
        <p className="text-sm text-gray-400">
          Generation, AI-Q, evaluation, and combine each get their own louder lane, with brighter checkbox states for faster scanning.
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

        {visibleRows.length === 0 ? (
          <p className="text-sm text-gray-500 py-6 text-center">No cards match the current filters.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0 text-xs">
              <thead>
                <tr className="bg-gray-900/70 text-[10px] uppercase tracking-wide text-gray-500">
                  <th className="sticky top-0 border-b border-gray-700 px-3 py-2 text-left">{hdrBtn('model', 'Model', 'text-left')}</th>
                  <th className="sticky top-0 border-b border-gray-700 px-2 py-2 text-center">{hdrBtn('score', `${metricLabel} Score`, 'w-full')}</th>
                  <th className="sticky top-0 border-b border-blue-700/70 bg-blue-950/70 px-2 py-2 text-center text-blue-200">FPF</th>
                  <th className="sticky top-0 border-b border-blue-700/70 bg-blue-950/70 px-2 py-2 text-center text-blue-200">GPT-R</th>
                  <th className="sticky top-0 border-b border-blue-700/70 bg-blue-950/70 px-2 py-2 text-center text-blue-200">DR</th>
                  <th className="sticky top-0 border-b border-cyan-700/70 bg-cyan-950/70 px-2 py-2 text-center text-cyan-200">AI-Q</th>
                  <th className="sticky top-0 border-b border-emerald-700/70 bg-emerald-950/70 px-2 py-2 text-center">{hdrBtn('evalScore', 'Eval Score', 'w-full')}</th>
                  <th className="sticky top-0 border-b border-emerald-700/70 bg-emerald-950/70 px-2 py-2 text-center text-emerald-200">Eval</th>
                  <th className="sticky top-0 border-b border-fuchsia-700/70 bg-fuchsia-950/70 px-2 py-2 text-center text-fuchsia-200">Combine</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((model) => {
                  const free = hasFreeSection(models[model]?.sections) || model.includes(':free')
                  const genScore = selectedGenerationScore(model)
                  const judgeScore = evalScore(model)
                  return (
                    <tr key={model} className="border-b border-gray-800/70 hover:bg-gray-800/35">
                      <td className="border-b border-gray-800 px-3 py-2 align-middle">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-gray-200">{model}</span>
                          {free ? <span className="rounded-full border border-green-700 bg-green-900/40 px-1.5 py-0.5 text-[10px] uppercase text-green-200">Free</span> : null}
                        </div>
                      </td>
                      <td className="border-b border-gray-800 px-2 py-2 text-center">
                        <span className={cn('inline-block min-w-[3.75rem] rounded px-2 py-1 font-mono', genScore != null ? tierClass(genScore, q.genScore, true) : 'bg-gray-900 text-gray-500')}>
                          {genScore != null ? genScore.toFixed(2) : '--'}
                        </span>
                      </td>
                      {(['fpf', 'gptr', 'dr'] as const).map((section) => (
                        <td key={`${section}-${model}`} className="border-b border-blue-950/80 bg-blue-950/25 px-2 py-2 text-center">
                          <label
                            className={cn(
                              'inline-flex h-9 w-9 items-center justify-center rounded-xl border transition duration-150',
                              isEligible(section, model)
                                ? isSelected(section, model)
                                  ? 'border-blue-300 bg-gradient-to-br from-cyan-400 via-blue-500 to-indigo-600 shadow-[0_0_0_2px_rgba(59,130,246,0.22),0_0_24px_rgba(56,189,248,0.35)]'
                                  : 'border-blue-700/70 bg-blue-950/70 hover:border-cyan-400/70 hover:bg-blue-900/80'
                                : 'border-dashed border-blue-950 bg-blue-950/20 opacity-35'
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={isSelected(section, model)}
                              disabled={!isEligible(section, model)}
                              onChange={(e) => handleMatrixToggle(section, model, e.target.checked)}
                              className="h-4 w-4 cursor-pointer rounded border-gray-600 bg-transparent text-white disabled:cursor-not-allowed disabled:opacity-30"
                              data-testid={`matrix-${section}-${model}`}
                            />
                          </label>
                        </td>
                      ))}
                      <td className="border-b border-cyan-950/80 bg-cyan-950/25 px-2 py-2 text-center">
                        <label
                          className={cn(
                            'inline-flex h-9 w-9 items-center justify-center rounded-xl border transition duration-150',
                            isEligible('aiq', model)
                              ? isSelected('aiq', model)
                                ? 'border-cyan-300 bg-gradient-to-br from-cyan-400 via-sky-500 to-blue-700 shadow-[0_0_0_2px_rgba(34,211,238,0.22),0_0_24px_rgba(14,165,233,0.35)]'
                                : 'border-cyan-700/70 bg-cyan-950/70 hover:border-cyan-300/70 hover:bg-cyan-900/80'
                              : 'border-dashed border-cyan-950 bg-cyan-950/20 opacity-35'
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected('aiq', model)}
                            disabled={!isEligible('aiq', model)}
                            onChange={(e) => handleMatrixToggle('aiq', model, e.target.checked)}
                            className="h-4 w-4 cursor-pointer rounded border-gray-600 bg-transparent text-white disabled:cursor-not-allowed disabled:opacity-30"
                            data-testid={`matrix-aiq-${model}`}
                          />
                        </label>
                      </td>
                      <td className="border-b border-emerald-950/80 bg-emerald-950/25 px-2 py-2 text-center">
                        <span className={cn('inline-block min-w-[4.75rem] rounded px-2 py-1 font-mono', judgeScore != null ? tierClass(judgeScore, q.evalScore, true) : 'bg-gray-900 text-gray-500')}>
                          {judgeScore != null ? `${judgeScore.toFixed(0)}%` : '--'}
                        </span>
                      </td>
                      <td className="border-b border-emerald-950/80 bg-emerald-950/25 px-2 py-2 text-center">
                        <label
                          className={cn(
                            'inline-flex h-9 w-9 items-center justify-center rounded-xl border transition duration-150',
                            isEligible('eval', model)
                              ? isSelected('eval', model)
                                ? 'border-emerald-300 bg-gradient-to-br from-lime-400 via-emerald-500 to-green-700 shadow-[0_0_0_2px_rgba(16,185,129,0.22),0_0_24px_rgba(74,222,128,0.35)]'
                                : 'border-emerald-700/70 bg-emerald-950/70 hover:border-lime-400/70 hover:bg-emerald-900/80'
                              : 'border-dashed border-emerald-950 bg-emerald-950/20 opacity-35'
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected('eval', model)}
                            disabled={!isEligible('eval', model)}
                            onChange={(e) => handleMatrixToggle('eval', model, e.target.checked)}
                            className="h-4 w-4 cursor-pointer rounded border-gray-600 bg-transparent text-white disabled:cursor-not-allowed disabled:opacity-30"
                            data-testid={`matrix-eval-${model}`}
                          />
                        </label>
                      </td>
                      <td className="border-b border-fuchsia-950/80 bg-fuchsia-950/25 px-2 py-2 text-center">
                        <label
                          className={cn(
                            'inline-flex h-9 w-9 items-center justify-center rounded-xl border transition duration-150',
                            isEligible('combine', model)
                              ? isSelected('combine', model)
                                ? 'border-fuchsia-300 bg-gradient-to-br from-pink-400 via-fuchsia-500 to-violet-700 shadow-[0_0_0_2px_rgba(217,70,239,0.2),0_0_24px_rgba(244,114,182,0.32)]'
                                : 'border-fuchsia-700/70 bg-fuchsia-950/70 hover:border-pink-400/70 hover:bg-fuchsia-900/80'
                              : 'border-dashed border-fuchsia-950 bg-fuchsia-950/20 opacity-35'
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected('combine', model)}
                            disabled={!isEligible('combine', model)}
                            onChange={(e) => handleMatrixToggle('combine', model, e.target.checked)}
                            className="h-4 w-4 cursor-pointer rounded border-gray-600 bg-transparent text-white disabled:cursor-not-allowed disabled:opacity-30"
                            data-testid={`matrix-combine-${model}`}
                          />
                        </label>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {visibleRows.length !== allRows.length ? (
          <p className="text-[10px] text-gray-600 text-right mt-2">Showing {visibleRows.length} of {allRows.length} rows</p>
        ) : null}
      </div>
    </div>
  )
}
