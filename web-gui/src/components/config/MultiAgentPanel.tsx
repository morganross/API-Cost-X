import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Checkbox, CheckboxGroup } from '../ui/checkbox'
import { Select } from '../ui/select'
import { Users, MessageSquare } from 'lucide-react'
import { useConfigStore } from '../../stores/config'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function MultiAgentPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()

  return (
    <Section
      title="Multi-Agent (MA) Configuration"
      icon={<Users className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <CheckboxGroup
        title="Enable Multi-Agent"
        enabled={config.ma.enabled}
        onEnabledChange={(enabled) => config.updateMa({ enabled })}
      >
        {/* Agent Parameters */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Users className="w-4 h-4" /> Agent Parameters
          </h4>

          <Slider
            label="Max Agents"
            value={config.ma.maxAgents}
            onChange={(val) => config.updateMa({ maxAgents: val })}
            min={2}
            max={10}
            step={1}
            displayValue={`${config.ma.maxAgents} agents`}
          />

          <Slider
            label="Max Rounds"
            value={config.ma.maxRounds}
            onChange={(val) => config.updateMa({ maxRounds: val })}
            min={1}
            max={10}
            step={1}
            displayValue={`${config.ma.maxRounds} rounds`}
          />

          <Select
            label="Communication Style"
            value={config.ma.communicationStyle}
            onChange={(val) => config.updateMa({ communicationStyle: val })}
            options={[
              { value: 'sequential', label: 'Sequential' },
              { value: 'parallel', label: 'Parallel' },
              { value: 'hierarchical', label: 'Hierarchical' },
              { value: 'round-robin', label: 'Round Robin' },
            ]}
          />
        </div>

        {/* Collaboration Options */}
        <div className="space-y-2 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 mb-2 flex items-center gap-2">
            <MessageSquare className="w-4 h-4" /> Collaboration Options
          </h4>
          <div className="grid grid-cols-2 gap-2">
            <Checkbox
              checked={config.ma.enableConsensus}
              onChange={(val) => config.updateMa({ enableConsensus: val })}
              label="Enable Consensus"
            />
            <Checkbox
              checked={config.ma.enableDebate}
              onChange={(val) => config.updateMa({ enableDebate: val })}
              label="Enable Debate Mode"
            />
            <Checkbox
              checked={config.ma.enableVoting}
              onChange={(val) => config.updateMa({ enableVoting: val })}
              label="Enable Voting"
            />
          </div>
        </div>
      </CheckboxGroup>
    </Section>
  )
}
