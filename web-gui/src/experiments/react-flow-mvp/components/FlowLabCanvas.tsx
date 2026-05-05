import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
  type Viewport,
} from '@xyflow/react'
import {
  ArrowUpRight,
  Boxes,
  GitBranchPlus,
  LayoutList,
  Link2,
  Plus,
  Play,
  Save,
  Trash2,
  Workflow,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { listPresets, type PresetSummary } from '@/api/presets'
import { runsApi } from '@/api/runs'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { notify } from '@/stores/notifications'
import { cn } from '@/lib/utils'
import { cleanupTemporaryFlowInputs, launchPresetRunNode, launchPresetRunNodeFromHandoff } from '../api/flowExecution'
import { applyCompletedNodeTransition, createStartingFlowExecutionRecord, getStartChainPlan } from '../lib/flowExecution'
import { buildHandoffArtifactFromRun } from '../lib/flowHandoff'
import {
  getEffectiveInputMode,
  getEffectiveOutputMode,
  getInputModeLabel,
  getOutputModeLabel,
  getNodeInputOverrideMode,
  getNodeOutputOverrideMode,
} from '../lib/flowOverrides'
import { cloneFlowForSave, createBlankSavedFlow, deleteSavedFlow, loadSavedFlows, upsertSavedFlow } from '../lib/flowStorage'
import { validateFlow } from '../lib/flowValidation'
import PresetRunNode from '../nodes/PresetRunNode'
import type { FlowExecutionRecord, PresetRunFlowNode, SavedFlowDefinition } from '../types/flow'

const nodeTypes: NodeTypes = {
  presetRun: PresetRunNode,
}

const DND_NODE_TYPE = 'application/apicostx-flow-node'
const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 1 }
const INPUT_OVERRIDE_OPTIONS = [
  { value: 'automatic', label: 'Automatic flow behavior' },
  { value: 'preset_inputs', label: 'Force preset-saved inputs' },
  { value: 'upstream_docs', label: 'Force upstream output docs' },
]
const OUTPUT_OVERRIDE_OPTIONS = [
  { value: 'automatic', label: 'Automatic normalized chain output' },
  { value: 'no_chain_output', label: 'Keep outputs local to this node' },
]
const TERMINAL_RUN_STATUSES = new Set(['completed', 'completed_with_errors', 'failed', 'cancelled'])

function getExecutionTone(status?: FlowExecutionRecord['status'] | string) {
  switch (status) {
    case 'running':
      return 'text-sky-300'
    case 'handoff_ready':
      return 'text-violet-300'
    case 'completed':
      return 'text-emerald-300'
    case 'blocked':
    case 'failed':
      return 'text-red-300'
    case 'starting':
      return 'text-blue-300'
    default:
      return 'text-gray-300'
  }
}

function formatExecutionStatusLabel(status?: string) {
  switch (status) {
    case 'starting':
      return 'Starting'
    case 'running':
      return 'Running'
    case 'handoff_ready':
      return 'Handoff ready'
    case 'completed':
      return 'Completed'
    case 'blocked':
      return 'Blocked'
    case 'failed':
      return 'Failed'
    case 'queued':
      return 'Queued'
    default:
      return status ?? 'Unknown'
  }
}

function createNodeLabel(index: number) {
  return `Preset Run ${index}`
}

