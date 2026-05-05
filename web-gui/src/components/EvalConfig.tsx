import { useState } from 'react'
import { Info, Plus, X } from 'lucide-react'

export default function EvalConfig() {
  const [model, setModel] = useState('gpt-4o')
  const [rubricType, setRubricType] = useState('scale_1_5')
  const [evaluationDimensions, setEvaluationDimensions] = useState([
    'accuracy',
    'completeness',
    'relevance',
  ])
  const [customDimension, setCustomDimension] = useState('')
  const [passThreshold, setPassThreshold] = useState(3.5)

  const addDimension = () => {
    if (customDimension && !evaluationDimensions.includes(customDimension)) {
      setEvaluationDimensions([...evaluationDimensions, customDimension])
      setCustomDimension('')
    }
  }

  const removeDimension = (dim: string) => {
    setEvaluationDimensions(evaluationDimensions.filter((d) => d !== dim))
  }

  return (
    <div className="space-y-4 pt-4">
      {/* Model Selection */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">
          Evaluator Model
        </label>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="w-full px-3 py-2 border rounded-md bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <optgroup label="OpenAI">
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o Mini</option>
          </optgroup>
          <optgroup label="Anthropic">
            <option value="claude-3-sonnet">Claude 3.5 Sonnet</option>
            <option value="claude-3-haiku">Claude 3 Haiku</option>
          </optgroup>
        </select>
        <p className="text-xs text-muted-foreground">
          The model used to evaluate generated reports
        </p>
      </div>

      {/* Rubric Type */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">
          Scoring Rubric
        </label>
        <div className="grid grid-cols-3 gap-2">
          {[
            { value: 'scale_1_5', label: '1-5 Scale', desc: 'Default scoring' },
            { value: 'binary', label: 'Pass/Fail', desc: 'Binary scoring' },
            { value: 'percentage', label: '0-100%', desc: 'Percentage' },
          ].map((rubric) => (
            <button
              key={rubric.value}
              type="button"
              onClick={() => setRubricType(rubric.value)}
              className={`p-3 border rounded-md text-left transition-colors ${
                rubricType === rubric.value
                  ? 'border-primary bg-primary/10'
                  : 'border-border hover:bg-accent'
              }`}
            >
              <div className="font-medium text-sm text-foreground">
                {rubric.label}
              </div>
              <div className="text-xs text-muted-foreground">{rubric.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Pass Threshold */}
      {rubricType === 'scale_1_5' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium text-foreground">
              Pass Threshold
            </label>
            <span className="text-sm text-muted-foreground">
              ≥ {passThreshold}
            </span>
          </div>
          <input
            type="range"
            min="1"
            max="5"
            step="0.5"
            value={passThreshold}
            onChange={(e) => setPassThreshold(parseFloat(e.target.value))}
            className="w-full"
          />
          <p className="text-xs text-muted-foreground">
            Scores at or above this value are considered passing
          </p>
        </div>
      )}

      {/* Evaluation Dimensions */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-foreground">
          Evaluation Dimensions
        </label>
        <div className="flex flex-wrap gap-2">
          {evaluationDimensions.map((dim) => (
            <span
              key={dim}
              className="inline-flex items-center gap-1 px-2 py-1 bg-muted rounded-md text-sm text-foreground"
            >
              {dim}
              <button
                type="button"
                onClick={() => removeDimension(dim)}
                className="p-0.5 hover:bg-accent rounded"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={customDimension}
            onChange={(e) => setCustomDimension(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addDimension())}
            placeholder="Add custom dimension..."
            className="flex-1 px-3 py-2 border rounded-md bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <button
            type="button"
            onClick={addDimension}
            className="px-3 py-2 border rounded-md hover:bg-accent transition-colors"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Info Box */}
      <div className="flex items-start gap-2 p-3 bg-muted rounded-md">
        <Info className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
        <p className="text-xs text-muted-foreground">
          Each dimension will be scored individually. The final score is the
          average across all dimensions.
        </p>
      </div>
    </div>
  )
}
