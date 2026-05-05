import type { Run, SourceDocResult } from '@/api/runs'
import type { FlowHandoffArtifact, FlowHandoffDocumentRef, PresetRunFlowNode } from '../types/flow'
import { getEffectiveOutputMode } from './flowOverrides'

function buildDocLabel(
  sourceDoc: SourceDocResult,
  kind: FlowHandoffDocumentRef['kind'],
  documentId: string,
  extras?: Partial<FlowHandoffDocumentRef>,
): string {
  const parts = [sourceDoc.source_doc_name]
  if (kind === 'combined') parts.push('combined')
  if (kind === 'winner') parts.push('winner')
  if (extras?.model) parts.push(extras.model)
  if (extras?.generator) parts.push(extras.generator)
  if (extras?.iteration !== undefined) parts.push(`iter ${extras.iteration}`)
  parts.push(documentId.slice(0, 8))
  return parts.join(' · ')
}

function buildCombinedRefs(sourceDoc: SourceDocResult): FlowHandoffDocumentRef[] {
  const combinedItems = sourceDoc.combined_docs?.length
    ? sourceDoc.combined_docs
    : sourceDoc.combined_doc
      ? [sourceDoc.combined_doc]
      : []

  return combinedItems.map((item) => ({
    documentId: item.id,
    sourceDocId: sourceDoc.source_doc_id,
    sourceDocName: sourceDoc.source_doc_name,
    model: item.model,
    generator: item.generator,
    iteration: item.iteration,
    kind: 'combined',
    label: buildDocLabel(sourceDoc, 'combined', item.id, item),
  }))
}

function buildWinnerRef(sourceDoc: SourceDocResult): FlowHandoffDocumentRef[] {
  if (!sourceDoc.winner_doc_id) return []
  const winnerDoc = sourceDoc.generated_docs.find((doc) => doc.id === sourceDoc.winner_doc_id)
  return [
    {
      documentId: sourceDoc.winner_doc_id,
      sourceDocId: sourceDoc.source_doc_id,
      sourceDocName: sourceDoc.source_doc_name,
      model: winnerDoc?.model,
      generator: winnerDoc?.generator,
      iteration: winnerDoc?.iteration,
      kind: 'winner',
      label: buildDocLabel(sourceDoc, 'winner', sourceDoc.winner_doc_id, winnerDoc),
    },
  ]
}

function buildGeneratedRefs(sourceDoc: SourceDocResult): FlowHandoffDocumentRef[] {
  return sourceDoc.generated_docs.map((item) => ({
    documentId: item.id,
    sourceDocId: sourceDoc.source_doc_id,
    sourceDocName: sourceDoc.source_doc_name,
    model: item.model,
    generator: item.generator,
    iteration: item.iteration,
    kind: 'generated',
    label: buildDocLabel(sourceDoc, 'generated', item.id, item),
  }))
}

function normalizeSourceDocOutputs(sourceDoc: SourceDocResult): {
  docs: FlowHandoffDocumentRef[]
  derivedFrom: FlowHandoffArtifact['derivedFrom']
} {
  const combinedRefs = buildCombinedRefs(sourceDoc)
  if (combinedRefs.length > 0) {
    return { docs: combinedRefs, derivedFrom: 'combined_docs' }
  }

  const winnerRefs = buildWinnerRef(sourceDoc)
  if (winnerRefs.length > 0) {
    return { docs: winnerRefs, derivedFrom: 'winner_doc_id' }
  }

  const generatedRefs = buildGeneratedRefs(sourceDoc)
  if (generatedRefs.length > 0) {
    return { docs: generatedRefs, derivedFrom: 'generated_docs' }
  }

  return { docs: [], derivedFrom: 'none' }
}

export function buildHandoffArtifactFromRun(node: PresetRunFlowNode, run: Run): FlowHandoffArtifact {
  const sourceResults = Object.values(run.source_doc_results ?? {})
  const normalized = sourceResults.map(normalizeSourceDocOutputs)
  const docs = normalized.flatMap((item) => item.docs)
  const derivedSet = new Set(normalized.map((item) => item.derivedFrom).filter((value) => value !== 'none'))
  const derivedFrom: FlowHandoffArtifact['derivedFrom'] =
    derivedSet.size === 0
      ? 'none'
      : derivedSet.size === 1
        ? Array.from(derivedSet)[0] as FlowHandoffArtifact['derivedFrom']
        : 'mixed'

  if (getEffectiveOutputMode(node.data) === 'no_chain_output') {
    return {
      sourceNodeId: node.id,
      runId: run.id,
      runStatus: run.status,
      status: 'suppressed',
      derivedFrom,
      docCount: docs.length,
      docs,
      summary:
        docs.length > 0
          ? `${docs.length} normalized output doc${docs.length === 1 ? '' : 's'} are available, but this node is configured to keep outputs local.`
          : 'This node is configured to keep outputs local, and the run did not produce normalized chain docs.',
      completedAt: run.completed_at,
      createdAt: new Date().toISOString(),
    }
  }

  if (docs.length === 0) {
    return {
      sourceNodeId: node.id,
      runId: run.id,
      runStatus: run.status,
      status: 'unavailable',
      derivedFrom: 'none',
      docCount: 0,
      docs: [],
      summary: 'No normalized output documents were found in the completed run snapshot.',
      completedAt: run.completed_at,
      createdAt: new Date().toISOString(),
    }
  }

  return {
    sourceNodeId: node.id,
    runId: run.id,
    runStatus: run.status,
    status: 'ready',
    derivedFrom,
    docCount: docs.length,
    docs,
    summary: `${docs.length} normalized output doc${docs.length === 1 ? '' : 's'} ready for downstream handoff.`,
    completedAt: run.completed_at,
    createdAt: new Date().toISOString(),
  }
}
