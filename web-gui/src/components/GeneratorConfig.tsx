import { useState } from 'react'
import { Info } from 'lucide-react'

type GeneratorAdapter = 'fpf' | 'gptr'

export default function GeneratorConfig() {
  const [adapter, setAdapter] = useState<GeneratorAdapter>('fpf')
  const [model, setModel] = useState('gpt-4o')
  const [temperature, setTemperature] = useState(0.7)
  const [maxTokens, setMaxTokens] = useState(4096)
  const [includeWebSearch, setIncludeWebSearch] = useState(true)

  return (
    <div className="space-y-4 pt-4">
      {/* Adapter Selection */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">
          Generator Adapter
        </label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setAdapter('fpf')}
            className={`p-3 border rounded-md text-left transition-colors ${
              adapter === 'fpf'
                ? 'border-primary bg-primary/10'
                : 'border-border hover:bg-accent'
            }`}
          >
            <div className="font-medium text-sm text-foreground">FilePromptForge</div>
            <div className="text-xs text-muted-foreground">
              Document-focused prompting
            </div>
          </button>
          <button
            type="button"
            onClick={() => setAdapter('gptr')}
            className={`p-3 border rounded-md text-left transition-colors ${
              adapter === 'gptr'
                ? 'border-primary bg-primary/10'
                : 'border-border hover:bg-accent'
            }`}
          >
            <div className="font-medium text-sm text-foreground">GPT-Researcher</div>
            <div className="text-xs text-muted-foreground">
              Research-style generation
            </div>
          </button>
        </div>
      </div>

      {/* Model Selection */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">Model</label>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="w-full px-3 py-2 border rounded-md bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <optgroup label="OpenAI">
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o Mini</option>
            <option value="gpt-4-turbo">GPT-4 Turbo</option>
          </optgroup>
          <optgroup label="Anthropic">
            <option value="claude-3-opus">Claude 3 Opus</option>
            <option value="claude-3-sonnet">Claude 3.5 Sonnet</option>
            <option value="claude-3-haiku">Claude 3 Haiku</option>
          </optgroup>
          <optgroup label="Google">
            <option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
            <option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
          </optgroup>
        </select>
      </div>

      {/* Temperature */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-foreground">
            Temperature
          </label>
          <span className="text-sm text-muted-foreground">{temperature}</span>
        </div>
        <input
          type="range"
          min="0"
          max="2"
          step="0.1"
          value={temperature}
          onChange={(e) => setTemperature(parseFloat(e.target.value))}
          className="w-full"
        />
        <p className="text-xs text-muted-foreground">
          Lower = more deterministic, Higher = more creative
        </p>
      </div>

      {/* Max Tokens */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium text-foreground">
            Max Output Tokens
          </label>
          <span className="text-sm text-muted-foreground">{maxTokens}</span>
        </div>
        <input
          type="range"
          min="1024"
          max="16384"
          step="512"
          value={maxTokens}
          onChange={(e) => setMaxTokens(parseInt(e.target.value))}
          className="w-full"
        />
      </div>

      {/* GPT-Researcher Options */}
      {adapter === 'gptr' && (
        <div className="space-y-3 p-3 bg-muted rounded-md">
          <div className="flex items-center gap-2">
            <Info className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">
              GPT-Researcher Options
            </span>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={includeWebSearch}
              onChange={(e) => setIncludeWebSearch(e.target.checked)}
              className="rounded border-input"
            />
            <span className="text-sm text-foreground">Enable web search</span>
          </label>
        </div>
      )}
    </div>
  )
}
