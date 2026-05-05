import {
  Bot,
  BrainCircuit,
  Clock3,
  Database,
  FileCode2,
  Globe,
  Search,
  Settings2,
  SlidersHorizontal,
} from 'lucide-react'
import { Section } from '../ui/section'
import { Checkbox } from '../ui/checkbox'
import { Select } from '../ui/select'
import { useConfigStore } from '../../stores/config'
import { useModelCatalog } from '../../stores/modelCatalog'
import {
  AIQ_AGENT_TYPE_OPTIONS,
  AIQ_DATA_SOURCE_OPTIONS,
  AIQ_KNOWLEDGE_BACKEND_OPTIONS,
  AIQ_PROFILE_OPTIONS,
} from '../../data/aiqOptions'

interface PanelProps {
  defaultExpanded?: boolean
  expanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
}

const inputClassName =
  'w-full rounded-lg border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-200 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-60'

const textareaClassName = inputClassName
const twoColumnGridClassName = 'grid gap-3 lg:grid-cols-2'

function clampToInteger(value: string, fallback: number, minimum: number) {
  if (value.trim() === '') return fallback
  const parsed = Number(value)
  if (Number.isNaN(parsed)) return fallback
  return Math.max(minimum, Math.round(parsed))
}

function clampToFloat(value: string, fallback: number, minimum: number, maximum: number) {
  if (value.trim() === '') return fallback
  const parsed = Number(value)
  if (Number.isNaN(parsed)) return fallback
  return Math.min(maximum, Math.max(minimum, parsed))
}

interface NumberFieldProps {
  label: string
  value: number
  min?: number
  step?: number
  onChange: (value: number) => void
  dataTestId?: string
}

function NumberField({ label, value, min = 0, step = 1, onChange, dataTestId }: NumberFieldProps) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium leading-5 text-gray-400">{label}</label>
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(clampToInteger(event.target.value, value, min))}
        className={inputClassName}
        data-testid={dataTestId}
      />
    </div>
  )
}

interface FloatFieldProps {
  label: string
  value: number
  min?: number
  max?: number
  step?: number
  onChange: (value: number) => void
  dataTestId?: string
}

function FloatField({ label, value, min = 0, max = 1, step = 0.1, onChange, dataTestId }: FloatFieldProps) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium leading-5 text-gray-400">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(clampToFloat(event.target.value, value, min, max))}
        className={inputClassName}
        data-testid={dataTestId}
      />
    </div>
  )
}

interface LlmCardProps {
  title: string
  description: string
  assignedModels: string[]
  assignedModelHint: string
  temperature?: number
  onTemperatureChange?: (value: number) => void
  topP?: number
  onTopPChange?: (value: number) => void
  maxTokens?: number
  onMaxTokensChange?: (value: number) => void
  retries?: number
  onRetriesChange?: (value: number) => void
  enableThinking?: boolean
  onEnableThinkingChange?: (value: boolean) => void
  testPrefix: string
}

