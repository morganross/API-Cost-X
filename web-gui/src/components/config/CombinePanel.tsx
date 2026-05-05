import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Section } from '../ui/section'
import { Combine as CombineIcon, FileText, ExternalLink, Sliders } from 'lucide-react'
import { useConfigStore } from '../../stores/config'
import { useModelCatalog } from '../../stores/modelCatalog'
import { contentsApi, type ContentSummary } from '../../api/contents'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function CombinePanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()
  const { combineModels, models, fetchModels } = useModelCatalog()

  // Content Library items for combine instructions
  const [combineInstructionContents, setCombineInstructionContents] = useState<ContentSummary[]>([])

  useEffect(() => {
    if (combineModels.length === 0) {
      fetchModels()
    }
    // Fetch content library items for combine instructions
    const loadContents = async () => {
      try {
        const result = await contentsApi.list({ content_type: 'combine_instructions' })
        setCombineInstructionContents(result.items)
      } catch (err) {
        console.error('Failed to load combine instruction contents:', err)
      }
    }
    loadContents()
  }, [])

  // Compute max output tokens based on selected combine models (use minimum across all selected)
  const maxOutputTokensLimit = useMemo(() => {
    if (config.combine.selectedModels.length === 0) {
      return 128000 // Default max when no models selected
    }
    const limits = config.combine.selectedModels
      .map(m => models[m]?.max_output_tokens)
      .filter((limit): limit is number => limit !== null && limit !== undefined)

    if (limits.length === 0) {
      return 128000 // Default if no limits found
    }
    return Math.min(...limits)
  }, [config.combine.selectedModels, models])

  return (
    <Section
      title="Combine (Gold Standard)"
      icon={<CombineIcon className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
        {/* Max Tokens Slider */}
        <div className="pt-1" data-section="combine-max-tokens">
          <h4 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2">
            <Sliders className="w-4 h-4" /> Max Output Tokens
          </h4>
          <p className="text-xs text-gray-500 mb-2">
            Maximum tokens for the combine model output (includes reasoning tokens for reasoning models). Limit: {maxOutputTokensLimit.toLocaleString()}
          </p>
          <div className="flex items-center gap-4">
            <input
              type="range"
              min="4000"
              max={maxOutputTokensLimit}
              step="1000"
              value={Math.min(config.combine.maxTokens, maxOutputTokensLimit)}
              onChange={(e) => config.updateCombine({ maxTokens: parseInt(e.target.value) })}
              className="flex-1"
            />
            <span className="text-sm text-gray-300 w-20 text-right">{config.combine.maxTokens.toLocaleString()}</span>
          </div>
        </div>

        {/* Combine Instructions */}
        <div className="space-y-2 border-t border-gray-700 pt-4 mt-4" data-section="combine-instructions">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
              <FileText className="w-4 h-4" /> Combine Instructions
            </h4>
            <Link
                    to="/content"
              className="inline-flex items-center gap-1 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition-colors"
            >
              <ExternalLink className="w-3 h-3" />
              Library</Link>
          </div>
          {combineInstructionContents.length === 0 ? (
            <p className="text-xs text-gray-500">No combine instructions in library. <Link to="/content" className="text-purple-400 hover:text-purple-300">Create one →</Link></p>
          ) : (
            <select
              value={config.combine.combineInstructionsId || ''}
              onChange={(e) => config.updateCombine({ combineInstructionsId: e.target.value || null })}
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            >
              <option value="">-- Use Default --</option>
              {combineInstructionContents.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
        </div>
    </Section>
  )
}
