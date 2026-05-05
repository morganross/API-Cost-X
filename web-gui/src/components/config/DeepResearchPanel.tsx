import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Network, GitBranch, Clock } from 'lucide-react'
import { useConfigStore } from '../../stores/config'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function DeepResearchPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()

  return (
    <Section
      title="Deep Research (DR) Parameters"
      icon={<Network className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <p className="mb-4 text-sm text-gray-400">
        Runs automatically when one or more Deep Research model cards are checked.
      </p>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <GitBranch className="w-4 h-4" /> Search Tree Parameters
        </h4>

          <Slider
            label="Breadth (Topics per Level)"
            value={config.dr.breadth}
            onChange={(val) => config.updateDr({ breadth: val })}
            min={1}
            max={8}
            step={1}
            displayValue={`${config.dr.breadth} topics`}
          />

          <Slider
            label="Depth (Search Levels)"
            value={config.dr.depth}
            onChange={(val) => config.updateDr({ depth: val })}
            min={1}
            max={8}
            step={1}
            displayValue={`${config.dr.depth} levels`}
          />

          <Slider
            label="Max Results per Search"
            value={config.dr.maxResults}
            onChange={(val) => config.updateDr({ maxResults: val })}
            min={1}
            max={20}
            step={1}
            displayValue={`${config.dr.maxResults} results`}
          />

          <Slider
            label="Concurrency Limit"
            value={config.dr.concurrencyLimit}
            onChange={(val) => config.updateDr({ concurrencyLimit: val })}
            min={1}
            max={10}
            step={1}
            displayValue={`${config.dr.concurrencyLimit} concurrent`}
          />
      </div>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Clock className="w-4 h-4" /> Timeout & Retry
        </h4>

          <Slider
            label="Subprocess Timeout"
            value={config.dr.subprocessTimeoutMinutes}
            onChange={(val) => config.updateDr({ subprocessTimeoutMinutes: val })}
            min={10}
            max={45}
            step={5}
            displayValue={`${config.dr.subprocessTimeoutMinutes} minutes`}
          />
          <p className="text-xs text-gray-500 -mt-2">
            Kill hung Deep Research subprocess after this time (10-45 min)
          </p>

          <Slider
            label="Timeout Retries"
            value={config.dr.subprocessRetries}
            onChange={(val) => config.updateDr({ subprocessRetries: val })}
            min={0}
            max={3}
            step={1}
            displayValue={`${config.dr.subprocessRetries} ${config.dr.subprocessRetries === 1 ? 'retry' : 'retries'}`}
          />
          <p className="text-xs text-gray-500 -mt-2">
            Retry on timeout before marking as failed (0-3)
          </p>
      </div>
    </Section>
  )
}
