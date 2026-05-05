import { getEffectiveInputMode } from './flowOverrides'
import type { FlowExecutionRecord, FlowHandoffArtifact, FlowValidationResult, PresetRunFlowNode, SavedFlowDefinition } from '../types/flow'

function createId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export interface StartChainPlan {
  executableNode: PresetRunFlowNode
  rootNodeIds: string[]
  nodeOrder: string[]
}

export interface CompletedNodeTransition {
  nextStatus: FlowExecutionRecord['status']
  nextCurrentNodeId: string | null
  nodeExecutions: FlowExecutionRecord['nodeExecutions']
  summaryMessage: string
}

export function buildOutgoingMap(edges: { source: string; target: string }[]): Map<string, string[]> {
  const map = new Map<string, string[]>()
  for (const edge of edges) {
    map.set(edge.source, [...(map.get(edge.source) ?? []), edge.target])
  }
  return map
}

export function getRootNodeIds(nodes: PresetRunFlowNode[], edges: { source: string; target: string }[]): string[] {
  const incoming = new Map<string, number>()
  for (const node of nodes) incoming.set(node.id, 0)
  for (const edge of edges) incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1)
  return nodes.filter((node) => (incoming.get(node.id) ?? 0) === 0).map((node) => node.id)
}

export function getStartChainPlan(
  flow: SavedFlowDefinition,
  nodes: PresetRunFlowNode[],
  edges: { source: string; target: string }[],
  validation: FlowValidationResult,
): { ok: true; plan: StartChainPlan } | { ok: false; reason: string } {
  if (!flow.name.trim()) {
    return { ok: false, reason: 'Give this saved flow a name before starting a chain.' }
  }

  if (!validation.executable) {
    return { ok: false, reason: 'Resolve the validation issues before starting a chain.' }
  }

  if (nodes.length === 0) {
    return {
      ok: false,
      reason: 'Add at least one Preset Run node before starting a chain.',
    }
  }

  const rootNodeIds = getRootNodeIds(nodes, edges)
  if (rootNodeIds.length !== 1) {
    return {
      ok: false,
      reason: 'This MVP supports one rooted chain at a time. Use exactly one root node before starting a chain.',
    }
  }

  const node = nodes.find((candidate) => candidate.id === rootNodeIds[0]) ?? nodes[0]
  if (!node.data.presetId) {
    return { ok: false, reason: `"${node.data.label || 'Untitled node'}" needs a preset selected before it can run.` }
  }

  if (getEffectiveInputMode(node.data, 0) === 'upstream_docs') {
    return {
      ok: false,
      reason: 'The first node in a chain cannot require upstream docs. Root nodes need to use their preset inputs.',
    }
  }

  return {
    ok: true,
    plan: {
      executableNode: node,
      rootNodeIds,
      nodeOrder: validation.executionOrder.length > 0 ? validation.executionOrder : [node.id],
    },
  }
}

export function createStartingFlowExecutionRecord(
  flow: SavedFlowDefinition,
  flowName: string,
  plan: StartChainPlan,
): FlowExecutionRecord {
  const startedAt = new Date().toISOString()
  const node = plan.executableNode
  const queuedNodeIds = plan.nodeOrder.filter((nodeId) => nodeId !== node.id)

  const nodeExecutions: FlowExecutionRecord['nodeExecutions'] = {
    [node.id]: {
      nodeId: node.id,
      presetId: node.data.presetId,
      presetName: node.data.presetName || 'Untitled Preset',
      status: 'starting',
      startedAt,
      message: 'Launching the underlying APICostX preset run…',
    },
  }

  for (const queuedNodeId of queuedNodeIds) {
    const queuedNode = flow.nodes.find((candidate) => candidate.id === queuedNodeId)
    if (!queuedNode) continue
    nodeExecutions[queuedNodeId] = {
      nodeId: queuedNode.id,
      presetId: queuedNode.data.presetId,
      presetName: queuedNode.data.presetName || 'Untitled Preset',
      status: 'queued',
      message: 'Waiting for upstream node completion and handoff preparation.',
      waitingForNodeId: node.id,
    }
  }

  return {
    id: createId('flow-exec'),
    flowId: flow.id,
    flowName: flowName.trim() || flow.name || 'Untitled Flow',
    status: 'starting',
    startedAt,
    currentNodeId: node.id,
    nodeOrder: plan.nodeOrder,
    nodeExecutions,
  }
}