function LlmCard({
  title,
  description,
  assignedModels,
  assignedModelHint,
  temperature,
  onTemperatureChange,
  topP,
  onTopPChange,
  maxTokens,
  onMaxTokensChange,
  retries,
  onRetriesChange,
  enableThinking,
  onEnableThinkingChange,
  testPrefix,
}: LlmCardProps) {
  return (
    <div className="space-y-4 rounded-xl border border-gray-700 bg-gray-800/40 p-4">
      <div className="space-y-1">
        <h5 className="text-sm font-semibold text-gray-200">{title}</h5>
        <p className="text-xs text-gray-500">{description}</p>
      </div>

      <div className="space-y-1.5">
        <label className="block text-sm font-medium leading-5 text-gray-400">
          {assignedModels.length > 1 ? 'Assigned Models' : 'Assigned Model'}
        </label>
        <div className="rounded-lg border border-gray-700 bg-gray-900/40 px-3 py-2 text-sm text-gray-200">
          {assignedModels.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {assignedModels.map((model) => (
                <span
                  key={`${testPrefix}-${model}`}
                  className="rounded-full border border-gray-600 bg-gray-800 px-2 py-1 text-[11px] font-mono text-gray-100"
                >
                  {model}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-gray-400">{assignedModelHint}</span>
          )}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {onTemperatureChange && temperature != null ? (
          <FloatField
            label="Temperature"
            value={temperature}
            min={0}
            max={2}
            step={0.1}
            onChange={onTemperatureChange}
            dataTestId={`${testPrefix}-temperature`}
          />
        ) : null}

        {onTopPChange && topP != null ? (
          <FloatField
            label="Top P"
            value={topP}
            min={0}
            max={1}
            step={0.1}
            onChange={onTopPChange}
            dataTestId={`${testPrefix}-top-p`}
          />
        ) : null}

        {onMaxTokensChange && maxTokens != null ? (
          <NumberField
            label="Max Tokens"
            value={maxTokens}
            min={1}
            step={1}
            onChange={onMaxTokensChange}
            dataTestId={`${testPrefix}-max-tokens`}
          />
        ) : null}

        {onRetriesChange && retries != null ? (
          <NumberField
            label="Retries"
            value={retries}
            min={0}
            step={1}
            onChange={onRetriesChange}
            dataTestId={`${testPrefix}-retries`}
          />
        ) : null}
      </div>

      {onEnableThinkingChange && enableThinking != null ? (
        <div className="rounded-lg border border-gray-700 bg-gray-900/30 p-3">
          <Checkbox
            checked={enableThinking}
            onChange={onEnableThinkingChange}
            label="Enable thinking"
            dataTestId={`${testPrefix}-enable-thinking`}
          />
        </div>
      ) : null}
    </div>
  )
}

export function AiqPanel({ defaultExpanded, expanded, onExpandedChange }: PanelProps) {
  const config = useConfigStore()
  const { models, fpfModels, fpfFreeModels } = useModelCatalog()
  const supportedProviders = new Set(['openai', 'anthropic', 'google', 'openrouter'])
  const smallModelOptions = Array.from(new Set([...fpfModels, ...fpfFreeModels]))
    .filter((model) => {
      const provider = model.split(':', 1)[0]
      return supportedProviders.has(provider) && models[model]?.dr_native !== true
    })
    .sort((a, b) => a.localeCompare(b))
    .map((model) => ({ value: model, label: model }))
  const smallModelProviders = Array.from(
    new Set(smallModelOptions.map((option) => option.value.split(':', 1)[0]))
  )
    .sort((a, b) => a.localeCompare(b))
    .map((provider) => ({ value: provider, label: provider }))
  const selectedSmallProvider = config.aiq.smallModel.includes(':')
    ? config.aiq.smallModel.split(':', 1)[0]
    : (smallModelProviders[0]?.value ?? '')
  const filteredSmallModelOptions = smallModelOptions.filter((option) =>
    selectedSmallProvider ? option.value.startsWith(`${selectedSmallProvider}:`) : true
  )
  const selectedBigModels = [...config.aiq.selectedModels].sort((a, b) => a.localeCompare(b))

  return (
    <Section
      title="AI-Q Parameters"
      icon={<Bot className="w-5 h-5" />}
      defaultExpanded={defaultExpanded}
      expanded={expanded}
      onExpandedChange={onExpandedChange}
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold text-blue-200">
                <Globe className="w-4 h-4" />
                <span>AI-Q runtime config</span>
              </div>
              <p className="text-xs text-blue-100/80">
                AI-Q does not have its own live model registry API. These controls map to the running AI-Q YAML
                profile and per-job overrides sent to the local AI-Q service.
              </p>
              <p className="text-xs text-blue-100/80">
                In ACM, the preset table now selects the AI-Q big model per run. The small model is chosen below and
                applied only to the intent and summary llm configs.
              </p>
            </div>
            <span className="rounded-full border border-cyan-400/40 bg-cyan-500/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-cyan-100">
              {config.aiq.enabled ? 'Enabled in table' : 'Table only'}
            </span>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <Search className="w-4 h-4 text-blue-400" />
            <span>APICostX Query & Profile</span>
          </h4>

          <p className="text-xs text-gray-400">
            APICostX builds the AI-Q query automatically from generation instructions plus input source text.
          </p>

          <div className={twoColumnGridClassName}>
            <Select
              label="Profile"
              value={config.aiq.profile}
              onChange={(profile) => config.updateAiq({ profile })}
              options={AIQ_PROFILE_OPTIONS}
              data-testid="aiq-profile"
            />

            <Select
              label="Agent Type"
              value={config.aiq.agentType}
              onChange={(agentType) => config.updateAiq({ agentType })}
              options={AIQ_AGENT_TYPE_OPTIONS}
              data-testid="aiq-agent-type"
            />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <FileCode2 className="w-4 h-4 text-blue-400" />
            <span>Report Output</span>
          </h4>

          <div className={twoColumnGridClassName}>
            <NumberField
              label="Minimum Words"
              value={config.aiq.reportMinWords}
              min={100}
              onChange={(reportMinWords) => config.updateAiq({ reportMinWords })}
              dataTestId="aiq-report-min-words"
            />
            <NumberField
              label="Target Max Words"
              value={config.aiq.reportMaxWords}
              min={100}
              onChange={(reportMaxWords) => config.updateAiq({ reportMaxWords })}
              dataTestId="aiq-report-max-words"
            />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-cyan-700/40 bg-cyan-950/20 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-cyan-100">
            <BrainCircuit className="w-4 h-4 text-cyan-300" />
            <span>Model Strategy</span>
          </h4>

          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-400">Big Model Runs</label>
            <div className="rounded-lg border border-gray-700 bg-gray-900/40 px-3 py-2 text-sm text-gray-200">
              {selectedBigModels.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {selectedBigModels.map((model) => (
                    <span
                      key={model}
                      className="rounded-full border border-cyan-700 bg-cyan-950/60 px-2 py-1 text-[11px] font-mono text-cyan-100"
                    >
                      {model}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="text-gray-400">
                  Select one or more models in the AI-Q column on the presets table.
                </span>
              )}
            </div>
          </div>

          <Select
            label="Small Model Provider"
            value={selectedSmallProvider}
            onChange={(provider) => {
              const firstModel = smallModelOptions.find((option) => option.value.startsWith(`${provider}:`))
              config.updateAiq({ smallModel: firstModel?.value ?? '' })
            }}
            options={smallModelProviders}
            placeholder="Select a provider"
            data-testid="aiq-small-model-provider"
          />

          <Select
            label="Small Model"
            value={config.aiq.smallModel}
            onChange={(smallModel) => config.updateAiq({ smallModel })}
            options={filteredSmallModelOptions}
            placeholder="Select a support model from the FPF registry"
            data-testid="aiq-small-model"
          />

          <p className="text-xs text-cyan-50/80">
            This one small model is assigned only to nemotron_llm_intent and summary_llm at runtime.
          </p>
          <p className="text-xs text-cyan-50/80">
            Table-selected big models drive nemotron_nano_llm and gpt_oss_llm for each run.
          </p>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <SlidersHorizontal className="w-4 h-4 text-blue-400" />
            <span>AI-Q LLM Configurations</span>
          </h4>

          <div className="grid gap-4">
            <LlmCard
              title="nemotron_llm_intent"
              description="Used by intent_classifier.llm. The model comes from Small Model above."
              assignedModels={config.aiq.smallModel ? [config.aiq.smallModel] : []}
              assignedModelHint="Select a Small Model above."
              temperature={config.aiq.intentTemperature}
              onTemperatureChange={(intentTemperature) => config.updateAiq({ intentTemperature })}
              topP={config.aiq.intentTopP}
              onTopPChange={(intentTopP) => config.updateAiq({ intentTopP })}
              maxTokens={config.aiq.intentMaxTokens}
              onMaxTokensChange={(intentMaxTokens) => config.updateAiq({ intentMaxTokens })}
              retries={config.aiq.intentRetries}
              onRetriesChange={(intentRetries) => config.updateAiq({ intentRetries })}
              enableThinking={config.aiq.intentEnableThinking}
              onEnableThinkingChange={(intentEnableThinking) => config.updateAiq({ intentEnableThinking })}
              testPrefix="aiq-intent"
            />

            <LlmCard
              title="nemotron_nano_llm"
              description="Used by clarifier_agent.llm, clarifier_agent.planner_llm, shallow_research_agent.llm, and deep_research_agent.researcher_llm. The models come from the AI-Q column in the table view."
              assignedModels={selectedBigModels}
              assignedModelHint="Select one or more models in the AI-Q column on the presets table."
              temperature={config.aiq.nanoTemperature}
              onTemperatureChange={(nanoTemperature) => config.updateAiq({ nanoTemperature })}
              topP={config.aiq.nanoTopP}
              onTopPChange={(nanoTopP) => config.updateAiq({ nanoTopP })}
              maxTokens={config.aiq.nanoMaxTokens}
              onMaxTokensChange={(nanoMaxTokens) => config.updateAiq({ nanoMaxTokens })}
              retries={config.aiq.nanoRetries}
              onRetriesChange={(nanoRetries) => config.updateAiq({ nanoRetries })}
              enableThinking={config.aiq.nanoEnableThinking}
              onEnableThinkingChange={(nanoEnableThinking) => config.updateAiq({ nanoEnableThinking })}
              testPrefix="aiq-nano"
            />

            <LlmCard
              title="gpt_oss_llm"
              description="Used by deep_research_agent.orchestrator_llm and deep_research_agent.planner_llm. The models come from the AI-Q column in the table view."
              assignedModels={selectedBigModels}
              assignedModelHint="Select one or more models in the AI-Q column on the presets table."
              temperature={config.aiq.gptOssTemperature}
              onTemperatureChange={(gptOssTemperature) => config.updateAiq({ gptOssTemperature })}
              topP={config.aiq.gptOssTopP}
              onTopPChange={(gptOssTopP) => config.updateAiq({ gptOssTopP })}
              maxTokens={config.aiq.gptOssMaxTokens}
              onMaxTokensChange={(gptOssMaxTokens) => config.updateAiq({ gptOssMaxTokens })}
              retries={config.aiq.gptOssMaxRetries}
              onRetriesChange={(gptOssMaxRetries) => config.updateAiq({ gptOssMaxRetries })}
              testPrefix="aiq-gpt-oss"
            />

            <LlmCard
              title="summary_llm"
              description="Used by knowledge_search.summary_model. The model comes from Small Model above."
              assignedModels={config.aiq.smallModel ? [config.aiq.smallModel] : []}
              assignedModelHint="Select a Small Model above."
              temperature={config.aiq.summaryTemperature}
              onTemperatureChange={(summaryTemperature) => config.updateAiq({ summaryTemperature })}
              maxTokens={config.aiq.summaryMaxTokens}
              onMaxTokensChange={(summaryMaxTokens) => config.updateAiq({ summaryMaxTokens })}
              retries={config.aiq.summaryRetries}
              onRetriesChange={(summaryRetries) => config.updateAiq({ summaryRetries })}
              testPrefix="aiq-summary"
            />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <Database className="w-4 h-4 text-blue-400" />
            <span>Search & Knowledge</span>
          </h4>

          <div className={twoColumnGridClassName}>
            <NumberField
              label="Web Search Max Results"
              value={config.aiq.webSearchMaxResults}
              min={1}
              onChange={(webSearchMaxResults) => config.updateAiq({ webSearchMaxResults })}
              dataTestId="aiq-web-search-max-results"
            />
            <NumberField
              label="Web Search Max Content Length"
              value={config.aiq.webSearchMaxContentLength}
              min={1}
              onChange={(webSearchMaxContentLength) => config.updateAiq({ webSearchMaxContentLength })}
              dataTestId="aiq-web-search-max-content-length"
            />
            <NumberField
              label="Advanced Web Search Max Results"
              value={config.aiq.advancedWebSearchMaxResults}
              min={1}
              onChange={(advancedWebSearchMaxResults) => config.updateAiq({ advancedWebSearchMaxResults })}
              dataTestId="aiq-advanced-web-search-max-results"
            />
          </div>

          <div className={twoColumnGridClassName}>
            <Select
              label="Knowledge Backend"
              value={config.aiq.knowledgeBackend}
              onChange={(knowledgeBackend) => config.updateAiq({ knowledgeBackend })}
              options={AIQ_KNOWLEDGE_BACKEND_OPTIONS}
              data-testid="aiq-knowledge-backend"
            />
            <div className="space-y-1">
              <label className="block text-sm font-medium text-gray-400">Collection Name</label>
              <input
                type="text"
                value={config.aiq.knowledgeCollectionName}
                onChange={(event) => config.updateAiq({ knowledgeCollectionName: event.target.value })}
                className={inputClassName}
                data-testid="aiq-knowledge-collection-name"
              />
            </div>
            <NumberField
              label="Knowledge Top K"
              value={config.aiq.knowledgeTopK}
              min={1}
              onChange={(knowledgeTopK) => config.updateAiq({ knowledgeTopK })}
              dataTestId="aiq-knowledge-top-k"
            />
            <NumberField
              label="Knowledge Timeout Seconds"
              value={config.aiq.knowledgeTimeoutSeconds}
              min={1}
              onChange={(knowledgeTimeoutSeconds) => config.updateAiq({ knowledgeTimeoutSeconds })}
              dataTestId="aiq-knowledge-timeout-seconds"
            />
          </div>

          <div className={twoColumnGridClassName}>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.advancedWebSearchAdvancedSearch}
                onChange={(advancedWebSearchAdvancedSearch) => config.updateAiq({ advancedWebSearchAdvancedSearch })}
                label="Advanced web search"
                dataTestId="aiq-advanced-web-search"
              />
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.knowledgeGenerateSummary}
                onChange={(knowledgeGenerateSummary) => config.updateAiq({ knowledgeGenerateSummary })}
                label="Generate knowledge summaries"
                dataTestId="aiq-knowledge-generate-summary"
              />
            </div>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <BrainCircuit className="w-4 h-4 text-blue-400" />
            <span>Agent Behavior</span>
          </h4>

          <div className={twoColumnGridClassName}>
            <NumberField
              label="Clarifier Max Turns"
              value={config.aiq.clarifierMaxTurns}
              min={1}
              onChange={(clarifierMaxTurns) => config.updateAiq({ clarifierMaxTurns })}
              dataTestId="aiq-clarifier-max-turns"
            />
            <NumberField
              label="Clarifier Log Response Max Chars"
              value={config.aiq.clarifierLogResponseMaxChars}
              min={1}
              onChange={(clarifierLogResponseMaxChars) => config.updateAiq({ clarifierLogResponseMaxChars })}
              dataTestId="aiq-clarifier-log-response-max-chars"
            />
            <NumberField
              label="Deep Research Max Loops"
              value={config.aiq.deepResearchMaxLoops}
              min={1}
              onChange={(deepResearchMaxLoops) => config.updateAiq({ deepResearchMaxLoops })}
              dataTestId="aiq-deep-research-max-loops"
            />
            <NumberField
              label="Shallow Research Max LLM Turns"
              value={config.aiq.shallowResearchMaxLlmTurns}
              min={1}
              onChange={(shallowResearchMaxLlmTurns) => config.updateAiq({ shallowResearchMaxLlmTurns })}
              dataTestId="aiq-shallow-research-max-llm-turns"
            />
            <NumberField
              label="Shallow Research Max Tool Iterations"
              value={config.aiq.shallowResearchMaxToolIterations}
              min={1}
              onChange={(shallowResearchMaxToolIterations) => config.updateAiq({ shallowResearchMaxToolIterations })}
              dataTestId="aiq-shallow-research-max-tool-iterations"
            />
          </div>

          <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
            <Checkbox
              checked={config.aiq.clarifierEnablePlanApproval}
              onChange={(clarifierEnablePlanApproval) => config.updateAiq({ clarifierEnablePlanApproval })}
              label="Clarifier requires plan approval"
              dataTestId="aiq-clarifier-enable-plan-approval"
            />
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <Clock3 className="w-4 h-4 text-blue-400" />
            <span>Workflow & Runtime</span>
          </h4>

          <div className={twoColumnGridClassName}>
            <Select
              label="Data Sources"
              value={config.aiq.dataSources[0] || 'web'}
              onChange={(dataSource) => config.updateAiq({ dataSources: [dataSource], webOnly: dataSource === 'web' })}
              options={AIQ_DATA_SOURCE_OPTIONS}
              data-testid="aiq-data-sources"
            />
            <NumberField
              label="Timeout Seconds"
              value={config.aiq.timeoutSeconds ?? 1200}
              min={60}
              onChange={(timeoutSeconds) => config.updateAiq({ timeoutSeconds })}
              dataTestId="aiq-timeout-seconds"
            />
            <NumberField
              label="Job Expiry Seconds"
              value={config.aiq.jobExpirySeconds ?? 3600}
              min={60}
              onChange={(jobExpirySeconds) => config.updateAiq({ jobExpirySeconds })}
              dataTestId="aiq-job-expiry-seconds"
            />
          </div>

          <div className={twoColumnGridClassName}>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.webOnly}
                onChange={(webOnly) => config.updateAiq({ webOnly })}
                label="Web-only mode"
                dataTestId="aiq-web-only"
              />
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.preserveDebugArtifacts}
                onChange={(preserveDebugArtifacts) => config.updateAiq({ preserveDebugArtifacts })}
                label="Preserve debug artifacts"
                dataTestId="aiq-preserve-debug-artifacts"
              />
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.workflowEnableEscalation}
                onChange={(workflowEnableEscalation) => config.updateAiq({ workflowEnableEscalation })}
                label="Enable escalation"
                dataTestId="aiq-workflow-enable-escalation"
              />
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.workflowEnableClarifier}
                onChange={(workflowEnableClarifier) => config.updateAiq({ workflowEnableClarifier })}
                label="Enable clarifier"
                dataTestId="aiq-workflow-enable-clarifier"
              />
            </div>
            <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-3">
              <Checkbox
                checked={config.aiq.workflowUseAsyncDeepResearch}
                onChange={(workflowUseAsyncDeepResearch) => config.updateAiq({ workflowUseAsyncDeepResearch })}
                label="Use async deep research"
                dataTestId="aiq-workflow-use-async-deep-research"
              />
            </div>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <Settings2 className="w-4 h-4 text-blue-400" />
            <span>Backend Notes</span>
          </h4>

          <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-100/90">
            <p>
              AI-Q does not expose a separate model registry endpoint. The dropdowns here are sourced from the shipped
              AI-Q YAML profiles and the live AI-Q service now applies these bindings plus structured config overrides per
              job.
            </p>
            <p className="mt-2">
              Profile selection is now meaningful on the AI-Q side, and the rest of these controls are serialized as
              per-job overrides against that selected profile.
            </p>
          </div>
        </div>

        <div className="space-y-3 rounded-xl border border-gray-700 bg-gray-900/30 p-4">
          <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-200">
            <FileCode2 className="w-4 h-4 text-blue-400" />
            <span>Advanced Overrides</span>
          </h4>

          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-400">Advanced JSON Overrides</label>
            <textarea
              value={config.aiq.advancedYamlOverrides}
              onChange={(event) => config.updateAiq({ advancedYamlOverrides: event.target.value })}
              className={textareaClassName}
              rows={6}
              placeholder={'{\n  "research": {\n    "max_steps": 8\n  }\n}'}
              data-testid="aiq-advanced-overrides"
            />
          </div>
        </div>
      </div>
    </Section>
  )
}
