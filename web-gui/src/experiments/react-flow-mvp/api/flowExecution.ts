import { contentsApi } from '@/api/contents'
import { deletePreset, duplicatePreset, executePreset, getPreset, updatePreset } from '@/api/presets'
import { runsApi } from '@/api/runs'
import type { FlowHandoffArtifact, PresetRunFlowNode, PresetRunLaunchResult } from '../types/flow'

function createFlowTempSuffix() {
  const stamp = new Date().toISOString().replace(/[.:TZ-]/g, '').slice(0, 14)
  const random = Math.random().toString(36).slice(2, 8)
  return `${stamp}-${random}`
}

async function cleanupTemporaryPreset(presetId: string | null | undefined) {
  if (!presetId) return
  try {
    await deletePreset(presetId, true)
  } catch {
    // Best-effort cleanup only; failure should not break a launched flow node.
  }
}

async function cleanupTemporaryInputs(documentIds: string[]) {
  await Promise.allSettled(documentIds.map(async (id) => {
    try {
      await contentsApi.delete(id)
    } catch {
      // Best-effort cleanup only.
    }
  }))
}

export async function cleanupTemporaryFlowInputs(documentIds: string[]) {
  await cleanupTemporaryInputs(documentIds)
}

export async function launchPresetRunNode(node: PresetRunFlowNode): Promise<PresetRunLaunchResult> {
  const presetId = node.data.presetId
  if (!presetId) {
    throw new Error(`"${node.data.label || 'Untitled node'}" does not have a preset selected.`)
  }

  const response = await executePreset(presetId)

  return {
    nodeId: node.id,
    presetId,
    presetName: node.data.presetName || response.preset_name || 'Untitled Preset',
    runId: response.run_id,
    launchStatus: response.status || 'started',
    startedAt: new Date().toISOString(),
  }
}

function buildFlowInputName(node: PresetRunFlowNode, index: number, label: string) {
  const safeLabel = label.replace(/\s+/g, ' ').trim()
  const base = `${node.data.label || node.data.presetName || 'Preset Run'} · ${safeLabel || `Doc ${index + 1}`}`
  return base.length <= 120 ? base : `${base.slice(0, 117)}...`
}

function buildTemporaryPresetName(node: PresetRunFlowNode, flowName: string) {
  const nodeLabel = node.data.label || node.data.presetName || 'Preset Run'
  const base = `[Flow Temp] ${flowName} · ${nodeLabel} · ${createFlowTempSuffix()}`
  return base.length <= 120 ? base : `${base.slice(0, 117)}...`
}

export async function launchPresetRunNodeFromHandoff(
  node: PresetRunFlowNode,
  artifact: FlowHandoffArtifact,
  flowName: string,
): Promise<PresetRunLaunchResult & { materializedDocumentIds: string[]; temporaryPresetId: string }> {
  const presetId = node.data.presetId
  if (!presetId) {
    throw new Error(`"${node.data.label || 'Untitled node'}" does not have a preset selected.`)
  }

  if (artifact.status !== 'ready' || artifact.docs.length === 0) {
    throw new Error('This node does not have ready upstream handoff docs to consume yet.')
  }

  const preset = await getPreset(presetId)
  let temporaryPresetId: string | undefined

  const materializedDocs = await Promise.all(
    artifact.docs.map(async (docRef, index) => {
      const generated = await runsApi.getGeneratedDocumentContent(artifact.runId, docRef.documentId)
      return contentsApi.create({
        name: buildFlowInputName(node, index, docRef.label),
        content_type: 'input_document',
        body: generated.content,
        description: `Temporary Flow Lab input created from run ${artifact.runId} (${docRef.kind}, ${artifact.derivedFrom}).`,
        tags: ['flow-lab', 'temporary', 'upstream-handoff'],
      })
    }),
  )

  const materializedDocumentIds = materializedDocs.map((doc) => doc.id)
  if (materializedDocumentIds.length === 0) {
    throw new Error('Upstream handoff docs could not be materialized into temporary flow inputs.')
  }

  try {
    const duplicatedPreset = await duplicatePreset(
      presetId,
      buildTemporaryPresetName(node, flowName),
    )
    temporaryPresetId = duplicatedPreset.id

    await updatePreset(temporaryPresetId, {
      documents: materializedDocumentIds,
      input_source_type: 'database',
      github_connection_id: '',
      github_input_paths: [],
      github_output_path: '',
      output_destination: 'none',
    })

    const launched = await executePreset(temporaryPresetId)

    return {
      nodeId: node.id,
      presetId,
      presetName: node.data.presetName || preset.name || 'Untitled Preset',
      runId: launched.run_id,
      launchStatus: launched.status || 'started',
      startedAt: new Date().toISOString(),
      materializedDocumentIds,
      temporaryPresetId,
    }
  } catch (error) {
    await cleanupTemporaryPreset(temporaryPresetId)
    await cleanupTemporaryInputs(materializedDocumentIds)
    throw error
  }
}
