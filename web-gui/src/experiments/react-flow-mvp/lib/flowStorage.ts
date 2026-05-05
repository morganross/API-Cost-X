import type { Edge, Viewport } from '@xyflow/react'
import type { PresetRunFlowNode, SavedFlowDefinition } from '../types/flow'

const STORAGE_KEY = 'apicostx-saved-flows-mvp'
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }

function createId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function createBlankSavedFlow(overrides?: Partial<SavedFlowDefinition>): SavedFlowDefinition {
  const now = new Date().toISOString()

  return {
    id: overrides?.id ?? createId('flow'),
    name: overrides?.name ?? 'Untitled Flow',
    description: overrides?.description ?? '',
    version: overrides?.version ?? 1,
    nodes: overrides?.nodes ?? [],
    edges: overrides?.edges ?? [],
    viewport: overrides?.viewport ?? DEFAULT_VIEWPORT,
    createdAt: overrides?.createdAt ?? now,
    updatedAt: overrides?.updatedAt ?? now,
  }
}

export function loadSavedFlows(): SavedFlowDefinition[] {
  if (typeof window === 'undefined') return []

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(Boolean)
  } catch {
    return []
  }
}

function writeSavedFlows(flows: SavedFlowDefinition[]) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(flows))
}

export function upsertSavedFlow(flow: SavedFlowDefinition): SavedFlowDefinition[] {
  const flows = loadSavedFlows()
  const next = [...flows]
  const index = next.findIndex((candidate) => candidate.id === flow.id)

  if (index >= 0) {
    next[index] = flow
  } else {
    next.unshift(flow)
  }

  writeSavedFlows(next)
  return next
}

export function deleteSavedFlow(flowId: string): SavedFlowDefinition[] {
  const next = loadSavedFlows().filter((flow) => flow.id !== flowId)
  writeSavedFlows(next)
  return next
}

export function cloneFlowForSave(
  flow: SavedFlowDefinition,
  nodes: PresetRunFlowNode[],
  edges: Edge[],
  viewport?: Viewport,
): SavedFlowDefinition {
  return {
    ...flow,
    nodes,
    edges,
    viewport: viewport ?? flow.viewport ?? DEFAULT_VIEWPORT,
    updatedAt: new Date().toISOString(),
  }
}
