import { Section } from '../ui/section'
import { Slider } from '../ui/slider'
import { Checkbox } from '../ui/checkbox'
import { Settings, Zap, Clock, Search, FileText, Globe } from 'lucide-react'
import { useConfigStore } from '../../stores/config'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

export function GptrParamsPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()

  return (
    <Section
      title="GPT-Researcher (GPTR) Parameters"
      icon={<Settings className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <p className="mb-4 text-sm text-gray-400">
        Runs automatically when one or more GPT-Researcher model cards are checked.
      </p>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Search className="w-4 h-4" /> Research Settings
        </h4>

          <Slider
            label="Temperature"
            value={config.gptr.temperature}
            onChange={(val) => config.updateGptr({ temperature: val })}
            min={0}
            max={1}
            step={0.1}
            displayValue={config.gptr.temperature.toFixed(1)}
          />

          <Slider
            label="Max Search Results Per Query"
            value={config.gptr.maxSearchResultsPerQuery}
            onChange={(val) => config.updateGptr({ maxSearchResultsPerQuery: val })}
            min={1}
            max={20}
            step={1}
            displayValue={`${config.gptr.maxSearchResultsPerQuery} results`}
          />

          <Slider
            label="Total Words Target"
            value={config.gptr.totalWords}
            onChange={(val) => config.updateGptr({ totalWords: val })}
            min={500}
            max={10000}
            step={500}
            displayValue={`${config.gptr.totalWords.toLocaleString()} words`}
          />

          <Slider
            label="Max Iterations"
            value={config.gptr.maxIterations}
            onChange={(val) => config.updateGptr({ maxIterations: val })}
            min={1}
            max={10}
            step={1}
            displayValue={`${config.gptr.maxIterations} iterations`}
          />

          <Slider
            label="Max Subtopics"
            value={config.gptr.maxSubtopics}
            onChange={(val) => config.updateGptr({ maxSubtopics: val })}
            min={1}
            max={15}
            step={1}
            displayValue={`${config.gptr.maxSubtopics} subtopics`}
          />

          <Slider
            label="Summary Token Limit"
            value={config.gptr.summaryTokenLimit}
            onChange={(val) => config.updateGptr({ summaryTokenLimit: val })}
            min={500}
            max={8000}
            step={500}
            displayValue={`${config.gptr.summaryTokenLimit.toLocaleString()} tokens`}
          />
      </div>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <FileText className="w-4 h-4" /> Report Options
        </h4>

          <div>
            <label className="text-sm font-medium text-gray-300 mb-2 block">
              Report Type
            </label>
            <select
              value={config.gptr.reportType}
              onChange={(e) => config.updateGptr({ reportType: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="research_report">Research Report - Comprehensive analysis</option>
              <option value="detailed_report">Detailed Report - In-depth coverage</option>
              <option value="quick_report">Quick Report - Brief summary</option>
              <option value="outline_report">Outline Report - Structure only</option>
              <option value="resource_report">Resource Report - Source compilation</option>
              <option value="subtopic_report">Subtopic Report - Focused analysis</option>
            </select>
          </div>

          <div>
            <label className="text-sm font-medium text-gray-300 mb-2 block">
              Report Source
            </label>
            <select
              value={config.gptr.reportSource}
              onChange={(e) => config.updateGptr({ reportSource: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="web">Web - Search the internet</option>
              <option value="local">Local - Use local documents only</option>
              <option value="hybrid">Hybrid - Combine web and local</option>
            </select>
          </div>

          <div>
            <label className="text-sm font-medium text-gray-300 mb-2 block">
              Tone
            </label>
            <select
              value={config.gptr.tone}
              onChange={(e) => config.updateGptr({ tone: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="Objective">Objective - Neutral and factual</option>
              <option value="Formal">Formal - Professional language</option>
              <option value="Analytical">Analytical - Data-driven</option>
              <option value="Informative">Informative - Educational</option>
              <option value="Persuasive">Persuasive - Convincing</option>
              <option value="Explanatory">Explanatory - Clarifying</option>
            </select>
          </div>
      </div>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Globe className="w-4 h-4" /> Web Scraping
        </h4>

          <div>
            <label className="text-sm font-medium text-gray-300 mb-2 block">
              Search Retriever
            </label>
            <select
              value={config.gptr.retriever}
              onChange={(e) => config.updateGptr({ retriever: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 text-gray-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="tavily">Tavily - AI-optimized search</option>
              <option value="duckduckgo">DuckDuckGo - Privacy-focused</option>
              <option value="google">Google - Comprehensive results</option>
              <option value="bing">Bing - Microsoft search</option>
              <option value="searx">SearX - Meta search engine</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4 mt-3">
            <Checkbox
              checked={config.gptr.scrapeUrls}
              onChange={(checked) => config.updateGptr({ scrapeUrls: checked })}
              label="Scrape URLs"
            />
            <Checkbox
              checked={config.gptr.addSourceUrls}
              onChange={(checked) => config.updateGptr({ addSourceUrls: checked })}
              label="Add Source URLs"
            />
            <Checkbox
              checked={config.gptr.followLinks}
              onChange={(checked) => config.updateGptr({ followLinks: checked })}
              label="Follow Links"
            />
            <Checkbox
              checked={config.gptr.verboseMode}
              onChange={(checked) => config.updateGptr({ verboseMode: checked })}
              label="Verbose Mode"
            />
          </div>
      </div>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Zap className="w-4 h-4" /> Token Limits
        </h4>

          <Slider
            label="Fast LLM Token Limit"
            value={config.gptr.fastLlmTokenLimit}
            onChange={(val) => config.updateGptr({ fastLlmTokenLimit: val })}
            min={1000}
            max={32000}
            step={1000}
            displayValue={`${config.gptr.fastLlmTokenLimit.toLocaleString()} tokens`}
          />

          <Slider
            label="Smart LLM Token Limit"
            value={config.gptr.smartLlmTokenLimit}
            onChange={(val) => config.updateGptr({ smartLlmTokenLimit: val })}
            min={1000}
            max={128000}
            step={1000}
            displayValue={`${config.gptr.smartLlmTokenLimit.toLocaleString()} tokens`}
          />

          <Slider
            label="Strategic LLM Token Limit"
            value={config.gptr.strategicLlmTokenLimit}
            onChange={(val) => config.updateGptr({ strategicLlmTokenLimit: val })}
            min={1000}
            max={200000}
            step={1000}
            displayValue={`${config.gptr.strategicLlmTokenLimit.toLocaleString()} tokens`}
          />

          <Slider
            label="Browse Chunk Max Length"
            value={config.gptr.browseChunkMaxLength}
            onChange={(val) => config.updateGptr({ browseChunkMaxLength: val })}
            min={1000}
            max={20000}
            step={500}
            displayValue={`${config.gptr.browseChunkMaxLength.toLocaleString()} chars`}
          />
      </div>

      <div className="space-y-3 border-t border-gray-700 pt-4">
        <h4 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
          <Clock className="w-4 h-4" /> Timeout & Retry
        </h4>

          <Slider
            label="Subprocess Timeout"
            value={config.gptr.subprocessTimeoutMinutes}
            onChange={(val) => config.updateGptr({ subprocessTimeoutMinutes: val })}
            min={10}
            max={45}
            step={5}
            displayValue={`${config.gptr.subprocessTimeoutMinutes} minutes`}
          />
          <p className="text-xs text-gray-500 -mt-2">
            Kill hung GPTR subprocess after this time (10-45 min)
          </p>

          <Slider
            label="Timeout Retries"
            value={config.gptr.subprocessRetries}
            onChange={(val) => config.updateGptr({ subprocessRetries: val })}
            min={0}
            max={3}
            step={1}
            displayValue={`${config.gptr.subprocessRetries} ${config.gptr.subprocessRetries === 1 ? 'retry' : 'retries'}`}
          />
          <p className="text-xs text-gray-500 -mt-2">
            Retry on timeout before marking as failed (0-3)
          </p>
      </div>
    </Section>
  )
}