export function applyCompletedNodeTransition(
  flow: SavedFlowDefinition,
  edges: { source: string; target: string }[],
  execution: FlowExecutionRecord,
  completedNodeId: string,
  artifact: FlowHandoffArtifact,
  upstreamRunStatus: string,
): CompletedNodeTransition {
  const outgoingMap = buildOutgoingMap(edges)
  const downstreamNodeIds = outgoingMap.get(completedNodeId) ?? []
  const nodeExecutions = { ...execution.nodeExecutions }
  const completedNode = nodeExecutions[completedNodeId]

  if (completedNode) {
    nodeExecutions[completedNodeId] = {
      ...completedNode,
      status: 'completed',
      message: artifact.summary,
      handoffArtifact: artifact,
    }
  }

  if (downstreamNodeIds.length === 0) {
    return {
      nextStatus: 'completed',
      nextCurrentNodeId: null,
      nodeExecutions,
      summaryMessage: 'Chain reached a terminal node. No downstream handoff is needed.',
    }
  }

  let firstReadyNodeId: string | null = null
  let anyBlocked = false

  for (const downstreamNodeId of downstreamNodeIds) {
    const downstreamNode = flow.nodes.find((candidate) => candidate.id === downstreamNodeId)
    if (!downstreamNode) continue

    const incomingCount = edges.filter((edge) => edge.target === downstreamNodeId).length
    const inputMode = getEffectiveInputMode(downstreamNode.data, incomingCount)

    if (upstreamRunStatus === 'failed' || upstreamRunStatus === 'cancelled') {
      anyBlocked = true
      nodeExecutions[downstreamNodeId] = {
        ...(nodeExecutions[downstreamNodeId] ?? {
          nodeId: downstreamNodeId,
          presetId: downstreamNode.data.presetId,
          presetName: downstreamNode.data.presetName || 'Untitled Preset',
        }),
        status: 'blocked',
        waitingForNodeId: completedNodeId,
        message: `Blocked because upstream node ended with status "${upstreamRunStatus}".`,
      }
      continue
    }

    if (artifact.status !== 'ready') {
      anyBlocked = true
      nodeExecutions[downstreamNodeId] = {
        ...(nodeExecutions[downstreamNodeId] ?? {
          nodeId: downstreamNodeId,
          presetId: downstreamNode.data.presetId,
          presetName: downstreamNode.data.presetName || 'Untitled Preset',
        }),
        status: 'blocked',
        waitingForNodeId: completedNodeId,
        message: artifact.summary,
      }
      continue
    }

    if (inputMode !== 'upstream_docs') {
      anyBlocked = true
      nodeExecutions[downstreamNodeId] = {
        ...(nodeExecutions[downstreamNodeId] ?? {
          nodeId: downstreamNodeId,
          presetId: downstreamNode.data.presetId,
          presetName: downstreamNode.data.presetName || 'Untitled Preset',
        }),
        status: 'blocked',
        waitingForNodeId: completedNodeId,
        handoffArtifact: artifact,
        message: 'This connected node is configured to keep using preset inputs, so flow handoff is bypassed.',
      }
      continue
    }

    if (!firstReadyNodeId) firstReadyNodeId = downstreamNodeId
    nodeExecutions[downstreamNodeId] = {
      ...(nodeExecutions[downstreamNodeId] ?? {
        nodeId: downstreamNodeId,
        presetId: downstreamNode.data.presetId,
        presetName: downstreamNode.data.presetName || 'Untitled Preset',
      }),
      status: 'handoff_ready',
      waitingForNodeId: completedNodeId,
      handoffArtifact: artifact,
      message: `${artifact.docCount} normalized output doc${artifact.docCount === 1 ? '' : 's'} are ready for this node. Runtime flow handoff is the next wiring step.`,
    }
  }

  if (firstReadyNodeId) {
    return {
      nextStatus: 'handoff_ready',
      nextCurrentNodeId: firstReadyNodeId,
      nodeExecutions,
      summaryMessage: `Upstream handoff is ready for ${downstreamNodeIds.length} downstream node${downstreamNodeIds.length === 1 ? '' : 's'}.`,
    }
  }

  return {
    nextStatus: anyBlocked ? 'blocked' : 'completed',
    nextCurrentNodeId: downstreamNodeIds[0] ?? null,
    nodeExecutions,
    summaryMessage: anyBlocked
      ? 'Downstream nodes are blocked. Review handoff readiness and override settings.'
      : 'Chain completed.',
  }
}
