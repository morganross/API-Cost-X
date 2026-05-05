import type {
  FlowInputOverrideMode,
  FlowOutputOverrideMode,
  PresetRunFlowNode,
  PresetRunNodeData,
} from '../types/flow'

export function getNodeInputOverrideMode(data: PresetRunNodeData): FlowInputOverrideMode {
  return data.inputOverrideMode ?? 'automatic'
}

export function getNodeOutputOverrideMode(data: PresetRunNodeData): FlowOutputOverrideMode {
  return data.outputOverrideMode ?? 'automatic'
}

export function getEffectiveInputMode(data: PresetRunNodeData, incomingCount: number): 'preset_inputs' | 'upstream_docs' {
  const override = getNodeInputOverrideMode(data)
  if (override === 'preset_inputs') return 'preset_inputs'
  if (override === 'upstream_docs') return 'upstream_docs'
  return incomingCount > 0 ? 'upstream_docs' : 'preset_inputs'
}

export function getEffectiveOutputMode(data: PresetRunNodeData): 'normalized_chain_output' | 'no_chain_output' {
  const override = getNodeOutputOverrideMode(data)
  if (override === 'no_chain_output') return 'no_chain_output'
  return 'normalized_chain_output'
}

export function getInputModeLabel(mode: 'preset_inputs' | 'upstream_docs'): string {
  return mode === 'upstream_docs' ? 'Uses upstream output docs' : 'Uses preset input docs'
}

export function getOutputModeLabel(mode: 'normalized_chain_output' | 'no_chain_output'): string {
  return mode === 'no_chain_output' ? 'Stops chain output handoff' : 'Produces normalized chain output'
}

export function hasExplicitInputOverride(data: PresetRunNodeData): boolean {
  return getNodeInputOverrideMode(data) !== 'automatic'
}

export function hasExplicitOutputOverride(data: PresetRunNodeData): boolean {
  return getNodeOutputOverrideMode(data) !== 'automatic'
}

export function countIncomingEdges(node: PresetRunFlowNode, edges: { target: string }[]): number {
  return edges.filter((edge) => edge.target === node.id).length
}
