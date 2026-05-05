import type { Edge } from '@xyflow/react'
import { getEffectiveInputMode, getEffectiveOutputMode } from './flowOverrides'
import type { FlowValidationIssue, FlowValidationResult, PresetRunFlowNode } from '../types/flow'

function buildAdjacency(nodes: PresetRunFlowNode[], edges: Edge[]) {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const adjacency = new Map<string, string[]>()
  const inDegree = new Map<string, number>()

  for (const node of nodes) {
    adjacency.set(node.id, [])
    inDegree.set(node.id, 0)
  }

  const issues: FlowValidationIssue[] = []

  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      issues.push({
        level: 'error',
        message: 'One or more connections point to a missing node.',
      })
      continue
    }

    adjacency.get(edge.source)?.push(edge.target)
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1)
  }

  return { adjacency, inDegree, issues }
}

export function validateFlow(nodes: PresetRunFlowNode[], edges: Edge[]): FlowValidationResult {
  const issues: FlowValidationIssue[] = []

  if (nodes.length === 0) {
    issues.push({
      level: 'warning',
      message: 'Add at least one Preset Run node to begin building a saved flow.',
    })
  }

  const { adjacency, inDegree, issues: adjacencyIssues } = buildAdjacency(nodes, edges)
  issues.push(...adjacencyIssues)

  for (const node of nodes) {
    if (!node.data.presetId) {
      issues.push({
        level: 'error',
        message: `"${node.data.label || 'Untitled node'}" does not have a preset selected.`,
      })
    }

    const incoming = inDegree.get(node.id) ?? 0
    const outgoing = adjacency.get(node.id)?.length ?? 0
    if (incoming > 1) {
      issues.push({
        level: 'error',
        message: `"${node.data.label || 'Untitled node'}" has more than one incoming connection. Flow merges are out of scope for this MVP.`,
      })
    }

    if (getEffectiveInputMode(node.data, incoming) === 'upstream_docs' && incoming === 0) {
      issues.push({
        level: 'error',
        message: `"${node.data.label || 'Untitled node'}" is configured to use upstream docs but does not have an incoming connection yet.`,
      })
    }

    if (getEffectiveOutputMode(node.data) === 'no_chain_output' && outgoing > 0) {
      issues.push({
        level: 'warning',
        message: `"${node.data.label || 'Untitled node'}" is configured to keep outputs local, so downstream nodes will not receive handoff docs.`,
      })
    }
  }

  const queue: string[] = []
  const mutableInDegree = new Map(inDegree)

  for (const [nodeId, degree] of mutableInDegree.entries()) {
    if (degree === 0) queue.push(nodeId)
  }

  const executionOrder: string[] = []

  while (queue.length > 0) {
    const nodeId = queue.shift()!
    executionOrder.push(nodeId)

    for (const target of adjacency.get(nodeId) ?? []) {
      const nextDegree = (mutableInDegree.get(target) ?? 0) - 1
      mutableInDegree.set(target, nextDegree)
      if (nextDegree === 0) {
        queue.push(target)
      }
    }
  }

  if (edges.length > 0 && executionOrder.length !== nodes.length) {
    issues.push({
      level: 'error',
      message: 'This flow contains a cycle. Saved flows must be acyclic for the MVP.',
    })
  }

  if (nodes.length > 0) {
    const rootNodes = nodes.filter((node) => (inDegree.get(node.id) ?? 0) === 0)
    if (rootNodes.length === 0) {
      issues.push({
        level: 'error',
        message: 'At least one root node is required. Root nodes use the preset input docs already saved inside the preset.',
      })
    } else {
      issues.push({
        level: 'info',
        message: `${rootNodes.length} root node${rootNodes.length === 1 ? '' : 's'} will use the input docs already stored inside their presets.`,
      })
    }
  }

  if (!issues.some((issue) => issue.level === 'error') && nodes.length > 0) {
    issues.push({
      level: 'info',
      message: 'Connected nodes will ignore their preset-saved input docs for that execution and consume upstream output docs instead.',
    })
  }

  return {
    executable: issues.every((issue) => issue.level !== 'error') && nodes.length > 0,
    issues,
    executionOrder,
  }
}
