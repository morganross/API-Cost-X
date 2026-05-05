import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Checkbox } from '../ui/checkbox'
import { BarChart3, FileText, Library, ExternalLink, Clock } from 'lucide-react'
import { useConfigStore } from '../../stores/config'
import { useModelCatalog } from '../../stores/modelCatalog'
import { contentsApi, type ContentSummary } from '../../api/contents'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function EvalPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()
  const { evalModels, models, fetchModels } = useModelCatalog()

  // Compute max output tokens based on selected judge models (use minimum across all selected)
  const maxOutputTokensLimit = useMemo(() => {
    if (config.eval.judgeModels.length === 0) {
      return 500000 // Default max when no models selected
    }
    // Skip openrouter: models — OpenRouter is a gateway, let it enforce its own limits
    const limits = config.eval.judgeModels
      .filter(m => !m.startsWith('openrouter:'))
      .map(m => models[m]?.max_output_tokens)
      .filter((limit): limit is number => limit !== null && limit !== undefined)

    if (limits.length === 0) {
      return 500000 // Default if no limits found (or all models are OpenRouter)
    }
    // Floor at 65536: no model should cap the slider below 65k
    // This prevents reasoning/thinking tokens from being throttled
    return Math.max(65536, Math.min(...limits))
  }, [config.eval.judgeModels, models])

  // Content Library items for eval instructions
  const [singleEvalContents, setSingleEvalContents] = useState<ContentSummary[]>([])
  const [pairwiseEvalContents, setPairwiseEvalContents] = useState<ContentSummary[]>([])
  const [evalCriteriaContents, setEvalCriteriaContents] = useState<ContentSummary[]>([])

  useEffect(() => {
    if (evalModels.length === 0) {
      fetchModels()
    }
    // Fetch content library items for each instruction type
    const loadContents = async () => {
      try {
        const [singleEval, pairwiseEval, evalCriteria] = await Promise.all([
          contentsApi.list({ content_type: 'single_eval_instructions' }),
          contentsApi.list({ content_type: 'pairwise_eval_instructions' }),
          contentsApi.list({ content_type: 'eval_criteria' }),
        ])
        setSingleEvalContents(singleEval.items)
        setPairwiseEvalContents(pairwiseEval.items)
        setEvalCriteriaContents(evalCriteria.items)
      } catch (err) {
        console.error('Failed to load eval instruction contents:', err)
      }
    }
    loadContents()
  }, [])

  return (
    <Section
      title="Evaluation Configuration"
      icon={<BarChart3 className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <p className="mb-4 text-sm text-gray-400">
        Runs automatically when one or more judge model cards are checked.
      </p>

      <div className="mb-4">
        <Checkbox
          checked={config.eval.enablePairwise}
          onChange={(val) => config.updateEval({ enablePairwise: val })}
          label="Enable pairwise comparison"
        />
        <p className="text-xs text-gray-500 ml-6">
          When enabled, models are compared head-to-head. Requires active evaluation and at least 2 generator model selections.
        </p>
      </div>

        {/* Evaluation Sliders */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <BarChart3 className="w-4 h-4" /> Evaluation Parameters
          </h4>

          <Slider
            label="Evaluation Iterations"
            value={config.eval.iterations}
            onChange={(val) => config.updateEval({ iterations: val })}
            min={1}
            max={9}
            step={1}
            displayValue={`${config.eval.iterations} iterations`}
          />

          <Slider
            label="Pairwise Top-N"
            value={config.eval.pairwiseTopN}
            onChange={(val) => config.updateEval({ pairwiseTopN: val })}
            min={1}
            max={10}
            step={1}
            displayValue={`Top ${config.eval.pairwiseTopN}`}
          />
        </div>

        {/* Timeout & Retry Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Clock className="w-4 h-4" /> Timeout & Retry
          </h4>

          <Slider
            label="Timeout (seconds)"
            value={config.eval.timeoutSeconds}
            onChange={(val) => config.updateEval({ timeoutSeconds: val })}
            min={60}
            max={1800}
            step={60}
            displayValue={`${config.eval.timeoutSeconds}s (${Math.round(config.eval.timeoutSeconds / 60)}m)`}
          />

          <Slider
            label="Extra Retries (JSON/Parse Errors)"
            value={config.eval.retries}
            onChange={(val) => config.updateEval({ retries: val })}
            min={0}
            max={5}
            step={1}
            displayValue={`${config.eval.retries} retries`}
          />
          <p className="text-xs text-gray-500">Additional retries for malformed LLM responses. HTTP errors use FPF's built-in retry.</p>

          <Slider
            label="Max Output Tokens"
            value={Math.min(config.eval.maxTokens, maxOutputTokensLimit)}
            onChange={(val) => config.updateEval({ maxTokens: val })}
            min={1024}
            max={maxOutputTokensLimit}
            step={1024}
            displayValue={`${config.eval.maxTokens.toLocaleString()} (limit: ${maxOutputTokensLimit.toLocaleString()})`}
          />
          <p className="text-xs text-gray-500">Maximum tokens for judge LLM output. Limit based on selected judge models.</p>

          <Slider
            label="Thinking Budget (tokens)"
            value={config.eval.thinkingBudget}
            onChange={(val) => config.updateEval({ thinkingBudget: val })}
            min={256}
            max={200000}
            step={256}
            displayValue={config.eval.thinkingBudget.toLocaleString()}
          />
          <p className="text-xs text-gray-500">Token budget for extended thinking in judge models (Anthropic, Gemini, etc.).</p>

          <Slider
            label="Temperature"
            value={config.eval.temperature}
            onChange={(val) => config.updateEval({ temperature: val })}
            min={0}
            max={2}
            step={0.1}
            displayValue={config.eval.temperature.toFixed(1)}
          />
          <p className="text-xs text-gray-500">Lower temperature = more deterministic judge responses.</p>
        </div>

        {/* Single Eval Instructions */}
        <div className="space-y-2 border-t border-gray-700 pt-4" data-section="single-eval-instructions">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
              <FileText className="w-4 h-4" /> Single Eval Instructions <span className="text-red-400">*</span>
            </h4>
            <Link
                    to="/content"
              className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              Library</Link>
          </div>
          {singleEvalContents.length === 0 ? (
            <p className="text-xs text-red-400">No single eval instructions in library - required</p>
          ) : (
            <select
              value={config.eval.singleEvalInstructionsId || ''}
              onChange={(e) => config.updateEval({ singleEvalInstructionsId: e.target.value || null })}
              className={`w-full bg-gray-700 border rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500 ${
                !config.eval.singleEvalInstructionsId ? 'border-red-500' : 'border-gray-600'
              }`}
            >
              <option value="">-- Select Instructions (required) --</option>
              {singleEvalContents.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
        </div>

        {/* Pairwise Eval Instructions */}
        <div className="space-y-2 border-t border-gray-700 pt-4" data-section="pairwise-eval-instructions">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
              <FileText className="w-4 h-4" /> Pairwise Eval Instructions <span className="text-red-400">*</span>
            </h4>
            <Link
                    to="/content"
              className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              Library</Link>
          </div>
          {pairwiseEvalContents.length === 0 ? (
            <p className="text-xs text-red-400">No pairwise eval instructions in library - required for pairwise</p>
          ) : (
            <select
              value={config.eval.pairwiseEvalInstructionsId || ''}
              onChange={(e) => config.updateEval({ pairwiseEvalInstructionsId: e.target.value || null })}
              className={`w-full bg-gray-700 border rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500 ${
                !config.eval.pairwiseEvalInstructionsId ? 'border-red-500' : 'border-gray-600'
              }`}
            >
              <option value="">-- Select Instructions (required for pairwise) --</option>
              {pairwiseEvalContents.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
        </div>

        {/* Eval Criteria */}
        <div className="space-y-2 border-t border-gray-700 pt-4" data-section="eval-criteria">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
              <Library className="w-4 h-4" /> Evaluation Criteria <span className="text-red-400">*</span>
            </h4>
            <Link
                    to="/content"
              className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              Library</Link>
          </div>
          {evalCriteriaContents.length === 0 ? (
            <div className="space-y-2">
              <p className="text-xs text-red-400">No eval criteria in library - required for evaluation</p>
              <Link
              to="/content"
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 rounded transition-colors"
              >
                Create Criteria in Library →</Link>
            </div>
          ) : (
            <select
              value={config.eval.evalCriteriaId || ''}
              onChange={(e) => config.updateEval({ evalCriteriaId: e.target.value || null })}
              className={`w-full bg-gray-700 border rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500 ${
                !config.eval.evalCriteriaId ? 'border-red-500' : 'border-gray-600'
              }`}
            >
              <option value="">-- Select Criteria (required) --</option>
              {evalCriteriaContents.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
          {!config.eval.evalCriteriaId && evalCriteriaContents.length > 0 && (
            <p className="text-xs text-red-400">Evaluation criteria is required</p>
          )}
        </div>
    </Section>
  )
}
