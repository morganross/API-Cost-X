// RunEstimator.tsx — Displays estimated LLM calls based on current config
import { useMemo, useState } from 'react'
import { Calculator, ChevronDown, ChevronRight } from 'lucide-react'
import { useConfigStore } from '../../stores/config'
import { estimateRuns, type RunEstimateBreakdown } from '../../lib/estimateRuns'

interface RunEstimatorProps {
  documentCount: number
}

export function RunEstimator({ documentCount }: RunEstimatorProps) {
  const config = useConfigStore()
  const [expanded, setExpanded] = useState(false)
  const fpfEnabled = config.fpf.selectedModels.length > 0
  const gptrEnabled = config.gptr.selectedModels.length > 0
  const drEnabled = config.dr.selectedModels.length > 0
  const aiqEnabled = config.aiq.selectedModels.length > 0
  const maEnabled = config.ma.enabled
  const evalEnabled = config.eval.judgeModels.length > 0
  const combineEnabled = config.combine.selectedModels.length > 0
  const generationModelCount =
    config.fpf.selectedModels.length +
    config.gptr.selectedModels.length +
    config.dr.selectedModels.length +
    (aiqEnabled ? config.aiq.selectedModels.length : 0) +
    (maEnabled ? config.ma.selectedModels.length : 0)
  const pairwiseEnabled =
    config.eval.enablePairwise &&
    evalEnabled &&
    generationModelCount >= 2

  const estimate: RunEstimateBreakdown = useMemo(() => {
    return estimateRuns({
      documentCount,
      fpfEnabled,
      fpfModelCount: config.fpf.selectedModels.length,
      gptrEnabled,
      gptrModelCount: config.gptr.selectedModels.length,
      drEnabled,
      drModelCount: config.dr.selectedModels.length,
      aiqEnabled,
      aiqModelCount: config.aiq.selectedModels.length,
      maEnabled,
      maModelCount: config.ma.selectedModels.length,
      iterations: config.general.iterations,
      evalEnabled,
      pairwiseEnabled,
      evalIterations: config.eval.iterations,
      judgeModelCount: config.eval.judgeModels.length,
      pairwiseTopN: config.eval.pairwiseTopN,
      combineEnabled,
      combineModelCount: config.combine.selectedModels.length,
    })
  }, [
    documentCount,
    fpfEnabled, config.fpf.selectedModels.length,
    gptrEnabled, config.gptr.selectedModels.length,
    drEnabled, config.dr.selectedModels.length,
    aiqEnabled, config.aiq.selectedModels.length,
    maEnabled, config.ma.selectedModels.length,
    config.general.iterations,
    evalEnabled, pairwiseEnabled, config.eval.iterations, config.eval.judgeModels.length,
    config.eval.pairwiseTopN,
    combineEnabled, config.combine.selectedModels.length,
  ])

  if (estimate.total === 0) {
    return (
      <div className="flex items-center gap-2 text-gray-500 text-sm">
        <Calculator className="w-4 h-4" />
        <span>0 LLM calls</span>
      </div>
    )
  }

  // Color based on magnitude
  const getColor = (n: number) => {
    if (n <= 20) return 'text-green-400'
    if (n <= 100) return 'text-yellow-400'
    if (n <= 500) return 'text-orange-400'
    return 'text-red-400'
  }

  const phases: { label: string; value: number; enabled: boolean }[] = [
    { label: 'Generation', value: estimate.generation, enabled: estimate.generation > 0 },
    { label: 'Single Eval', value: estimate.singleEval, enabled: evalEnabled },
    { label: 'Pre-Combine PW', value: estimate.preCombinePairwise, enabled: pairwiseEnabled },
    { label: 'Combine', value: estimate.combine, enabled: combineEnabled },
    { label: 'Post-Combine PW', value: estimate.postCombinePairwise, enabled: combineEnabled && pairwiseEnabled },
  ]

  const activePhases = phases.filter(p => p.enabled && p.value > 0)

  return (
    <div className="relative">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 px-3 py-1.5 bg-gray-700/80 hover:bg-gray-600/80 border border-gray-600 rounded-lg transition-colors"
      >
        <Calculator className="w-4 h-4 text-blue-400" />
        <span className={`font-bold text-lg tabular-nums ${getColor(estimate.total)}`}>
          {estimate.total.toLocaleString()}
        </span>
        <span className="text-gray-400 text-sm">LLM calls</span>
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-gray-500" />
        ) : (
          <ChevronRight className="w-3 h-3 text-gray-500" />
        )}
      </button>

      {expanded && (
        <div className="absolute top-full right-0 mt-1 z-50 bg-gray-800 border border-gray-600 rounded-lg shadow-xl p-3 min-w-[280px]">
          <div className="text-xs text-gray-400 mb-2 font-medium uppercase tracking-wider">
            Estimated LLM Calls Breakdown
          </div>

          {/* Per-phase breakdown */}
          <div className="space-y-1.5">
            {activePhases.map((phase) => (
              <div key={phase.label} className="flex items-center justify-between text-sm">
                <span className="text-gray-300">{phase.label}</span>
                <span className={`font-mono tabular-nums ${getColor(phase.value)}`}>
                  {phase.value.toLocaleString()}
                </span>
              </div>
            ))}
          </div>

          {/* Divider + Total */}
          <div className="border-t border-gray-600 mt-2 pt-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-200">Total</span>
            <span className={`font-mono font-bold tabular-nums ${getColor(estimate.total)}`}>
              {estimate.total.toLocaleString()}
            </span>
          </div>

          {/* Context info */}
          {estimate.documentCount > 1 && (
            <div className="text-xs text-gray-500 mt-2">
              {estimate.perDoc.total.toLocaleString()} calls/doc × {estimate.documentCount} docs
            </div>
          )}

          {/* Disabled phases */}
          {phases.some(p => !p.enabled) && (
            <div className="text-xs text-gray-600 mt-2 pt-2 border-t border-gray-700">
              {phases.filter(p => !p.enabled).map(p => p.label).join(', ')} — disabled
            </div>
          )}
        </div>
      )}
    </div>
  )
}
