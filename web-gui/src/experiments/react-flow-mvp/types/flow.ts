import type { Edge, Node, Viewport } from '@xyflow/react'

export type FlowNodeType = 'presetRun'
export type FlowInputOverrideMode = 'automatic' | 'preset_inputs' | 'upstream_docs'
export type FlowOutputOverrideMode = 'automatic' | 'normalized_chain_output' | 'no_chain_output'
export type FlowExecutionStatus = 'idle' | 'starting' | 'running' | 'handoff_ready' | 'completed' | 'blocked' | 'failed'
export type FlowNodeExecutionStatus = 'idle' | 'queued' | 'starting' | 'running' | 'handoff_ready' | 'completed' | 'blocked' | 'failed'

export interface PresetRunNodeData extends Record<string, unknown> {
  label: string
  presetId: string
  presetName: string
  presetDescription?: string
  documentCount?: number
  runCount?: number
  generators?: string[]
  incomingCount?: number
  outgoingCount?: number
  executionIndex?: number | null
  executionStatus?: FlowNodeExecutionStatus
  activeRunId?: string
  activeRunStatus?: string
  executionMessage?: string
  inputOverrideMode?: FlowInputOverrideMode
  outputOverrideMode?: FlowOutputOverrideMode
}

export type PresetRunFlowNode = Node<PresetRunNodeData, 'presetRun'>

export interface SavedFlowDefinition {
  id: string
  name: string
  description?: string
  version: number
  nodes: PresetRunFlowNode[]
  edges: Edge[]
  viewport?: Viewport
  createdAt: string
  updatedAt: string
}

export interface FlowValidationIssue {
  level: 'error' | 'warning' | 'info'
  message: string
}

export interface FlowValidationResult {
  executable: boolean
  issues: FlowValidationIssue[]
  executionOrder: string[]
}

export interface PresetRunLaunchResult {
  nodeId: string
  presetId: string
  presetName: string
  runId: string
  launchStatus: string
  startedAt: string
}

export interface FlowHandoffDocumentRef {
  documentId: string
  sourceDocId?: string
  sourceDocName?: string
  model?: string
  generator?: string
  iteration?: number
  kind: 'combined' | 'winner' | 'generated'
  label: string
}

export interface FlowHandoffArtifact {
  sourceNodeId: string
  runId: string
  runStatus: string
  status: 'ready' | 'suppressed' | 'unavailable'
  derivedFrom: 'combined_docs' | 'winner_doc_id' | 'generated_docs' | 'mixed' | 'none'
  docCount: number
  docs: FlowHandoffDocumentRef[]
  summary: string
  completedAt?: string
  createdAt: string
}

export interface PresetRunNodeExecution {
  nodeId: string
  presetId: string
  presetName: string
  status: FlowNodeExecutionStatus
  runId?: string
  launchStatus?: string
  lastKnownRunStatus?: string
  startedAt?: string
  completedAt?: string
  message?: string
  handoffArtifact?: FlowHandoffArtifact
  waitingForNodeId?: string
  materializedDocumentIds?: string[]
  temporaryPresetId?: string
  temporaryInputCleanupStatus?: 'pending' | 'completed' | 'failed'
  temporaryInputCleanupError?: string
}

export interface FlowExecutionRecord {
  id: string
  flowId: string
  flowName: string
  status: FlowExecutionStatus
  startedAt: string
  currentNodeId: string | null
  nodeOrder: string[]
  nodeExecutions: Record<string, PresetRunNodeExecution>
}
