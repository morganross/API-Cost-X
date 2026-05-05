import { useMemo } from 'react'
import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { FileText, Search, Zap } from 'lucide-react'
import { useConfigStore } from '../../stores/config'
import { useModelCatalog } from '../../stores/modelCatalog'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function FpfParamsPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()
  const { models } = useModelCatalog()
  const hasOpenRouterFpfModel = useMemo(
    () => config.fpf.selectedModels.some((model) => model.startsWith('openrouter:')),
    [config.fpf.selectedModels]
  )
  const searchMaxResults = Math.max(1, Math.min(10, config.fpf.openrouterSearchMaxResults || 3))
  const searchMaxTotalResults = Math.max(
    searchMaxResults,
    Math.min(20, config.fpf.openrouterSearchMaxTotalResults || 5)
  )

  // Compute max output tokens based on selected models (use minimum across all selected)
  const maxOutputTokensLimit = useMemo(() => {
    if (config.fpf.selectedModels.length === 0) {
      return 500000 // Default max when no models selected
    }
    // Skip openrouter: models — OpenRouter is a gateway, let it enforce its own limits
    const limits = config.fpf.selectedModels
      .filter(m => !m.startsWith('openrouter:'))
      .map(m => models[m]?.max_output_tokens)
      .filter((limit): limit is number => limit !== null && limit !== undefined)

    if (limits.length === 0) {
      return 500000 // Default if no limits found (or all models are OpenRouter)
    }
    // Floor at 65536: no model should cap the slider below 65k
    // This prevents reasoning/thinking tokens from being throttled
    return Math.max(65536, Math.min(...limits))
  }, [config.fpf.selectedModels, models])

  return (
    <Section
      title="FilePromptForge (FPF) Parameters"
      icon={<FileText className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <p className="mb-4 text-sm text-gray-400">
        Runs automatically when one or more FPF model cards are checked.
      </p>

      <div className="space-y-3 pt-2">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Zap className="w-4 h-4" /> Generation Parameters
        </h4>

        <Slider
          label="Temperature"
          value={config.fpf.temperature}
          onChange={(val) => config.updateFpf({ temperature: val })}
          min={0}
          max={2}
          step={0.1}
          displayValue={config.fpf.temperature.toFixed(1)}
        />

        <Slider
          label="Max Output Tokens"
          value={Math.min(config.fpf.maxTokens, maxOutputTokensLimit)}
          onChange={(val) => config.updateFpf({ maxTokens: val })}
          min={512}
          max={maxOutputTokensLimit}
          step={256}
          displayValue={`${config.fpf.maxTokens.toLocaleString()} (limit: ${maxOutputTokensLimit.toLocaleString()})`}
        />

        <Slider
          label="Thinking Budget (tokens)"
          value={config.fpf.thinkingBudget}
          onChange={(val) => config.updateFpf({ thinkingBudget: val })}
          min={256}
          max={200000}
          step={256}
          displayValue={config.fpf.thinkingBudget}
        />

        {hasOpenRouterFpfModel && (
          <div className="mt-4 space-y-3 rounded-lg border border-sky-800 bg-sky-950/20 p-3">
            <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
              <Search className="w-4 h-4" /> OpenRouter Web Search
            </h4>
            <p className="text-xs text-gray-400">
              Applies only to OpenRouter models selected in FPF.
            </p>

            <label className="block space-y-1 text-sm">
              <span className="text-gray-300">Search Context Size</span>
              <select
                value={config.fpf.openrouterSearchContextSize}
                onChange={(event) =>
                  config.updateFpf({
                    openrouterSearchContextSize: event.target.value as 'low' | 'medium' | 'high',
                  })
                }
                className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-sky-500 focus:outline-none"
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>

            <Slider
              label="Search Results Per Query"
              value={searchMaxResults}
              onChange={(val) =>
                config.updateFpf({
                  openrouterSearchMaxResults: val,
                  openrouterSearchMaxTotalResults: Math.max(config.fpf.openrouterSearchMaxTotalResults, val),
                })
              }
              min={1}
              max={10}
              step={1}
              displayValue={searchMaxResults}
            />

            <Slider
              label="Total Search Results"
              value={searchMaxTotalResults}
              onChange={(val) => config.updateFpf({ openrouterSearchMaxTotalResults: val })}
              min={searchMaxResults}
              max={20}
              step={1}
              displayValue={searchMaxTotalResults}
            />
          </div>
        )}
      </div>
    </Section>
  )
}
