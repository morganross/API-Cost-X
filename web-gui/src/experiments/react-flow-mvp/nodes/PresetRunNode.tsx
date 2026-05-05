import { Handle, Position, type NodeProps } from '@xyflow/react'
import { ArrowUpRight, Boxes, CheckCircle2, Clock3, PlayCircle, PlugZap, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  getEffectiveInputMode,
  getEffectiveOutputMode,
  getInputModeLabel,
  hasExplicitInputOverride,
  hasExplicitOutputOverride,
} from '../lib/flowOverrides'
import type { PresetRunFlowNode } from '../types/flow'

function getExecutionStatusClasses(status: PresetRunFlowNode['data']['executionStatus']) {
  switch (status) {
    case 'queued':
      return 'border-amber-500/30 bg-amber-500/10 text-amber-200'
    case 'starting':
      return 'border-blue-500/30 bg-blue-500/10 text-blue-200'
    case 'running':
      return 'border-sky-500/30 bg-sky-500/10 text-sky-200'
    case 'handoff_ready':
      return 'border-violet-500/30 bg-violet-500/10 text-violet-200'
    case 'completed':
      return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
    case 'blocked':
    case 'failed':
      return 'border-red-500/30 bg-red-500/10 text-red-200'
    default:
      return 'border-gray-600 bg-gray-800 text-gray-200'
  }
}

function getExecutionStatusIcon(status: PresetRunFlowNode['data']['executionStatus']) {
  switch (status) {
    case 'queued':
      return <Clock3 className="h-3.5 w-3.5" />
    case 'completed':
    case 'handoff_ready':
      return <ArrowUpRight className="h-3.5 w-3.5" />
    case 'blocked':
    case 'failed':
      return <XCircle className="h-3.5 w-3.5" />
    default:
      return <PlayCircle className="h-3.5 w-3.5" />
  }
}

function getExecutionStatusLabel(status: PresetRunFlowNode['data']['executionStatus']) {
  switch (status) {
    case 'queued':
      return 'Queued'
    case 'starting':
      return 'Launching…'
    case 'running':
      return 'Run active'
    case 'handoff_ready':
      return 'Handoff ready'
    case 'completed':
      return 'Completed'
    case 'blocked':
      return 'Blocked'
    case 'failed':
      return 'Failed'
    default:
      return null
  }
}

export default function PresetRunNode({ data, selected }: NodeProps<PresetRunFlowNode>) {
  const incomingCount = data.incomingCount ?? 0
  const hasPreset = Boolean(data.presetId)
  const executionStatus = data.executionStatus
  const effectiveInputMode = getEffectiveInputMode(data, incomingCount)
  const effectiveOutputMode = getEffectiveOutputMode(data)

  return (
    <div
      className={cn(
        'relative w-[21rem] rounded-2xl border border-gray-700 bg-gray-900/95 px-4 py-3 shadow-[0_20px_60px_rgba(15,23,42,0.45)] transition',
        selected && 'ring-2 ring-blue-400/70',
      )}
      data-testid="flow-lab-preset-run-node"
      data-preset-selected={hasPreset}
      data-root-node={incomingCount === 0}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-3 !w-3 !border-2 !border-gray-900 !bg-slate-300"
      />

      <div className="flex items-start gap-3">
        <div className="rounded-xl border border-blue-500/40 bg-blue-500/10 px-2 py-2 text-blue-200">
          <Boxes className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500">Preset Run</p>
          <h3 className="truncate text-base font-semibold text-gray-100">{data.label || 'Untitled Preset Run'}</h3>
          <p className="mt-1 truncate text-sm leading-5 text-gray-300">
            {hasPreset ? data.presetName : 'Choose a preset in the inspector'}
          </p>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2 text-xs text-gray-300">
        <PlugZap className="h-3.5 w-3.5 text-blue-300" />
        <span>{getInputModeLabel(effectiveInputMode)}</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-xl border border-gray-700 bg-gray-800/80 px-3 py-2">
          <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Preset Docs</div>
          <div className="mt-1 font-medium text-gray-100">{data.documentCount ?? 0}</div>
        </div>
        <div className="rounded-xl border border-gray-700 bg-gray-800/80 px-3 py-2">
          <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Past Runs</div>
          <div className="mt-1 font-medium text-gray-100">{data.runCount ?? 0}</div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {hasPreset ? (
          <>
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-emerald-200">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Preset attached
            </span>
            {typeof data.executionIndex === 'number' && data.executionIndex >= 0 ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-600 bg-gray-800 px-2 py-1 text-gray-200">
                <PlayCircle className="h-3.5 w-3.5" />
                Step {data.executionIndex + 1}
              </span>
            ) : null}
            {executionStatus && getExecutionStatusLabel(executionStatus) ? (
              <span
                className={cn(
                  'inline-flex items-center gap-1 rounded-full border px-2 py-1',
                  getExecutionStatusClasses(executionStatus),
                )}
              >
                {getExecutionStatusIcon(executionStatus)}
                {getExecutionStatusLabel(executionStatus)}
              </span>
            ) : null}
            {hasExplicitInputOverride(data) ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-1 text-blue-200">
                Input override
              </span>
            ) : null}
            {hasExplicitOutputOverride(data) || effectiveOutputMode === 'no_chain_output' ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-fuchsia-500/30 bg-fuchsia-500/10 px-2 py-1 text-fuchsia-200">
                {effectiveOutputMode === 'no_chain_output' ? 'No chain output' : 'Output override'}
              </span>
            ) : null}
          </>
        ) : (
          <span className="inline-flex items-center rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-amber-200">
            Preset required
          </span>
        )}
      </div>

      {data.generators && data.generators.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-1">
          {data.generators.slice(0, 3).map((generator) => (
            <span
              key={generator}
              className="rounded-md border border-gray-700 bg-gray-800/90 px-2 py-1 text-[11px] uppercase tracking-[0.12em] text-gray-300"
            >
              {generator}
            </span>
          ))}
        </div>
      ) : null}

      {data.activeRunId ? (
        <div className="mt-3 text-[11px] uppercase tracking-[0.12em] text-gray-500">
          Live run
          <div className="mt-1 break-all text-xs tracking-normal text-gray-300">{data.activeRunId}</div>
        </div>
      ) : null}

      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !border-2 !border-gray-900 !bg-blue-300"
      />
    </div>
  )
}
