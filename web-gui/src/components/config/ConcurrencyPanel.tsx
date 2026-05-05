import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Zap, Timer, RefreshCw } from 'lucide-react'
import { useConfigStore } from '../../stores/config'

export function ConcurrencyPanel() {
  const config = useConfigStore()

  return (
    <Section
      title="Concurrency & Timing"
      icon={<Zap className="w-5 h-5" />}
      defaultExpanded={true}
    >
      {/* Main Concurrency Sliders */}
      <div className="space-y-4">
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Zap className="w-4 h-4" /> Concurrency Settings
          </h4>

          <Slider
            label="Generation Concurrency"
            value={config.concurrency.maxConcurrent}
            onChange={(val) => config.updateConcurrency({ maxConcurrent: val })}
            min={1}
            max={20}
            step={1}
            displayValue={`${config.concurrency.maxConcurrent} concurrent`}
          />

          <Slider
            label="Evaluation Concurrency"
            value={config.concurrency.evalConcurrency}
            onChange={(val) => config.updateConcurrency({ evalConcurrency: val })}
            min={1}
            max={50}
            step={1}
            displayValue={`${config.concurrency.evalConcurrency} concurrent`}
          />

          <Slider
            label="Launch Delay (seconds)"
            value={config.concurrency.launchDelay}
            onChange={(val) => config.updateConcurrency({ launchDelay: val })}
            min={0.1}
            max={100.0}
            step={0.1}
            displayValue={`${config.concurrency.launchDelay.toFixed(1)}s`}
          />
        </div>

        {/* Timeout Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <Timer className="w-4 h-4" /> Timeout Settings
          </h4>

          <div className="space-y-1">
            <div className="flex justify-between text-sm">
              <span className="text-gray-300">Request Timeout</span>
              <span className="font-mono text-blue-400">
                {config.concurrency.requestTimeout === null ? 'none' : `${config.concurrency.requestTimeout}s`}
              </span>
            </div>
            <input
              type="number"
              min="0"
              step="1"
              placeholder="None (no timeout)"
              value={config.concurrency.requestTimeout ?? ''}
              onChange={(e) => config.updateConcurrency({
                requestTimeout: e.target.value === '' ? null : Number(e.target.value)
              })}
              className="w-full rounded-lg border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500">Timeout for generation requests (blank = no limit)</p>
          </div>
        </div>

        {/* FPF API Retry Settings */}
        <div className="space-y-3 border-t border-gray-700 pt-4">
          <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
            <RefreshCw className="w-4 h-4" /> FPF API Retry Settings
          </h4>
          <p className="text-xs text-gray-500">Retries for transient API errors (429 rate limits, 500s server errors)</p>

          <Slider
            label="Max Retries"
            value={config.concurrency.fpfMaxRetries}
            onChange={(val) => config.updateConcurrency({ fpfMaxRetries: val })}
            min={0}
            max={50}
            step={1}
            displayValue={`${config.concurrency.fpfMaxRetries} retries`}
          />

          <Slider
            label="Retry Delay (seconds)"
            value={config.concurrency.fpfRetryDelay}
            onChange={(val) => config.updateConcurrency({ fpfRetryDelay: val })}
            min={0.5}
            max={121}
            step={0.5}
            displayValue={`${config.concurrency.fpfRetryDelay.toFixed(1)}s`}
          />
        </div>
      </div>
    </Section>
  )
}
