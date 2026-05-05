import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Checkbox } from '../ui/checkbox'
import { Settings, FolderOpen, ScrollText, Target } from 'lucide-react'
import { useConfigStore } from '../../stores/config'

export function GeneralPanel() {
  const config = useConfigStore()

  return (
    <Section
      title="General Settings"
      icon={<Settings className="w-5 h-5" />}
      defaultExpanded={true}
    >
      <div className="space-y-4">
        {/* Main Run Settings */}
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Settings className="w-4 h-4" /> Run Configuration
          </h4>

          <Slider
            label="Total Iterations"
            value={config.general.iterations}
            onChange={(val) => config.updateGeneral({ iterations: val })}
            min={1}
            max={9}
            step={1}
            displayValue={`${config.general.iterations} iterations`}
          />
        </div>

        {/* Generation Quality Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Target className="w-4 h-4" /> Generation Quality
          </h4>

          <Checkbox
            checked={config.general.exposeCriteriaToGenerators}
            onChange={(val) => config.updateGeneral({ exposeCriteriaToGenerators: val })}
            label="Expose Evaluation Criteria to Generators"
          />
          <p className="text-xs text-gray-500 ml-6">
            When enabled, generators will see the evaluation criteria they'll be judged on,
            helping them optimize output quality.
          </p>
        </div>

        {/* Output Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <FolderOpen className="w-4 h-4" /> Output Settings
          </h4>

          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-400">Output Filename Template</label>
            <input
              type="text"
              value={config.general.outputFilenameTemplate}
              onChange={(e) => config.updateGeneral({ outputFilenameTemplate: e.target.value })}
              className="w-full rounded-lg border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="{source_doc_name}_{winner_model}_{timestamp}"
            />
            <p className="text-xs text-gray-500">
              Variables: {"{source_doc_name}"}, {"{winner_model}"}, {"{timestamp}"}, {"{iteration}"}, {"{model}"}. Leave empty for default naming.
            </p>
          </div>
        </div>

        {/* Logging Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <ScrollText className="w-4 h-4" /> Logging
          </h4>

          <Checkbox
            checked={config.general.saveRunLogs}
            onChange={(checked) => config.updateGeneral({ saveRunLogs: checked })}
            label="Save Run Logs"
          />
          <p className="text-xs text-gray-500 ml-6">
            Saves the full per-run user log to disk for this preset. The log viewer detail toggle only changes what you see, not what gets saved.
          </p>
        </div>
      </div>
    </Section>
  )
}