function createPresetRunNode(
  position: { x: number; y: number },
  index: number,
  preset?: PresetSummary,
): PresetRunFlowNode {
  return {
    id: `preset-run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: 'presetRun',
    position,
    data: {
      label: createNodeLabel(index),
      presetId: preset?.id ?? '',
      presetName: preset?.name ?? '',
      presetDescription: preset?.description ?? '',
      documentCount: preset?.document_count ?? 0,
      runCount: preset?.run_count ?? 0,
      generators: [],
      inputOverrideMode: 'automatic',
      outputOverrideMode: 'automatic',
    },
  }
}

function applyPresetToNode(node: PresetRunFlowNode, preset?: PresetSummary): PresetRunFlowNode {
  if (!preset) return node

  return {
    ...node,
    data: {
      ...node.data,
      presetId: preset.id,
      presetName: preset.name,
      presetDescription: preset.description ?? '',
      documentCount: preset.document_count ?? 0,
      runCount: preset.run_count ?? 0,
      generators: node.data.generators ?? [],
    },
  }
}

function createStarterSavedFlow(presets: PresetSummary[] = []): SavedFlowDefinition {
  const firstPreset = presets[0]
  const secondPreset = presets[1] ?? presets[0]
  const firstNode = createPresetRunNode({ x: 120, y: 180 }, 1, firstPreset)
  const secondNode = createPresetRunNode({ x: 520, y: 180 }, 2, secondPreset)

  return createBlankSavedFlow({
    name: 'Starter Flow',
    description: 'Two preset runs chained together to show how saved flows hand off output docs.',
    nodes: [firstNode, secondNode],
    edges: [
      {
        id: `starter-edge-${firstNode.id}-${secondNode.id}`,
        source: firstNode.id,
        target: secondNode.id,
        animated: true,
        style: { stroke: '#93c5fd', strokeWidth: 2 },
      },
    ],
    viewport: { x: 0, y: 0, zoom: 0.95 },
  })
}

function FlowLabInner() {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const reactFlow = useReactFlow()
  const navigate = useNavigate()
  const initialFlowRef = useRef<SavedFlowDefinition>(createStarterSavedFlow())

  const [savedFlows, setSavedFlows] = useState<SavedFlowDefinition[]>([])
  const [currentFlow, setCurrentFlow] = useState<SavedFlowDefinition>(initialFlowRef.current)
  const [nodes, setNodes, onNodesChange] = useNodesState<PresetRunFlowNode>(initialFlowRef.current.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialFlowRef.current.edges)
  const [flowName, setFlowName] = useState(initialFlowRef.current.name)
  const [flowDescription, setFlowDescription] = useState(initialFlowRef.current.description ?? '')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [viewport, setViewport] = useState<Viewport>(initialFlowRef.current.viewport ?? DEFAULT_VIEWPORT)
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [presetsLoading, setPresetsLoading] = useState(true)
  const [presetsError, setPresetsError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState('Starter flow ready')
  const [flowExecution, setFlowExecution] = useState<FlowExecutionRecord | null>(null)
  const [isStartingChain, setIsStartingChain] = useState(false)
  const downstreamLaunchKeyRef = useRef<string | null>(null)

  useEffect(() => {
    const existingFlows = loadSavedFlows()
    setSavedFlows(existingFlows)

    if (existingFlows.length > 0) {
      const firstFlow = existingFlows[0]
      setCurrentFlow(firstFlow)
      setFlowName(firstFlow.name)
      setFlowDescription(firstFlow.description ?? '')
      setNodes(firstFlow.nodes)
      setEdges(firstFlow.edges)
      setViewport(firstFlow.viewport ?? DEFAULT_VIEWPORT)
      setSaveMessage(`Loaded ${firstFlow.name}`)
      return
    }

    setSaveMessage('Starter flow ready')
  }, [setEdges, setNodes])

  useEffect(() => {
    let cancelled = false

    async function loadPresetOptions() {
      setPresetsLoading(true)
      setPresetsError(null)
      try {
        const firstPage = await listPresets(1, 100)
        let items = firstPage.items ?? []
        const pages = firstPage.pages ?? 1

        if (pages > 1) {
          const rest = await Promise.all(
            Array.from({ length: pages - 1 }, (_, index) => listPresets(index + 2, 100)),
          )
          items = [...items, ...rest.flatMap((page) => page.items ?? [])]
        }

        if (!cancelled) {
          setPresets(items)
        }
      } catch (error) {
        if (!cancelled) {
          console.error('Failed to load presets for Flow Lab:', error)
          setPresetsError('Could not load presets for node selection.')
        }
      } finally {
        if (!cancelled) {
          setPresetsLoading(false)
        }
      }
    }

    loadPresetOptions()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!currentFlow.viewport) return
    reactFlow.setViewport(currentFlow.viewport, { duration: 0 })
  }, [currentFlow.id, currentFlow.viewport, reactFlow])

  useEffect(() => {
    if (savedFlows.length > 0 || presetsLoading || presets.length === 0) return

    setNodes((currentNodes) => {
      if (currentNodes.length !== 2 || currentNodes.some((node) => node.data.presetId)) {
        return currentNodes
      }

      return currentNodes.map((node, index) => applyPresetToNode(node, presets[index] ?? presets[0]))
    })
  }, [presets, presetsLoading, savedFlows.length, setNodes])

  const validation = useMemo(() => validateFlow(nodes, edges), [nodes, edges])
  const startChainPlan = useMemo(
    () =>
      getStartChainPlan(
        {
          ...currentFlow,
          name: flowName.trim() || currentFlow.name,
          description: flowDescription.trim(),
        },
        nodes,
        edges,
        validation,
      ),
    [currentFlow, edges, flowDescription, flowName, nodes, validation],
  )

  const nodesForRender = useMemo(() => {
    const executionOrderMap = new Map(validation.executionOrder.map((nodeId, index) => [nodeId, index]))
    const incomingCounts = new Map<string, number>()
    const outgoingCounts = new Map<string, number>()

    for (const edge of edges) {
      incomingCounts.set(edge.target, (incomingCounts.get(edge.target) ?? 0) + 1)
      outgoingCounts.set(edge.source, (outgoingCounts.get(edge.source) ?? 0) + 1)
    }

    return nodes.map((node) => {
      const nodeExecution = flowExecution?.nodeExecutions[node.id]
      return {
        ...node,
        data: {
          ...node.data,
          incomingCount: incomingCounts.get(node.id) ?? 0,
          outgoingCount: outgoingCounts.get(node.id) ?? 0,
          executionIndex: executionOrderMap.get(node.id) ?? null,
          executionStatus: nodeExecution?.status,
          activeRunId: nodeExecution?.runId,
          activeRunStatus: nodeExecution?.lastKnownRunStatus ?? nodeExecution?.launchStatus,
          executionMessage: nodeExecution?.message,
        },
      }
    })
  }, [edges, flowExecution, nodes, validation.executionOrder])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  )

  const presetOptions = useMemo(
    () =>
      presets.map((preset) => ({
        value: preset.id,
        label: preset.name,
      })),
    [presets],
  )

  const onConnect = useCallback(
    (connection: Connection) =>
      setEdges((currentEdges) =>
        addEdge({ ...connection, animated: true, style: { stroke: '#93c5fd', strokeWidth: 2 } }, currentEdges),
      ),
    [setEdges],
  )

  const onDragStart = useCallback((event: React.DragEvent<HTMLButtonElement>, nodeType: string) => {
    event.dataTransfer.setData(DND_NODE_TYPE, nodeType)
    event.dataTransfer.effectAllowed = 'move'
  }, [])

  const addPresetRunAtPosition = useCallback(
    (position: { x: number; y: number }) => {
      setNodes((currentNodes) => {
        const nextNode = createPresetRunNode(position, currentNodes.length + 1)
        setSelectedNodeId(nextNode.id)
        return [...currentNodes, nextNode]
      })
    },
    [setNodes],
  )

  const onDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault()

      const nodeType = event.dataTransfer.getData(DND_NODE_TYPE)
      if (nodeType !== 'presetRun') return

      const position = reactFlow.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      })

      addPresetRunAtPosition(position)
    },
    [addPresetRunAtPosition, reactFlow],
  )

  const handleAddPresetRun = useCallback(() => {
    const bounds = wrapperRef.current?.getBoundingClientRect()
    const fallbackPosition = bounds
      ? reactFlow.screenToFlowPosition({
          x: bounds.left + bounds.width / 2,
          y: bounds.top + bounds.height / 2,
        })
      : { x: 120, y: 120 }
    addPresetRunAtPosition(fallbackPosition)
  }, [addPresetRunAtPosition, reactFlow])

  const updateSelectedNode = useCallback(
    (updater: (node: PresetRunFlowNode) => PresetRunFlowNode) => {
      if (!selectedNodeId) return
      setNodes((currentNodes) =>
        currentNodes.map((node) => (node.id === selectedNodeId ? updater(node) : node)),
      )
    },
    [selectedNodeId, setNodes],
  )

  const handlePresetSelection = useCallback(
    (presetId: string) => {
      const preset = presets.find((candidate) => candidate.id === presetId)
      updateSelectedNode((node) => ({
        ...node,
        data: {
          ...node.data,
          presetId,
          presetName: preset?.name ?? '',
          presetDescription: preset?.description ?? '',
          documentCount: preset?.document_count ?? 0,
          runCount: preset?.run_count ?? 0,
          generators: node.data.generators ?? [],
        },
      }))
    },
    [presets, updateSelectedNode],
  )

  const handleSaveFlow = useCallback(() => {
    const flowToSave = cloneFlowForSave(
      {
        ...currentFlow,
        name: flowName.trim() || 'Untitled Flow',
        description: flowDescription.trim(),
      },
      nodes,
      edges,
      viewport,
    )

    const nextFlows = upsertSavedFlow(flowToSave)
    setSavedFlows(nextFlows)
    setCurrentFlow(flowToSave)
    setSaveMessage(`Saved ${flowToSave.name}`)
  }, [currentFlow, edges, flowDescription, flowName, nodes, viewport])

  const handleNewFlow = useCallback(() => {
    const blankFlow = createBlankSavedFlow({
      name: `Untitled Flow ${savedFlows.length + 1}`,
    })
    setCurrentFlow(blankFlow)
    setFlowName(blankFlow.name)
    setFlowDescription(blankFlow.description ?? '')
    setNodes([])
    setEdges([])
    setViewport(DEFAULT_VIEWPORT)
    setSelectedNodeId(null)
    setFlowExecution(null)
    reactFlow.setViewport(DEFAULT_VIEWPORT, { duration: 0 })
    setSaveMessage('Started a new blank flow')
  }, [reactFlow, savedFlows.length, setEdges, setNodes])

  const handleDeleteFlow = useCallback(() => {
    const nextFlows = deleteSavedFlow(currentFlow.id)
    setSavedFlows(nextFlows)

    if (nextFlows.length > 0) {
      const replacement = nextFlows[0]
      setCurrentFlow(replacement)
      setFlowName(replacement.name)
      setFlowDescription(replacement.description ?? '')
      setNodes(replacement.nodes)
      setEdges(replacement.edges)
      setViewport(replacement.viewport ?? DEFAULT_VIEWPORT)
      setSelectedNodeId(null)
      setFlowExecution(null)
      setSaveMessage(`Deleted flow. Loaded ${replacement.name}`)
      return
    }

    const blankFlow = createBlankSavedFlow()
    setCurrentFlow(blankFlow)
    setFlowName(blankFlow.name)
    setFlowDescription(blankFlow.description ?? '')
    setNodes([])
    setEdges([])
    setViewport(DEFAULT_VIEWPORT)
    setSelectedNodeId(null)
    setFlowExecution(null)
    setSaveMessage('Deleted saved flow. Started a blank one.')
  }, [currentFlow.id, setEdges, setNodes])

  const handleLoadFlow = useCallback(
    (flowId: string) => {
      const flow = savedFlows.find((candidate) => candidate.id === flowId)
      if (!flow) return
      setCurrentFlow(flow)
      setFlowName(flow.name)
      setFlowDescription(flow.description ?? '')
      setNodes(flow.nodes)
      setEdges(flow.edges)
      setViewport(flow.viewport ?? DEFAULT_VIEWPORT)
      setSelectedNodeId(null)
      setFlowExecution(null)
      setSaveMessage(`Loaded ${flow.name}`)
    },
    [savedFlows, setEdges, setNodes],
  )

  const handleStartChain = useCallback(async () => {
    if (!startChainPlan.ok) {
      notify.warning(startChainPlan.reason)
      return
    }

    const flowSnapshot: SavedFlowDefinition = {
      ...currentFlow,
      name: flowName.trim() || currentFlow.name || 'Untitled Flow',
      description: flowDescription.trim(),
      nodes,
      edges,
      viewport,
      updatedAt: new Date().toISOString(),
    }

    const startingExecution = createStartingFlowExecutionRecord(flowSnapshot, flowSnapshot.name, startChainPlan.plan)
    setFlowExecution(startingExecution)
    setIsStartingChain(true)

    try {
      const launch = await launchPresetRunNode(startChainPlan.plan.executableNode)

      setFlowExecution({
        ...startingExecution,
        status: 'running',
        nodeExecutions: {
          ...startingExecution.nodeExecutions,
          [launch.nodeId]: {
            ...startingExecution.nodeExecutions[launch.nodeId],
            status: 'running',
            runId: launch.runId,
            launchStatus: launch.launchStatus,
            lastKnownRunStatus: launch.launchStatus,
            startedAt: launch.startedAt,
            message: 'Underlying APICostX run launched. Downstream nodes stay queued until this run finishes.',
          },
        },
      })
      setSaveMessage(`Chain started from ${startChainPlan.plan.executableNode.data.label || launch.presetName}`)
      notify.success(`Chain started. ${launch.presetName} is now running.`)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start chain.'
      setFlowExecution({
        ...startingExecution,
        status: 'failed',
        nodeExecutions: {
          ...startingExecution.nodeExecutions,
          [startChainPlan.plan.executableNode.id]: {
            ...startingExecution.nodeExecutions[startChainPlan.plan.executableNode.id],
            status: 'failed',
            message,
          },
        },
      })
      notify.error(message)
    } finally {
      setIsStartingChain(false)
    }
  }, [currentFlow, edges, flowDescription, flowName, nodes, startChainPlan, viewport])

  useEffect(() => {
    if (!flowExecution?.currentNodeId) return

    const currentExecution = flowExecution.nodeExecutions[flowExecution.currentNodeId]
    if (!currentExecution?.runId) return
    if (currentExecution.status !== 'running' && currentExecution.status !== 'starting') return

    let cancelled = false
    let inFlight = false

    const pollRunState = async () => {
      if (cancelled || inFlight) return
      inFlight = true

      try {
        const summary = await runsApi.getLiveSummary(currentExecution.runId!)
        if (cancelled) return

        if (!TERMINAL_RUN_STATUSES.has(summary.status)) {
          setFlowExecution((previous) => {
            if (!previous || previous.currentNodeId !== flowExecution.currentNodeId) return previous
            const activeExecution = previous.nodeExecutions[flowExecution.currentNodeId!]
            if (!activeExecution || activeExecution.runId !== currentExecution.runId) return previous

            return {
              ...previous,
              status: 'running',
              nodeExecutions: {
                ...previous.nodeExecutions,
                [flowExecution.currentNodeId!]: {
                  ...activeExecution,
                  status: 'running',
                  launchStatus: summary.status,
                  lastKnownRunStatus: summary.status,
                  message:
                    summary.error_message && summary.error_message.trim().length > 0
                      ? `Underlying APICostX run is ${summary.status}. ${summary.error_message}`
                      : `Underlying APICostX run is ${summary.status}. Waiting for completion before preparing downstream handoff.`,
                },
              },
            }
          })
          return
        }

        const completedRun = await runsApi.getSnapshot(currentExecution.runId!)
        if (cancelled) return

        const currentNode = nodes.find((node) => node.id === flowExecution.currentNodeId)
        if (!currentNode) return

        const artifact = buildHandoffArtifactFromRun(currentNode, completedRun)
        const transition = applyCompletedNodeTransition(
          {
            ...currentFlow,
            name: flowName.trim() || currentFlow.name || 'Untitled Flow',
            description: flowDescription.trim(),
            nodes,
            edges,
            viewport,
            updatedAt: new Date().toISOString(),
          },
          edges,
          flowExecution,
          currentNode.id,
          artifact,
          completedRun.status,
        )

        setFlowExecution((previous) => {
          if (!previous || previous.currentNodeId !== flowExecution.currentNodeId) return previous
          const activeExecution = previous.nodeExecutions[flowExecution.currentNodeId!]
          if (!activeExecution || activeExecution.runId !== currentExecution.runId) return previous

          const finalizedTransition = applyCompletedNodeTransition(
            {
              ...currentFlow,
              name: flowName.trim() || currentFlow.name || 'Untitled Flow',
              description: flowDescription.trim(),
              nodes,
              edges,
              viewport,
              updatedAt: new Date().toISOString(),
            },
            edges,
            previous,
            currentNode.id,
            artifact,
            completedRun.status,
          )

          return {
            ...previous,
            status: finalizedTransition.nextStatus,
            currentNodeId: finalizedTransition.nextCurrentNodeId,
            nodeExecutions: finalizedTransition.nodeExecutions,
          }
        })

        setSaveMessage(transition.summaryMessage)
        if (transition.nextStatus === 'handoff_ready') {
          notify.success(transition.summaryMessage)
        } else if (transition.nextStatus === 'blocked') {
          notify.warning(transition.summaryMessage)
        } else if (transition.nextStatus === 'completed') {
          notify.success('Chain finished. No downstream handoff was needed.')
        }
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : 'Failed to monitor the active preset run.'
        setFlowExecution((previous) => {
          if (!previous || previous.currentNodeId !== flowExecution.currentNodeId) return previous
          const activeExecution = previous.nodeExecutions[flowExecution.currentNodeId!]
          if (!activeExecution || activeExecution.runId !== currentExecution.runId) return previous

          return {
            ...previous,
            status: 'failed',
            nodeExecutions: {
              ...previous.nodeExecutions,
              [flowExecution.currentNodeId!]: {
                ...activeExecution,
                status: 'failed',
                message,
              },
            },
          }
        })
        setSaveMessage(message)
        notify.error(message)
      } finally {
        inFlight = false
      }
    }

    void pollRunState()
    const intervalId = window.setInterval(() => {
      void pollRunState()
    }, 4000)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [currentFlow, edges, flowDescription, flowExecution, flowName, nodes, viewport])

  useEffect(() => {
    if (!flowExecution?.currentNodeId) return

    const currentNode = nodes.find((node) => node.id === flowExecution.currentNodeId)
    const currentExecution = flowExecution.nodeExecutions[flowExecution.currentNodeId]

    if (!currentNode || !currentExecution) return
    if (currentExecution.status !== 'handoff_ready') return
    if (!currentExecution.handoffArtifact || currentExecution.handoffArtifact.status !== 'ready') return
    const handoffArtifact = currentExecution.handoffArtifact

    const launchKey = [
      flowExecution.id,
      currentNode.id,
      handoffArtifact.runId,
      handoffArtifact.createdAt,
    ].join(':')

    if (downstreamLaunchKeyRef.current === launchKey) return
    downstreamLaunchKeyRef.current = launchKey

    let cancelled = false

    const launchDownstreamNode = async () => {
      setFlowExecution((previous) => {
        if (!previous || previous.id !== flowExecution.id) return previous
        const targetExecution = previous.nodeExecutions[currentNode.id]
        if (!targetExecution || targetExecution.status !== 'handoff_ready') return previous

        return {
          ...previous,
          status: 'running',
          nodeExecutions: {
            ...previous.nodeExecutions,
            [currentNode.id]: {
              ...targetExecution,
              status: 'starting',
              message: 'Materializing upstream docs into temporary flow inputs and launching the downstream preset run…',
            },
          },
        }
      })

      try {
        const launch = await launchPresetRunNodeFromHandoff(
          currentNode,
          handoffArtifact,
          flowExecution.flowName,
        )

        if (cancelled) return

        setFlowExecution((previous) => {
          if (!previous || previous.id !== flowExecution.id) return previous
          const targetExecution = previous.nodeExecutions[currentNode.id]
          if (!targetExecution) return previous

          return {
            ...previous,
            status: 'running',
            currentNodeId: currentNode.id,
            nodeExecutions: {
              ...previous.nodeExecutions,
              [currentNode.id]: {
                ...targetExecution,
                status: 'running',
                runId: launch.runId,
                launchStatus: launch.launchStatus,
                lastKnownRunStatus: launch.launchStatus,
                startedAt: launch.startedAt,
                materializedDocumentIds: launch.materializedDocumentIds,
                temporaryPresetId: launch.temporaryPresetId,
                temporaryInputCleanupStatus: launch.materializedDocumentIds.length > 0 ? 'pending' : undefined,
                message: `Underlying APICostX run launched from ${launch.materializedDocumentIds.length} upstream handoff doc${launch.materializedDocumentIds.length === 1 ? '' : 's'}.`,
              },
            },
          }
        })

        setSaveMessage(`Launched ${currentNode.data.label || launch.presetName} from upstream handoff docs`)
        notify.success(`${currentNode.data.label || launch.presetName} launched from upstream handoff docs.`)
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : 'Failed to launch the downstream preset run from handoff docs.'

        setFlowExecution((previous) => {
          if (!previous || previous.id !== flowExecution.id) return previous
          const targetExecution = previous.nodeExecutions[currentNode.id]
          if (!targetExecution) return previous

          return {
            ...previous,
            status: 'failed',
            nodeExecutions: {
              ...previous.nodeExecutions,
              [currentNode.id]: {
                ...targetExecution,
                status: 'failed',
                message,
              },
            },
          }
        })

        setSaveMessage(message)
        notify.error(message)
      }
    }

    void launchDownstreamNode()

    return () => {
      cancelled = true
    }
  }, [flowExecution, nodes])

  useEffect(() => {
    if (!flowExecution) return

    const cleanupCandidate = Object.values(flowExecution.nodeExecutions).find((nodeExecution) => {
      if (!nodeExecution.materializedDocumentIds?.length) return false
      if (nodeExecution.temporaryInputCleanupStatus !== 'pending') return false
      return nodeExecution.status === 'completed' || nodeExecution.status === 'failed' || nodeExecution.status === 'blocked'
    })

    if (!cleanupCandidate) return

    let cancelled = false

    const runCleanup = async () => {
      try {
        await cleanupTemporaryFlowInputs(cleanupCandidate.materializedDocumentIds!)
        if (cancelled) return

        setFlowExecution((previous) => {
          if (!previous) return previous
          const target = previous.nodeExecutions[cleanupCandidate.nodeId]
          if (!target || target.temporaryInputCleanupStatus !== 'pending') return previous

          return {
            ...previous,
            nodeExecutions: {
              ...previous.nodeExecutions,
              [cleanupCandidate.nodeId]: {
                ...target,
                temporaryInputCleanupStatus: 'completed',
                temporaryInputCleanupError: undefined,
                message: `${target.message ? `${target.message} ` : ''}Temporary flow inputs cleaned up.`,
              },
            },
          }
        })
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : 'Failed to clean up temporary flow inputs.'

        setFlowExecution((previous) => {
          if (!previous) return previous
          const target = previous.nodeExecutions[cleanupCandidate.nodeId]
          if (!target || target.temporaryInputCleanupStatus !== 'pending') return previous

          return {
            ...previous,
            nodeExecutions: {
              ...previous.nodeExecutions,
              [cleanupCandidate.nodeId]: {
                ...target,
                temporaryInputCleanupStatus: 'failed',
                temporaryInputCleanupError: message,
                message: `${target.message ? `${target.message} ` : ''}Temporary flow input cleanup failed.`,
              },
            },
          }
        })
      }
    }

    void runCleanup()

    return () => {
      cancelled = true
    }
  }, [flowExecution])

  const handleOpenLiveRun = useCallback(
    (runId: string) => {
      navigate(`/execute/${runId}`)
    },
    [navigate],
  )

  const savedFlowOptions = useMemo(
    () =>
      savedFlows.map((flow) => ({
        value: flow.id,
        label: flow.name,
      })),
    [savedFlows],
  )

  const selectedNodeIncomingCount = selectedNode
    ? edges.filter((edge) => edge.target === selectedNode.id).length
    : 0
  const selectedNodeExecution = selectedNode ? flowExecution?.nodeExecutions[selectedNode.id] ?? null : null
  const selectedNodeInputOverrideMode = selectedNode ? getNodeInputOverrideMode(selectedNode.data) : 'automatic'
  const selectedNodeOutputOverrideMode = selectedNode ? getNodeOutputOverrideMode(selectedNode.data) : 'automatic'
  const selectedNodeEffectiveInputMode = selectedNode
    ? getEffectiveInputMode(selectedNode.data, selectedNodeIncomingCount)
    : 'preset_inputs'
  const selectedNodeEffectiveOutputMode = selectedNode
    ? getEffectiveOutputMode(selectedNode.data)
    : 'normalized_chain_output'

  return (
    <div className="flex min-h-0 flex-1 gap-4 xl:flex-row">
      <aside className="w-full space-y-4 rounded-2xl border border-gray-700 bg-gray-900/90 p-4 xl:max-w-sm">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300">Saved Flows MVP</p>
          <h2 className="mt-2 text-lg font-semibold text-gray-100">Flow Builder</h2>
          <p className="mt-2 text-sm leading-6 text-gray-400">
            Build saved flows by chaining preset runs. Root nodes use the preset input docs already saved inside the
            preset. Connected nodes consume upstream output docs.
          </p>
        </div>

        <div className="space-y-3 rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
          <div className="flex items-center gap-2">
            <Workflow className="h-4 w-4 text-blue-300" />
            <h3 className="text-sm font-semibold text-gray-100">Saved Flow</h3>
          </div>
          <div className="space-y-2">
            <label className="block text-xs font-medium uppercase tracking-[0.14em] text-gray-400">Name</label>
            <input
              value={flowName}
              onChange={(event) => setFlowName(event.target.value)}
              className="w-full rounded-lg border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
              placeholder="Untitled Flow"
              data-testid="flow-lab-flow-name"
            />
          </div>
          <div className="space-y-2">
            <label className="block text-xs font-medium uppercase tracking-[0.14em] text-gray-400">Saved flows</label>
            <Select
              value={savedFlows.some((flow) => flow.id === currentFlow.id) ? currentFlow.id : ''}
              onChange={handleLoadFlow}
              options={savedFlowOptions}
              placeholder="Load a saved flow"
              data-testid="flow-lab-saved-flow-select"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={handleSaveFlow}
              icon={<Save className="h-4 w-4" />}
              data-testid="flow-lab-save"
            >
              Save Flow
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNewFlow}
              icon={<Plus className="h-4 w-4" />}
              data-testid="flow-lab-new"
            >
              New
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleDeleteFlow}
              icon={<Trash2 className="h-4 w-4" />}
              disabled={!savedFlows.some((flow) => flow.id === currentFlow.id)}
              data-testid="flow-lab-delete"
            >
              Delete
            </Button>
          </div>
          <p className="text-xs text-gray-400">{saveMessage}</p>
        </div>

        <div className="space-y-3 rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
          <div className="flex items-center gap-2">
            <GitBranchPlus className="h-4 w-4 text-violet-300" />
            <h3 className="text-sm font-semibold text-gray-100">Node Palette</h3>
          </div>
          <button
            type="button"
            draggable
            onDragStart={(event) => onDragStart(event, 'presetRun')}
            className="w-full rounded-xl border border-dashed border-blue-500/40 bg-blue-500/10 px-3 py-3 text-left transition hover:bg-blue-500/15"
            data-testid="flow-lab-palette-preset-run"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-blue-100">
              <Boxes className="h-4 w-4" />
              Preset Run
            </div>
            <p className="mt-1 text-xs leading-5 text-blue-200/80">
              Drag onto the canvas. Root nodes use preset inputs. Connected nodes consume upstream outputs.
            </p>
          </button>
          <Button variant="outline" size="sm" onClick={handleAddPresetRun} icon={<Plus className="h-4 w-4" />}>
            Add Preset Run
          </Button>
        </div>

        <div className="space-y-3 rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
          <div className="flex items-center gap-2">
            <LayoutList className="h-4 w-4 text-emerald-300" />
            <h3 className="text-sm font-semibold text-gray-100">Execution Readiness</h3>
          </div>
          <Button
            variant="success"
            size="sm"
            onClick={handleStartChain}
            loading={isStartingChain}
            icon={<Play className="h-4 w-4" />}
            disabled={!startChainPlan.ok}
            data-testid="flow-lab-start-chain"
          >
            {isStartingChain ? 'Starting Chain…' : 'Start Chain'}
          </Button>
          {!startChainPlan.ok ? (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-100">
              {startChainPlan.reason}
            </div>
          ) : (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs leading-5 text-emerald-100">
              Rooted chains can launch from this page. Downstream nodes stay queued until upstream completion prepares handoff docs.
            </div>
          )}
          <div className="space-y-2">
            {validation.issues.map((issue, index) => (
              <div
                key={`${issue.level}-${index}`}
                className={cn(
                  'rounded-xl border px-3 py-2 text-xs leading-5',
                  issue.level === 'error' && 'border-red-500/30 bg-red-500/10 text-red-100',
                  issue.level === 'warning' && 'border-amber-500/30 bg-amber-500/10 text-amber-100',
                  issue.level === 'info' && 'border-gray-700 bg-gray-900/60 text-gray-300',
                )}
              >
                {issue.message}
              </div>
            ))}
          </div>
          <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-3">
            <div className="flex items-center justify-between text-xs uppercase tracking-[0.14em] text-gray-500">
              <span>Executable</span>
              <span className={validation.executable ? 'text-emerald-300' : 'text-amber-300'}>
                {validation.executable ? 'Yes' : 'Not yet'}
              </span>
            </div>
            <div className="mt-3 space-y-2">
              {validation.executionOrder.length > 0 ? (
                validation.executionOrder.map((nodeId, index) => {
                  const node = nodes.find((candidate) => candidate.id === nodeId)
                  return (
                    <div key={nodeId} className="flex items-center gap-2 text-sm text-gray-200">
                      <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-800 text-[11px] font-semibold text-gray-300">
                        {index + 1}
                      </span>
                      <span className="truncate">{node?.data.label || node?.data.presetName || nodeId}</span>
                    </div>
                  )
                })
              ) : (
                <p className="text-sm text-gray-400">Execution order will appear once the graph becomes valid enough to schedule.</p>
              )}
            </div>
          </div>
          {flowExecution ? (
            <div className="rounded-xl border border-gray-700 bg-gray-900/60 p-3" data-testid="flow-lab-execution-state">
              <div className="flex items-center justify-between text-xs uppercase tracking-[0.14em] text-gray-500">
                <span>Current chain</span>
                <span
                  className={cn(getExecutionTone(flowExecution.status))}
                >
                  {formatExecutionStatusLabel(flowExecution.status)}
                </span>
              </div>
              <div className="mt-3 space-y-2 text-sm text-gray-200">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-gray-400">Flow</span>
                  <span className="text-right font-medium text-gray-100">{flowExecution.flowName}</span>
                </div>
                {flowExecution.currentNodeId ? (
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Current node</span>
                    <span className="text-right font-medium text-gray-100">
                      {nodes.find((node) => node.id === flowExecution.currentNodeId)?.data.label || flowExecution.currentNodeId}
                    </span>
                  </div>
                ) : null}
                {flowExecution.currentNodeId && flowExecution.nodeExecutions[flowExecution.currentNodeId]?.runId ? (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <span className="text-gray-400">Run id</span>
                      <span className="break-all text-right font-medium text-gray-100">
                        {flowExecution.nodeExecutions[flowExecution.currentNodeId]?.runId}
                      </span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleOpenLiveRun(flowExecution.nodeExecutions[flowExecution.currentNodeId!].runId!)}
                      icon={<ArrowUpRight className="h-4 w-4" />}
                      data-testid="flow-lab-open-live-run"
                    >
                      Open Live Run
                    </Button>
                  </>
                ) : null}
                <div className="mt-3 space-y-2 rounded-xl border border-gray-700 bg-gray-950/50 p-3">
                  <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Nodes</div>
                  {flowExecution.nodeOrder.map((nodeId, index) => {
                    const nodeExecution = flowExecution.nodeExecutions[nodeId]
                    const node = nodes.find((candidate) => candidate.id === nodeId)
                    if (!nodeExecution) return null
                    return (
                      <div key={nodeId} className="rounded-lg border border-gray-800 bg-gray-900/70 px-3 py-2">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Step {index + 1}</div>
                            <div className="truncate font-medium text-gray-100">{node?.data.label || nodeExecution.presetName}</div>
                          </div>
                          <span className={cn('text-xs font-medium uppercase tracking-[0.12em]', getExecutionTone(nodeExecution.status))}>
                            {formatExecutionStatusLabel(nodeExecution.status)}
                          </span>
                        </div>
                        {nodeExecution.message ? (
                          <p className="mt-2 text-xs leading-5 text-gray-400">{nodeExecution.message}</p>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 gap-4 lg:flex-row">
        <div className="min-h-[42rem] min-w-0 flex-1 overflow-hidden rounded-2xl border border-gray-700 bg-[#0b1020] shadow-[0_25px_80px_rgba(15,23,42,0.45)]">
          <div className="border-b border-gray-800 px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-100">Canvas</h3>
                <p className="mt-1 text-xs text-gray-400">
                  Drag preset-run nodes into the canvas and connect them left to right.
                </p>
              </div>
              <div className="rounded-full border border-gray-700 bg-gray-900/70 px-3 py-1 text-xs text-gray-300">
                {nodes.length} node{nodes.length === 1 ? '' : 's'} / {edges.length} connection{edges.length === 1 ? '' : 's'}
              </div>
            </div>
          </div>

          <div ref={wrapperRef} className="relative h-[calc(100%-65px)]" onDragOver={onDragOver} onDrop={onDrop}>
            {nodes.length === 0 ? (
              <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
                <div className="max-w-md rounded-2xl border border-dashed border-gray-600 bg-gray-900/80 px-6 py-5 text-center shadow-xl">
                  <p className="text-sm font-semibold text-gray-100">Blank canvas</p>
                  <p className="mt-2 text-sm leading-6 text-gray-400">
                    Drag a <span className="font-medium text-gray-200">Preset Run</span> node from the palette to start a saved flow.
                  </p>
                </div>
              </div>
            ) : null}

            <ReactFlow
              nodes={nodesForRender}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onSelectionChange={({ nodes: selectedNodes }) => {
                const selected = selectedNodes[0] as Node | undefined
                setSelectedNodeId(selected?.id ?? null)
              }}
              onMoveEnd={(_, nextViewport) => setViewport(nextViewport)}
              fitView={nodes.length > 0}
              fitViewOptions={{ padding: 0.18 }}
              deleteKeyCode={['Backspace', 'Delete']}
              className="bg-[radial-gradient(circle_at_top_left,_rgba(59,130,246,0.12),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(167,139,250,0.12),_transparent_34%),linear-gradient(180deg,_#0b1020,_#111827)]"
              data-testid="flow-lab-canvas"
            >
              <MiniMap pannable zoomable className="!bg-gray-950/90" maskColor="rgba(15, 23, 42, 0.55)" />
              <Controls className="!shadow-lg" />
              <Background variant={BackgroundVariant.Dots} gap={20} size={1.5} color="rgba(148, 163, 184, 0.25)" />
            </ReactFlow>
          </div>
        </div>

        <aside className="w-full space-y-4 rounded-2xl border border-gray-700 bg-gray-900/90 p-4 lg:max-w-sm">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-violet-300">Inspector</p>
            <h3 className="mt-2 text-lg font-semibold text-gray-100">
              {selectedNode ? selectedNode.data.label || 'Preset Run' : 'No node selected'}
            </h3>
            <p className="mt-2 text-sm leading-6 text-gray-400">
              {selectedNode
                ? 'Edit the selected Preset Run node. Root nodes keep using their preset input docs. Connected nodes use upstream outputs when the flow eventually runs.'
                : 'Select a node on the canvas to choose a preset and edit its label.'}
            </p>
          </div>

          {selectedNode ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="block text-xs font-medium uppercase tracking-[0.14em] text-gray-400">Node label</label>
                <input
                  value={selectedNode.data.label}
                  onChange={(event) =>
                    updateSelectedNode((node) => ({
                      ...node,
                      data: {
                        ...node.data,
                        label: event.target.value,
                      },
                    }))
                  }
                  className="w-full rounded-lg border border-gray-600 bg-gray-700 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                  placeholder="Preset Run 1"
                  data-testid="flow-lab-node-label"
                />
              </div>

              <Select
                value={selectedNode.data.presetId}
                onChange={handlePresetSelection}
                options={presetOptions}
                label="Preset"
                placeholder={presetsLoading ? 'Loading presets...' : 'Select a preset'}
                disabled={presetsLoading}
                data-testid="flow-lab-node-preset"
              />

              {presetsError ? (
                <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-100">
                  {presetsError}
                </div>
              ) : null}

              <div className="rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
                <div className="flex items-center gap-2 text-sm font-semibold text-gray-100">
                  <Link2 className="h-4 w-4 text-blue-300" />
                  Input behavior
                </div>
                <div className="mt-3 rounded-xl border border-gray-700 bg-gray-900/60 p-3">
                  <p className="text-xs leading-5 text-gray-400">
                    These overrides live only in Flow Lab. They do not change the preset itself.
                  </p>
                </div>
                <div className="mt-3">
                  <Select
                    value={selectedNodeInputOverrideMode}
                    onChange={(value) =>
                      updateSelectedNode((node) => ({
                        ...node,
                        data: {
                          ...node.data,
                          inputOverrideMode: value as typeof selectedNodeInputOverrideMode,
                        },
                      }))
                    }
                    options={INPUT_OVERRIDE_OPTIONS}
                    label="Input override"
                    data-testid="flow-lab-node-input-override"
                  />
                </div>
                <div className="mt-3 space-y-2 text-sm text-gray-300">
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Effective mode</span>
                    <span className="text-right font-medium text-gray-100">{getInputModeLabel(selectedNodeEffectiveInputMode)}</span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Override mode</span>
                    <span className="text-right font-medium text-gray-100">
                      {selectedNodeInputOverrideMode === 'automatic'
                        ? 'Automatic'
                        : selectedNodeInputOverrideMode === 'preset_inputs'
                          ? 'Forced preset inputs'
                          : 'Forced upstream docs'}
                    </span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Incoming connections</span>
                    <span className="font-medium text-gray-100">{selectedNodeIncomingCount}</span>
                  </div>
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Preset docs</span>
                    <span className="font-medium text-gray-100">{selectedNode.data.documentCount ?? 0}</span>
                  </div>
                  {selectedNodeInputOverrideMode === 'upstream_docs' && selectedNodeIncomingCount === 0 ? (
                    <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-100">
                      This node is forced to use upstream docs, but it has no incoming connection yet.
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
                <div className="text-sm font-semibold text-gray-100">Output behavior</div>
                <div className="mt-3">
                  <Select
                    value={selectedNodeOutputOverrideMode}
                    onChange={(value) =>
                      updateSelectedNode((node) => ({
                        ...node,
                        data: {
                          ...node.data,
                          outputOverrideMode: value as typeof selectedNodeOutputOverrideMode,
                        },
                      }))
                    }
                    options={OUTPUT_OVERRIDE_OPTIONS}
                    label="Output override"
                    data-testid="flow-lab-node-output-override"
                  />
                </div>
                <div className="mt-3 space-y-2 text-sm text-gray-300">
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-gray-400">Effective mode</span>
                    <span className="text-right font-medium text-gray-100">{getOutputModeLabel(selectedNodeEffectiveOutputMode)}</span>
                  </div>
                  {selectedNodeOutputOverrideMode === 'no_chain_output' ? (
                    <p className="rounded-xl border border-blue-500/20 bg-blue-500/10 px-3 py-2 text-xs leading-5 text-blue-100">
                      Future downstream nodes will not receive handoff docs from this node while this override is active.
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="rounded-2xl border border-gray-700 bg-gray-800/70 p-3">
                <div className="text-sm font-semibold text-gray-100">Preset snapshot</div>
                {selectedNode.data.presetId ? (
                  <div className="mt-3 space-y-3 text-sm">
                    <div>
                      <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Name</div>
                      <div className="mt-1 text-gray-100">{selectedNode.data.presetName}</div>
                    </div>
                    {selectedNode.data.presetDescription ? (
                      <div>
                        <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Description</div>
                        <div className="mt-1 leading-6 text-gray-300">{selectedNode.data.presetDescription}</div>
                      </div>
                    ) : null}
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2">
                        <div className="text-[10px] uppercase tracking-[0.14em] text-gray-500">Docs</div>
                        <div className="mt-1 text-gray-100">{selectedNode.data.documentCount ?? 0}</div>
                      </div>
                      <div className="rounded-xl border border-gray-700 bg-gray-900/70 px-3 py-2">
                        <div className="text-[10px] uppercase tracking-[0.14em] text-gray-500">Runs</div>
                        <div className="mt-1 text-gray-100">{selectedNode.data.runCount ?? 0}</div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-gray-400">
                    Choose a preset to attach a real preset configuration to this node.
                  </p>
                )}
              </div>

              {selectedNodeExecution ? (
                <div className="rounded-2xl border border-gray-700 bg-gray-800/70 p-3" data-testid="flow-lab-node-execution">
                  <div className="text-sm font-semibold text-gray-100">Chain execution</div>
                  <div className="mt-3 space-y-2 text-sm text-gray-300">
                    <div className="flex items-start justify-between gap-3">
                      <span className="text-gray-400">Status</span>
                        <span
                          className={cn('font-medium', getExecutionTone(selectedNodeExecution.status))}
                        >
                          {formatExecutionStatusLabel(selectedNodeExecution.status)}
                        </span>
                      </div>
                    {selectedNodeExecution.message ? (
                      <p className="rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2 text-xs leading-5 text-gray-300">
                        {selectedNodeExecution.message}
                      </p>
                    ) : null}
                    {selectedNodeExecution.waitingForNodeId ? (
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-gray-400">Waiting for</span>
                        <span className="text-right font-medium text-gray-100">
                          {nodes.find((node) => node.id === selectedNodeExecution.waitingForNodeId)?.data.label ||
                            selectedNodeExecution.waitingForNodeId}
                        </span>
                      </div>
                    ) : null}
                    {selectedNodeExecution.lastKnownRunStatus ? (
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-gray-400">Last run status</span>
                        <span className="text-right font-medium text-gray-100">{selectedNodeExecution.lastKnownRunStatus}</span>
                      </div>
                    ) : null}
                    {selectedNodeExecution.materializedDocumentIds?.length ? (
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-gray-400">Flow input docs</span>
                        <span className="text-right font-medium text-gray-100">
                          {selectedNodeExecution.materializedDocumentIds.length}
                        </span>
                      </div>
                    ) : null}
                    {selectedNodeExecution.temporaryPresetId ? (
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-gray-400">Temp preset</span>
                        <span className="break-all text-right font-medium text-gray-100">{selectedNodeExecution.temporaryPresetId}</span>
                      </div>
                    ) : null}
                    {selectedNodeExecution.temporaryInputCleanupStatus ? (
                      <div className="flex items-start justify-between gap-3">
                        <span className="text-gray-400">Input cleanup</span>
                        <span className="text-right font-medium text-gray-100">{selectedNodeExecution.temporaryInputCleanupStatus}</span>
                      </div>
                    ) : null}
                    {selectedNodeExecution.runId ? (
                      <>
                        <div className="flex items-start justify-between gap-3">
                          <span className="text-gray-400">Underlying run</span>
                          <span className="break-all text-right font-medium text-gray-100">{selectedNodeExecution.runId}</span>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleOpenLiveRun(selectedNodeExecution.runId!)}
                          icon={<ArrowUpRight className="h-4 w-4" />}
                          data-testid="flow-lab-node-open-run"
                        >
                          Open Execute Page
                        </Button>
                      </>
                    ) : null}
                    {selectedNodeExecution.handoffArtifact ? (
                      <div className="rounded-xl border border-gray-700 bg-gray-900/60 px-3 py-2 text-xs leading-5 text-gray-300">
                        <div className="flex items-start justify-between gap-3">
                          <span className="text-gray-400">Handoff</span>
                          <span className="font-medium text-gray-100">{selectedNodeExecution.handoffArtifact.status}</span>
                        </div>
                        <div className="mt-2 flex items-start justify-between gap-3">
                          <span className="text-gray-400">Derived from</span>
                          <span className="font-medium text-gray-100">{selectedNodeExecution.handoffArtifact.derivedFrom}</span>
                        </div>
                        <div className="mt-2 flex items-start justify-between gap-3">
                          <span className="text-gray-400">Docs</span>
                          <span className="font-medium text-gray-100">{selectedNodeExecution.handoffArtifact.docCount}</span>
                        </div>
                        <p className="mt-2 text-gray-400">{selectedNodeExecution.handoffArtifact.summary}</p>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-gray-700 bg-gray-800/50 p-4 text-sm leading-6 text-gray-400">
              Nothing is selected yet. Click a node on the canvas to edit it here.
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export default function FlowLabCanvas() {
  return <FlowLabInner />
}
