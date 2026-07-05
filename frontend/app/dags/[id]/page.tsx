'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import dynamic from 'next/dynamic'
import { StatusBadge } from '../../components/StatusComponents'
import { API, TEMPORAL_UI } from '../../lib/api'

const DAGGraph = dynamic(() => import('../../components/DAGGraph'), { ssr: false })

interface DAGNode {
  id: number
  dag_id: string
  node_id: string
  skill_id: string | null
  description: string | null
  status: string
  depends_on: string[]
  config: Record<string, any>
  input_mapping: Record<string, any>
  output_data: any
  task_id: string | null
  container_id: string | null
  started_at: string | null
  completed_at: string | null
  selected_skill_v2_id: string | null
  skill_selection_reason: string | null
}

interface DAGDetail {
  id: string
  objective: string
  status: string
  workspace_id: string
  llm_model: string
  workflow_id: string | null
  dag_json: any
  nodes: DAGNode[]
  created_at: string
  started_at: string | null
  completed_at: string | null
}

interface TaskOutput {
  id: number
  iteration: number
  completed: string
  capability_requested: string
  agent_logs: string | null
  output: string | null
  error: string | null
  llm_response_preview: string | null
  model_used: string | null
  image_used: string | null
  duration_ms: number | null
  deliverables: Record<string, string> | null
  raw_result: any
  created_at: string | null
}

interface SBOMData {
  id: number
  task_id: string
  image_tag: string
  image_version: number
  format: string
  packages: { name: string; version: string; type: string; license: string }[]
  generator: string | null
  generated_at: string
}

interface NodeStateSnapshot {
  id: number
  dag_id: string
  node_id: string
  task_id: string | null
  phase: string
  status: string
  wave: number | null
  attempt: number
  input_context: Record<string, any>
  output_context: Record<string, any>
  completion_state: Record<string, any>
  acquisition_log: Record<string, any>[]
  acceptance_result: Record<string, any>
  pending_items: any[]
  created_at: string
}

interface NodeAuditEvent {
  id: number
  dag_id: string
  node_id: string
  task_id: string | null
  event_type: string
  severity: string
  message: string
  event_data: Record<string, any>
  created_at: string
}

interface NodeAcceptanceResponse {
  node_id: string
  status: string
  acceptance_verdict: string | null
  acceptance_score: number
  success_criteria: string[]
  criteria_met: Record<string, boolean>
  skill_id: string | null
  skill_followed: boolean | null
  deliverables_keys: string[]
  workspace_step_path: string | null
}

interface WorkspaceManifestResponse {
  workspace_id: string
  step_manifest: Record<string, string[]>
  total_files: number
  steps_with_deliverables: string[]
}

function summarizeNodeFailureReason(
  node: DAGNode,
  stateEntry?: { latest: NodeStateSnapshot | null; events: NodeAuditEvent[] },
): string | null {
  const output = node.output_data || {}
  const candidates: Array<string | null | undefined> = [
    output.gate_failure,
    output.error,
    output.reason,
    output.message,
    stateEntry?.latest?.acceptance_result?.reason,
    stateEntry?.events?.find((e) => e.severity === 'critical')?.message,
    stateEntry?.events?.[0]?.message,
  ]

  for (const raw of candidates) {
    const text = String(raw || '').trim()
    if (!text) continue
    return text.length > 260 ? `${text.slice(0, 257)}...` : text
  }
  return null
}

export default function DAGDetailPage() {
  const params = useParams()
  const dagId = params.id as string
  const router = useRouter()
  const [dag, setDag] = useState<DAGDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
const [activeTab, setActiveTab] = useState<'overview' | 'outputs' | 'audit' | 'sbom' | 'workspace'>('overview')
   const [showRevise, setShowRevise] = useState(false)
  const [reviseComments, setReviseComments] = useState('')
  const [revising, setRevising] = useState(false)
  const [showNodeActions, setShowNodeActions] = useState(false)
  const [nodeActionLoading, setNodeActionLoading] = useState(false)
  const [showEnhanceDialog, setShowEnhanceDialog] = useState(false)
  const [enhanceMode, setEnhanceMode] = useState<'rewrite' | 'split'>('rewrite')
  const [enhanceGuidance, setEnhanceGuidance] = useState('')
  const [enhanceSplitCount, setEnhanceSplitCount] = useState(2)

  // Per-node task data cache
  const [nodeTaskData, setNodeTaskData] = useState<Record<string, {
    timeline: any
    outputs: TaskOutput[]
    currentState: any
    auditTurns: any
    sbom: SBOMData | null | undefined
  }>>({})
  const [nodeDataLoading, setNodeDataLoading] = useState(false)
  const [nodeState, setNodeState] = useState<Record<string, { latest: NodeStateSnapshot | null; events: NodeAuditEvent[] }>>({})
  const [nodeAcceptance, setNodeAcceptance] = useState<Record<string, NodeAcceptanceResponse | null>>({})
  const [workspaceManifest, setWorkspaceManifest] = useState<WorkspaceManifestResponse | null>(null)

  const fetchDag = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/dags/${dagId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDag(await res.json())
    } catch (err: any) {
      setError(err.message)
    }
  }, [dagId])

  useEffect(() => { fetchDag() }, [fetchDag])

  // Auto-refresh while running
  useEffect(() => {
    if (dag?.status === 'running') {
      const interval = setInterval(fetchDag, 4000)
      return () => clearInterval(interval)
    }
  }, [dag?.status, fetchDag])

  // Fetch task data when a node with task_id is selected
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node?.task_id) return
    if (nodeTaskData[node.task_id]) return

    const taskId = node.task_id
    setNodeDataLoading(true)

    const fetchNodeTask = async () => {
      try {
        const [timelineRes, stateRes, outputsRes] = await Promise.all([
          fetch(`${API}/api/tasks/${taskId}/execution-timeline`),
          fetch(`${API}/api/tasks/${taskId}/current-state`),
          fetch(`${API}/api/tasks/${taskId}/outputs`),
        ])
        const timeline = timelineRes.ok ? await timelineRes.json() : null
        const currentState = stateRes.ok ? await stateRes.json() : null
        const outputsData = outputsRes.ok ? await outputsRes.json() : { outputs: [] }

        setNodeTaskData(prev => ({
          ...prev,
          [taskId]: {
            timeline,
            outputs: outputsData.outputs || [],
            currentState,
            auditTurns: null,
            sbom: undefined,
          }
        }))
      } catch (e) {
        console.error('Failed to fetch node task data', e)
      } finally {
        setNodeDataLoading(false)
      }
    }

    fetchNodeTask()
  }, [selectedNodeId, dag?.nodes, nodeTaskData])

  // Load audit turns when audit tab is active
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node?.task_id || activeTab !== 'audit') return
    const taskId = node.task_id
    if (nodeTaskData[taskId]?.auditTurns) return

    const fetchAudit = async () => {
      try {
        const res = await fetch(`${API}/api/tasks/${taskId}/audit-turns`)
        if (res.ok) {
          const data = await res.json()
          setNodeTaskData(prev => ({
            ...prev,
            [taskId]: { ...prev[taskId], auditTurns: data }
          }))
        }
      } catch (e) { /* ignore */ }
    }
    fetchAudit()
  }, [selectedNodeId, activeTab, dag?.nodes, nodeTaskData])

  // Load SBOM when sbom tab is active
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node?.task_id || activeTab !== 'sbom') return
    const taskId = node.task_id
    if (nodeTaskData[taskId]?.sbom !== undefined) return

    const fetchSbom = async () => {
      try {
        const res = await fetch(`${API}/api/tasks/${taskId}/sbom`)
        if (res.ok) {
          const data = await res.json()
          setNodeTaskData(prev => ({
            ...prev,
            [taskId]: { ...prev[taskId], sbom: data }
          }))
        } else {
          setNodeTaskData(prev => ({
            ...prev,
            [taskId]: { ...prev[taskId], sbom: null }
          }))
        }
      } catch (e) { /* ignore */ }
    }
    fetchSbom()
  }, [selectedNodeId, activeTab, dag?.nodes, nodeTaskData])

  // Load DAG execution state/provenance for selected node
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node) return
    if (nodeState[node.node_id]) return

    const loadNodeState = async () => {
      try {
        const [latestRes, eventsRes] = await Promise.all([
          fetch(`${API}/api/dags/${dagId}/nodes/${node.node_id}/state/latest`),
          fetch(`${API}/api/dags/${dagId}/nodes/${node.node_id}/audit-events?limit=20`),
        ])
        const latest = latestRes.ok ? await latestRes.json() : null
        const events = eventsRes.ok ? await eventsRes.json() : []
        setNodeState(prev => ({ ...prev, [node.node_id]: { latest, events } }))
      } catch {
        setNodeState(prev => ({ ...prev, [node.node_id]: { latest: null, events: [] } }))
      }
    }

    loadNodeState()
  }, [selectedNodeId, dag?.nodes, dagId, nodeState])

  useEffect(() => {
    setShowNodeActions(false)
    setShowEnhanceDialog(false)
  }, [selectedNodeId])

  // Preload failure state for failed nodes so DAG-level summary is visible
  // even before the user clicks into a specific node.
  useEffect(() => {
    if (!dag?.nodes?.length) return

    const failedNodesToLoad = dag.nodes.filter(
      (node) => node.status === 'failed' && !nodeState[node.node_id],
    )
    if (failedNodesToLoad.length === 0) return

    const loadFailedNodeStates = async () => {
      const entries = await Promise.all(
        failedNodesToLoad.map(async (node) => {
          try {
            const [latestRes, eventsRes] = await Promise.all([
              fetch(`${API}/api/dags/${dagId}/nodes/${node.node_id}/state/latest`),
              fetch(`${API}/api/dags/${dagId}/nodes/${node.node_id}/audit-events?limit=20`),
            ])
            const latest = latestRes.ok ? await latestRes.json() : null
            const events = eventsRes.ok ? await eventsRes.json() : []
            return [node.node_id, { latest, events }] as const
          } catch {
            return [node.node_id, { latest: null, events: [] }] as const
          }
        }),
      )

      setNodeState((prev) => {
        const next = { ...prev }
        for (const [nodeId, state] of entries) {
          next[nodeId] = state
        }
        return next
      })
    }

loadFailedNodeStates()
   }, [dag?.nodes, dagId, nodeState])

   // Load structured acceptance data for selected node
   useEffect(() => {
     const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
     if (!node || nodeAcceptance[node.node_id]) return

     const fetchAcceptance = async () => {
       try {
         const res = await fetch(`${API}/api/dags/${dagId}/nodes/${node.node_id}/acceptance`)
         if (res.ok) {
           const data: NodeAcceptanceResponse = await res.json()
           setNodeAcceptance(prev => ({ ...prev, [node.node_id]: data }))
         } else {
           setNodeAcceptance(prev => ({ ...prev, [node.node_id]: null }))
         }
       } catch {
         setNodeAcceptance(prev => ({ ...prev, [node.node_id]: null }))
       }
     }
     fetchAcceptance()
   }, [selectedNodeId, dag?.nodes, dagId, nodeAcceptance])

   // Load workspace manifest when workspace tab is active
   useEffect(() => {
     if (!dag || activeTab !== 'workspace' || workspaceManifest) return

     const fetchWorkspaceManifest = async () => {
       try {
         const res = await fetch(`${API}/api/dags/${dagId}/workspace/manifest`)
         if (res.ok) {
           const data: WorkspaceManifestResponse = await res.json()
           setWorkspaceManifest(data)
         }
       } catch {
         setWorkspaceManifest(null)
       }
     }
     fetchWorkspaceManifest()
   }, [dag, dagId, activeTab, workspaceManifest])

   const startDag = async () => {
    try {
      await fetch(`${API}/api/dags/${dagId}/start`, { method: 'POST' })
      fetchDag()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const cancelDag = async () => {
    try {
      await fetch(`${API}/api/dags/${dagId}/cancel`, { method: 'POST' })
      fetchDag()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const reviseDag = async () => {
    if (!reviseComments.trim()) return
    setRevising(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comments: reviseComments }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${res.status}`)
      }
      const newDag = await res.json()
      setShowRevise(false)
      setReviseComments('')
      // Redirect to the newly created revision DAG
      router.push(`/dags/${newDag.id}`)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setRevising(false)
    }
  }

  const resetSelectedNodeCaches = useCallback((node: DAGNode | null) => {
    if (!node) return

    if (node.task_id) {
      setNodeTaskData(prev => {
        const next = { ...prev }
        delete next[node.task_id as string]
        return next
      })
    }

setNodeState(prev => {
       const next = { ...prev }
       delete next[node.node_id]
       return next
     })

     setNodeAcceptance(prev => {
       const next = { ...prev }
       delete next[node.node_id]
       return next
     })
   }, [])

  const applyDagMutationResult = useCallback(async (updatedDag?: DAGDetail | null, clearSelection?: boolean) => {
    if (updatedDag?.id) {
      setDag(updatedDag)
    } else {
      await fetchDag()
    }
    if (clearSelection) {
      setSelectedNodeId(null)
    }
  }, [fetchDag])

  const deleteSelectedNode = async () => {
    if (!dag || !selectedNode) return
    if (!(dag.status === 'failed' || dag.status === 'ready')) return

    const confirmed = window.confirm(`Delete step '${selectedNode.node_id}'? Dependencies will be rewired automatically.`)
    if (!confirmed) return

    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}`, {
        method: 'DELETE',
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error((data as any).detail || `HTTP ${res.status}`)
      }
      resetSelectedNodeCaches(selectedNode)
      setShowNodeActions(false)
      await applyDagMutationResult(data as DAGDetail, true)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const enhanceSelectedNode = async () => {
    if (!dag || !selectedNode) return
    if (!(dag.status === 'failed' || dag.status === 'ready')) return

    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}/enhance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: enhanceMode,
          guidance: enhanceGuidance,
          split_count: enhanceMode === 'split' ? enhanceSplitCount : undefined,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error((data as any).detail || `HTTP ${res.status}`)
      }
      resetSelectedNodeCaches(selectedNode)
      setShowEnhanceDialog(false)
      setShowNodeActions(false)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const retryFromSelectedNode = async () => {
    if (!dag || !selectedNode) return
    if (dag.status !== 'failed') return

    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/retry-from/${selectedNode.node_id}`, {
        method: 'POST',
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error((data as any).detail || `HTTP ${res.status}`)
      }
      setShowNodeActions(false)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const getWorkflowLink = (workflowId: string) =>
    `${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(workflowId)}`

  const getNodeWorkflowLink = (nodeId: string) =>
    getWorkflowLink(`dag-node-${dagId}-${nodeId}`)

  const selectedNode = dag?.nodes.find(n => n.node_id === selectedNodeId) || null
  const selectedTaskData = selectedNode?.task_id ? nodeTaskData[selectedNode.task_id] : null
const selectedNodeState = selectedNode ? nodeState[selectedNode.node_id] : null
   const selectedNodeAcceptance = selectedNode ? nodeAcceptance[selectedNode.node_id] : null
   const selectedFailureReason = selectedNode ? summarizeNodeFailureReason(selectedNode, selectedNodeState || undefined) : null
  const nodeActionsAllowed = !!(selectedNode && (dag?.status === 'failed' || dag?.status === 'ready'))

  if (error) return <div className="text-red-400 p-8">Error: {error}</div>
  if (!dag) return <div className="text-gray-500 p-8">Loading...</div>

  const completedNodes = dag.nodes.filter(n => n.status === 'completed').length
  const runningNodes = dag.nodes.filter(n => n.status === 'running').length
  const failedNodes = dag.nodes.filter(n => n.status === 'failed').length
  const pendingApproval = dag.nodes.filter(n => n.status === 'pending_approval').length
  const failedNodeSummaries = dag.nodes
    .filter(n => n.status === 'failed')
    .map(n => ({
      nodeId: n.node_id,
      reason: summarizeNodeFailureReason(n, nodeState[n.node_id]),
    }))
    .filter(entry => entry.reason)

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-1">
            <Link href="/dags" className="text-gray-500 hover:text-gray-300 text-sm">&larr; DAGs</Link>
            <span className="text-gray-600">/</span>
            <h1 className="text-xl font-bold font-mono">{dag.id}</h1>
            <StatusBadge status={dag.status} />
          </div>
          <p className="text-gray-400 text-sm mt-1 max-w-3xl">{dag.objective}</p>
        </div>
        <div className="flex gap-2">
          {dag.status === 'ready' && (
            <button onClick={startDag} className="btn-success text-sm">&#9654; Start</button>
          )}
          {dag.status === 'running' && (
            <button onClick={cancelDag} className="btn-danger text-sm">&#9209; Cancel</button>
          )}
          {(dag.status === 'completed' || dag.status === 'failed' || dag.status === 'cancelled') && (
            <button
              onClick={() => setShowRevise(!showRevise)}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-md transition-colors"
            >
              {showRevise ? 'Cancel' : '♻️ Revise'}
            </button>
          )}
        </div>
      </div>

      {/* Revise panel */}
      {showRevise && (
        <div className="card p-4 mb-4 border border-indigo-500/30">
          <label className="block text-sm font-medium text-gray-300 mb-2">Review Comments</label>
          <textarea
            value={reviseComments}
            onChange={e => setReviseComments(e.target.value)}
            rows={4}
            className="w-full bg-gray-800 border border-gray-600 rounded-md p-3 text-sm text-gray-200 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none resize-y"
            placeholder="Describe what needs to be changed or fixed..."
          />
          <div className="flex justify-end mt-3">
            <button
              onClick={reviseDag}
              disabled={revising || !reviseComments.trim()}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded-md transition-colors"
            >
              {revising ? 'Starting...' : '🚀 Revise DAG'}
            </button>
          </div>
        </div>
      )}

      {/* Stats bar */}
      <div className="grid grid-cols-6 gap-3 mb-4">
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Model</div>
          <div className="text-xs font-mono text-gray-300 truncate">{dag.llm_model}</div>
        </div>
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Nodes</div>
          <div className="text-sm font-bold text-white">{dag.nodes.length}</div>
        </div>
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Completed</div>
          <div className="text-sm font-bold text-emerald-400">{completedNodes}</div>
        </div>
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Running</div>
          <div className="text-sm font-bold text-blue-400">{runningNodes}</div>
        </div>
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Failed</div>
          <div className="text-sm font-bold text-red-400">{failedNodes}</div>
        </div>
        <div className="card py-2 px-3 text-center">
          <div className="text-[10px] text-gray-500 uppercase">Approval</div>
          <div className={`text-sm font-bold ${pendingApproval > 0 ? 'text-amber-400' : 'text-gray-500'}`}>{pendingApproval}</div>
        </div>
      </div>

      {failedNodeSummaries.length > 0 && (
        <div className="mb-4 rounded border border-red-700/50 bg-red-950/40 p-4">
          <div className="text-sm font-semibold text-red-200 mb-2">DAG Failure Summary</div>
          <div className="space-y-2">
            {failedNodeSummaries.map(({ nodeId, reason }) => (
              <button
                key={nodeId}
                onClick={() => { setSelectedNodeId(nodeId); setActiveTab('overview') }}
                className="block w-full text-left rounded border border-red-800/40 bg-black/10 px-3 py-2 hover:border-red-500/60 transition-colors"
              >
                <div className="text-xs font-mono text-red-300 mb-1">{nodeId}</div>
                <div className="text-sm text-red-100">{reason}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Workflow link */}
      {dag.workflow_id && (
        <div className="mb-4 text-xs text-gray-500">
          Workflow:{' '}
          <a href={getWorkflowLink(dag.workflow_id)} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300 font-mono">
            {dag.workflow_id}
          </a>
        </div>
      )}

      {/* DAG Graph */}
      <div className="mb-4">
        <DAGGraph
          nodes={dag.nodes.map(n => ({
            node_id: n.node_id,
            description: n.description,
            status: n.status,
            depends_on: n.depends_on,
            task_id: n.task_id,
            skill_id: n.skill_id,
            started_at: n.started_at,
            completed_at: n.completed_at,
          }))}
          onNodeClick={(nodeId) => {
            setSelectedNodeId(nodeId === selectedNodeId ? null : nodeId)
            setActiveTab('overview')
          }}
        />
      </div>

      {/* Selected Node Detail Panel */}
      {selectedNode && (
        <div className="card border-blue-500/30 mb-4">
          {/* Node header */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm font-bold text-white">{selectedNode.node_id}</span>
              <StatusBadge status={selectedNode.status} />
              {selectedNode.skill_id && (
                <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">
                  skill: {selectedNode.skill_id}
                </span>
              )}
              {selectedNode.selected_skill_v2_id && (
                <span
                  className="text-xs bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded cursor-help"
                  title={selectedNode.skill_selection_reason ?? 'v2 skill selected'}
                >
                  v2: {selectedNode.selected_skill_v2_id}
                </span>
              )}
              {selectedNode.config?.base_image && (
                <span className="text-xs bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded">
                  image: {selectedNode.config.base_image}
                </span>
              )}
              {selectedNode.config?.dag_image && (
                <span
                  className="text-xs bg-indigo-500/20 text-indigo-300 px-2 py-0.5 rounded cursor-help"
                  title={`Inherited built image: ${selectedNode.config.dag_image}`}
                >
                  custom build: {selectedNode.config.dag_image.split(':').pop() || selectedNode.config.dag_image}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 text-xs">
              {nodeActionsAllowed && (
                <div className="relative">
                  <button
                    onClick={() => setShowNodeActions(v => !v)}
                    disabled={nodeActionLoading}
                    className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50"
                    title="Step actions"
                  >
                    Actions ▾
                  </button>
                  {showNodeActions && (
                    <div className="absolute right-0 mt-1 w-56 rounded border border-gray-700 bg-gray-900 shadow-xl z-50">
                      <button
                        onClick={() => {
                          setShowNodeActions(false)
                          setShowEnhanceDialog(true)
                        }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ✨ Enhance Step
                      </button>
                      <button
                        onClick={deleteSelectedNode}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-red-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🗑 Delete Step
                      </button>
                      {dag.status === 'failed' && (
                        <button
                          onClick={retryFromSelectedNode}
                          disabled={nodeActionLoading}
                          className="w-full text-left px-3 py-2 text-xs text-emerald-300 hover:bg-gray-800 border-t border-gray-700 disabled:opacity-50"
                        >
                          ▶ Retry From This Step
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
              {selectedNode.task_id && (
                <Link href={`/tasks/${selectedNode.task_id}`} className="text-blue-400 hover:text-blue-300">
                  Full task view &rarr;
                </Link>
              )}
              <a href={getNodeWorkflowLink(selectedNode.node_id)} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">
                Temporal &#8599;
              </a>
              <button onClick={() => setSelectedNodeId(null)} className="text-gray-500 hover:text-gray-300">&times;</button>
            </div>
          </div>

          {selectedNode.status === 'failed' && selectedFailureReason && (
            <div className="mb-3 p-4 rounded border border-red-700/60 bg-red-950/50">
              <div className="text-[11px] uppercase tracking-wide text-red-300 mb-1">Failure Reason</div>
              <div className="text-base font-medium text-red-100">{selectedFailureReason}</div>
            </div>
          )}

          {selectedNode.description && (
            <p className="text-sm text-gray-400 mb-3">{selectedNode.description}</p>
          )}

          {selectedNode.skill_selection_reason && (
            <div className="mb-3 p-2 bg-blue-950/40 border border-blue-800/50 rounded text-xs text-blue-300">
              <span className="font-semibold text-blue-400">Skill rationale: </span>
              {selectedNode.skill_selection_reason}
            </div>
          )}

          {showEnhanceDialog && (
            <div className="mb-3 rounded border border-indigo-700/40 bg-indigo-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-indigo-300 mb-2">Enhance This Step</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                <button
                  onClick={() => setEnhanceMode('rewrite')}
                  className={`text-xs px-2 py-1 rounded border ${enhanceMode === 'rewrite' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                >
                  Rewrite Same Step
                </button>
                <button
                  onClick={() => setEnhanceMode('split')}
                  className={`text-xs px-2 py-1 rounded border ${enhanceMode === 'split' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                >
                  Split Into Sub-Steps
                </button>
                {enhanceMode === 'split' && (
                  <select
                    value={enhanceSplitCount}
                    onChange={(e) => setEnhanceSplitCount(Number(e.target.value))}
                    className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200"
                  >
                    <option value={2}>2 steps</option>
                    <option value={3}>3 steps</option>
                    <option value={4}>4 steps</option>
                  </select>
                )}
              </div>
              <textarea
                value={enhanceGuidance}
                onChange={(e) => setEnhanceGuidance(e.target.value)}
                rows={3}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                placeholder="Optional guidance (e.g. required deliverables, constraints, acceptance criteria)."
              />
              <div className="flex justify-end gap-2 mt-2">
                <button
                  onClick={() => setShowEnhanceDialog(false)}
                  className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  onClick={enhanceSelectedNode}
                  disabled={nodeActionLoading}
                  className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50"
                >
                  {nodeActionLoading ? 'Applying...' : 'Apply Enhancement'}
                </button>
              </div>
            </div>
          )}

          {selectedNode.depends_on.length > 0 && (
            <div className="text-xs text-gray-500 mb-3">
              Dependencies:{' '}
              {selectedNode.depends_on.map(dep => (
                <button
                  key={dep}
                  onClick={() => { setSelectedNodeId(dep); setActiveTab('overview') }}
                  className="font-mono text-blue-400 hover:text-blue-300 mx-1"
                >
                  {dep}
                </button>
              ))}
            </div>
          )}

          {/* Timing info */}
          {(selectedNode.started_at || selectedNode.completed_at) && (
            <div className="flex gap-4 text-xs text-gray-500 mb-3">
              {selectedNode.started_at && <span>Started: {new Date(selectedNode.started_at).toLocaleString()}</span>}
              {selectedNode.completed_at && <span>Completed: {new Date(selectedNode.completed_at).toLocaleString()}</span>}
              {selectedNode.started_at && selectedNode.completed_at && (
                <span className="text-gray-400">
                  Duration: {((new Date(selectedNode.completed_at).getTime() - new Date(selectedNode.started_at).getTime()) / 1000).toFixed(0)}s
                </span>
              )}
            </div>
          )}

          {/* Execution state continuity panel */}
          <div className="mb-3 rounded border border-cyan-700/40 bg-cyan-950/20 p-3">
            <div className="text-xs uppercase tracking-wide text-cyan-300 mb-2">Execution State</div>
            {!selectedNodeState ? (
              <div className="text-xs text-gray-500">Loading node state...</div>
            ) : !selectedNodeState.latest ? (
              <div className="text-xs text-gray-500">No captured state snapshot yet.</div>
            ) : (
              <div className="space-y-2 text-xs">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  <div className="bg-gray-900/40 rounded p-2">
                    <div className="text-gray-500">Phase</div>
                    <div className="font-mono text-cyan-300">{selectedNodeState.latest.phase}</div>
                  </div>
                  <div className="bg-gray-900/40 rounded p-2">
                    <div className="text-gray-500">Status</div>
                    <div className="font-mono text-white">{selectedNodeState.latest.status}</div>
                  </div>
                  <div className="bg-gray-900/40 rounded p-2">
                    <div className="text-gray-500">Wave</div>
                    <div className="font-mono text-white">{selectedNodeState.latest.wave ?? '-'}</div>
                  </div>
                  <div className="bg-gray-900/40 rounded p-2">
                    <div className="text-gray-500">Acceptance</div>
                    <div className={`font-mono ${selectedNodeState.latest.acceptance_result?.valid === false ? 'text-red-300' : 'text-emerald-300'}`}>
                      {selectedNodeState.latest.acceptance_result?.valid === false ? 'failed' : 'passed/na'}
                    </div>
                  </div>
                </div>

                <details className="bg-gray-900/30 rounded p-2">
                  <summary className="cursor-pointer text-cyan-200">1) Inputs brought into this step</summary>
                  <pre className="mt-2 text-[11px] text-gray-300 overflow-auto max-h-40">{JSON.stringify(selectedNodeState.latest.input_context, null, 2)}</pre>
                </details>

                <details className="bg-gray-900/30 rounded p-2">
                  <summary className="cursor-pointer text-cyan-200">2) Output of this step</summary>
                  <pre className="mt-2 text-[11px] text-gray-300 overflow-auto max-h-40">{JSON.stringify(selectedNodeState.latest.output_context, null, 2)}</pre>
                </details>

                <details className="bg-gray-900/30 rounded p-2">
                  <summary className="cursor-pointer text-cyan-200">3) How output was obtained</summary>
                  <pre className="mt-2 text-[11px] text-gray-300 overflow-auto max-h-40">{JSON.stringify(selectedNodeState.latest.acquisition_log, null, 2)}</pre>
                </details>

<details className="bg-gray-900/30 rounded p-2">
                   <summary className="cursor-pointer text-cyan-200">4) Acceptance criteria check</summary>
                   {selectedNodeAcceptance ? (
                     <div className="mt-2 text-[11px] text-gray-300 space-y-1">
                       <div className="flex items-center gap-2">
                         <span className="font-semibold">Verdict:</span>
                         <span className={selectedNodeAcceptance.acceptance_verdict === 'fail' ? 'text-red-300' : selectedNodeAcceptance.acceptance_verdict === 'partial' ? 'text-amber-300' : 'text-emerald-300'}>
                           {selectedNodeAcceptance.acceptance_verdict || 'pending'}
                         </span>
                         <span className="ml-auto font-mono">Score: {selectedNodeAcceptance.acceptance_score}</span>
                       </div>
                       {selectedNodeAcceptance.success_criteria?.length > 0 && (
                         <div>
                           <span className="font-semibold">Success Criteria:</span>
                           <ul className="ml-4 mt-1 list-disc">
                             {selectedNodeAcceptance.success_criteria.map((c, i) => (
                               <li key={i} className={selectedNodeAcceptance.criteria_met?.[c] ? 'text-emerald-300' : 'text-gray-400'}>
                                 {c}
                               </li>
                             ))}
                           </ul>
                         </div>
                       )}
                       {selectedNodeAcceptance.skill_id && (
                         <div>
                           <span className="font-semibold">Skill:</span> {selectedNodeAcceptance.skill_id}
                           {selectedNodeAcceptance.skill_followed !== null && (
                             <span className={selectedNodeAcceptance.skill_followed ? 'text-emerald-300 ml-2' : 'text-red-300 ml-2'}>
                               {selectedNodeAcceptance.skill_followed ? 'followed' : 'not followed'}
                             </span>
                           )}
                         </div>
                       )}
                       {selectedNodeAcceptance.deliverables_keys?.length > 0 && (
                         <div>
                           <span className="font-semibold">Deliverables:</span>
                           <div className="flex flex-wrap gap-1 mt-1">
                             {selectedNodeAcceptance.deliverables_keys.map(f => (
                               <span key={f} className="font-mono bg-emerald-900/30 text-emerald-400 px-1.5 py-0.5 rounded text-[10px]">
                                 {f.split('/').pop() || f}
                               </span>
                             ))}
                           </div>
                         </div>
                       )}
                     </div>
                   ) : (
                     <pre className="mt-2 text-[11px] text-gray-300 overflow-auto max-h-40">{JSON.stringify(selectedNodeState.latest.acceptance_result, null, 2)}</pre>
                   )}
                 </details>

                {selectedNodeState.events.length > 0 && (
                  <details className="bg-gray-900/30 rounded p-2">
                    <summary className="cursor-pointer text-cyan-200">Recent audit events ({selectedNodeState.events.length})</summary>
                    <div className="mt-2 space-y-1 max-h-40 overflow-auto">
                      {selectedNodeState.events.map(ev => (
                        <div key={ev.id} className="text-[11px] text-gray-300 border-b border-gray-800 pb-1">
                          <span className="font-mono text-gray-500 mr-2">{new Date(ev.created_at).toLocaleTimeString()}</span>
                          <span className={`mr-2 ${ev.severity === 'critical' ? 'text-red-300' : ev.severity === 'warning' ? 'text-amber-300' : 'text-cyan-300'}`}>
                            {ev.severity}
                          </span>
                          <span className="text-cyan-200 mr-2">{ev.event_type}</span>
                          <span>{ev.message}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
          </div>

          {/* Task data tabs */}
          {selectedNode.task_id && (
            <>
              <div className="flex border-b border-gray-700 mb-3 gap-1">
{(['overview', 'outputs', 'audit', 'sbom'] as const).map(tab => (
                    <button
                      key={tab}
                      onClick={() => setActiveTab(tab)}
                      className={`px-3 py-1.5 text-xs font-medium capitalize rounded-t transition-colors ${
                        activeTab === tab
                          ? 'bg-gray-700 text-white border-b-2 border-blue-500'
                          : 'text-gray-500 hover:text-gray-300'
                      }`}
                    >
                      {tab === 'overview' ? 'Overview' :
                       tab === 'outputs' ? `Outputs${selectedTaskData ? ` (${selectedTaskData.outputs.length})` : ''}` :
                       tab === 'audit' ? 'Audit' :
                       'SBOM'}
                    </button>
                  ))}
              </div>

              {nodeDataLoading && !selectedTaskData && (
                <div className="text-gray-500 text-sm py-4 text-center animate-pulse">Loading task data...</div>
              )}

              {selectedTaskData && (
                <>
                  {/* Overview tab */}
                  {activeTab === 'overview' && (
                    <div>
                      {selectedTaskData.currentState && (
                        <div className="grid grid-cols-4 gap-2 mb-3">
                          <div className="bg-gray-900/50 rounded p-2 text-center">
                            <div className="text-[10px] text-gray-600">Status</div>
                            <div className="text-xs font-bold">{selectedTaskData.currentState.status}</div>
                          </div>
                          <div className="bg-gray-900/50 rounded p-2 text-center">
                            <div className="text-[10px] text-gray-600">Iterations</div>
                            <div className="text-xs font-bold text-blue-400">{selectedTaskData.outputs.length}</div>
                          </div>
                          <div className="bg-gray-900/50 rounded p-2 text-center">
                            <div className="text-[10px] text-gray-600">Image Version</div>
                            <div className="text-xs font-bold text-purple-400">v{selectedTaskData.currentState.current_image_version}</div>
                          </div>
                          <div className="bg-gray-900/50 rounded p-2 text-center">
                            <div className="text-[10px] text-gray-600">Pending Approvals</div>
                            <div className={`text-xs font-bold ${selectedTaskData.currentState.pending_approvals > 0 ? 'text-amber-400' : 'text-gray-500'}`}>
                              {selectedTaskData.currentState.pending_approvals}
                            </div>
                          </div>
                        </div>
                      )}

                      {/* Capability requests */}
                      {selectedTaskData.timeline?.capability_requests?.length > 0 && (
                        <div className="mt-3">
                          <div className="text-xs text-gray-500 uppercase font-medium mb-2">Capability Requests</div>
                          <div className="space-y-1.5">
                            {selectedTaskData.timeline.capability_requests.map((req: any) => (
                              <div key={req.id} className={`flex items-center justify-between bg-gray-900/50 rounded px-3 py-2 text-xs ${
                                req.status === 'pending' ? 'border border-amber-500/30' : ''
                              }`}>
                                <div className="flex items-center gap-2">
                                  <span className="font-mono text-gray-300">{req.type}: {req.resource}</span>
                                  {req.status === 'pending' && (
                                    <Link href="/approvals" className="text-amber-400 hover:text-amber-300">Review &rarr;</Link>
                                  )}
                                </div>
                                <span className={`px-1.5 py-0.5 rounded ${
                                  req.status === 'approved' ? 'bg-emerald-900/50 text-emerald-300' :
                                  req.status === 'denied' ? 'bg-red-900/50 text-red-300' :
                                  'bg-amber-900/50 text-amber-300'
                                }`}>
                                  {req.status}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Timeline events */}
                      {selectedTaskData.timeline?.timeline?.length > 0 && (
                        <div className="mt-3">
                          <div className="text-xs text-gray-500 uppercase font-medium mb-2">Timeline</div>
                          <div className="space-y-1">
                            {selectedTaskData.timeline.timeline.map((ev: any, i: number) => (
                              <div key={i} className="flex items-center gap-2 text-xs text-gray-400">
                                <span className="text-gray-600 font-mono w-20 shrink-0">
                                  {new Date(ev.timestamp).toLocaleTimeString()}
                                </span>
                                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                                  ev.event.includes('completed') ? 'bg-emerald-400' :
                                  ev.event.includes('started') ? 'bg-blue-400' :
                                  ev.event.includes('capability') ? 'bg-amber-400' :
                                  'bg-gray-500'
                                }`} />
                                <span>{ev.description}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Outputs tab */}
                  {activeTab === 'outputs' && (
                    <div className="space-y-2 max-h-96 overflow-y-auto">
                      {selectedTaskData.outputs.length === 0 ? (
                        <div className="text-gray-500 text-sm text-center py-4">No outputs yet</div>
                      ) : (
                        selectedTaskData.outputs.map(o => {
                          const hasError = !!o.error
                          const isDone = o.completed === 'true'
                          const hasCap = o.capability_requested === 'true'
                          const deliverableCount = o.deliverables ? Object.keys(o.deliverables).length : 0
                          const preview = o.llm_response_preview || ''

                          return (
                            <div key={o.id} className={`rounded border p-3 text-xs ${
                              hasError ? 'border-red-500/30 bg-red-900/10' :
                              isDone ? 'border-emerald-500/30 bg-emerald-900/10' :
                              hasCap ? 'border-amber-500/30 bg-amber-900/10' :
                              'border-gray-700 bg-gray-900/30'
                            }`}>
                              <div className="flex items-center justify-between mb-1">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono font-bold text-gray-300">Iter {o.iteration}</span>
                                  {o.model_used && <span className="text-gray-500">{o.model_used}</span>}
                                  {o.duration_ms && <span className="text-gray-600">{(o.duration_ms / 1000).toFixed(1)}s</span>}
                                </div>
                                <span className={`px-1.5 py-0.5 rounded ${
                                  hasError ? 'bg-red-900/50 text-red-300' :
                                  isDone ? 'bg-emerald-900/50 text-emerald-300' :
                                  hasCap ? 'bg-amber-900/50 text-amber-300' :
                                  'bg-blue-900/50 text-blue-300'
                                }`}>
                                  {hasError ? 'Error' : isDone ? 'Done' : hasCap ? 'Capability' : 'Running'}
                                </span>
                              </div>

                              {deliverableCount > 0 && (
                                <div className="flex flex-wrap gap-1 mb-1">
                                  {Object.keys(o.deliverables!).map(f => (
                                    <span key={f} className="font-mono bg-emerald-900/30 text-emerald-400 px-1.5 py-0.5 rounded border border-emerald-500/20">
                                      {f}
                                    </span>
                                  ))}
                                </div>
                              )}

                              {hasError && (
                                <pre className="text-red-300 mt-1 whitespace-pre-wrap break-words max-h-24 overflow-y-auto">
                                  {o.error}
                                </pre>
                              )}

                              {preview && (
                                <div className="text-gray-400 mt-1 line-clamp-3">{preview.slice(0, 300)}</div>
                              )}
                            </div>
                          )
                        })
                      )}
                    </div>
                  )}

                  {/* Audit tab */}
                  {activeTab === 'audit' && (
                    <div className="max-h-96 overflow-y-auto">
                      {!selectedTaskData.auditTurns ? (
                        <div className="text-gray-500 text-sm text-center py-4 animate-pulse">Loading audit data...</div>
                      ) : selectedTaskData.auditTurns.total_iterations === 0 ? (
                        <div className="text-gray-500 text-sm text-center py-4">No audit data yet</div>
                      ) : (
                        <div className="space-y-3">
                          {/* Token summary */}
                          <div className="grid grid-cols-4 gap-2">
                            <div className="bg-gray-900/50 rounded p-2 text-center">
                              <div className="text-[10px] text-gray-600">LLM Calls</div>
                              <div className="text-xs font-bold text-white">{selectedTaskData.auditTurns.total_turns}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded p-2 text-center">
                              <div className="text-[10px] text-gray-600">Input Tokens</div>
                              <div className="text-xs font-bold text-indigo-400">{selectedTaskData.auditTurns.total_input_tokens?.toLocaleString()}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded p-2 text-center">
                              <div className="text-[10px] text-gray-600">Output Tokens</div>
                              <div className="text-xs font-bold text-emerald-400">{selectedTaskData.auditTurns.total_output_tokens?.toLocaleString()}</div>
                            </div>
                            <div className="bg-gray-900/50 rounded p-2 text-center">
                              <div className="text-[10px] text-gray-600">Iterations</div>
                              <div className="text-xs font-bold text-amber-400">{selectedTaskData.auditTurns.total_iterations}</div>
                            </div>
                          </div>

                          {/* Iteration details */}
                          {(selectedTaskData.auditTurns.iterations || []).map((iter: any) => {
                            const turns = iter.turns || []
                            return (
                              <details key={iter.workflow_id} className="rounded border border-gray-700 bg-gray-900/30">
                                <summary className="px-3 py-2 cursor-pointer text-xs flex items-center justify-between hover:bg-gray-800/50">
                                  <div className="flex items-center gap-2">
                                    <span className="font-bold text-white">Iteration {iter.iteration}</span>
                                    <span className="text-gray-500">{turns.length} turn{turns.length !== 1 ? 's' : ''}</span>
                                  </div>
                                  {iter.container?.image && (
                                    <span className="font-mono text-gray-600 truncate max-w-48">{iter.container.image}</span>
                                  )}
                                </summary>
                                <div className="border-t border-gray-700 px-3 py-2 space-y-2">
                                  {turns.map((turn: any, idx: number) => {
                                    const td = turn.data || {}
                                    const resp = td.response || {}
                                    const toolCalls = resp.tool_calls || []
                                    const usage = resp.usage || {}
                                    const req = td.request || {}
                                    return (
                                      <details key={idx} className="bg-gray-950 rounded text-[11px]">
                                        <summary className="p-2 cursor-pointer hover:bg-gray-900/50">
                                          <div className="flex items-center justify-between text-gray-500">
                                            <div className="flex items-center gap-2">
                                              <span className="font-bold text-indigo-400">Turn {turn.turn_number || idx + 1}</span>
                                              <span>{td.provider || 'unknown'}</span>
                                              <span className="text-gray-600">{resp.finish_reason || ''}</span>
                                            </div>
                                            <div className="flex gap-3">
                                              {usage.prompt_tokens && (
                                                <span className="text-indigo-400/70">{usage.prompt_tokens.toLocaleString()} in</span>
                                              )}
                                              {usage.completion_tokens && (
                                                <span className="text-emerald-400/70">{usage.completion_tokens.toLocaleString()} out</span>
                                              )}
                                              {td.timestamp && (
                                                <span className="text-gray-600">{new Date(td.timestamp).toLocaleTimeString()}</span>
                                              )}
                                            </div>
                                          </div>
                                          {toolCalls.length > 0 && (
                                            <div className="flex flex-wrap gap-1 mt-1.5">
                                              {toolCalls.map((tc: any, tci: number) => (
                                                <span key={tci} className="font-mono bg-indigo-500/15 text-indigo-400 px-1.5 py-0.5 rounded">
                                                  {tc.name}
                                                  {tc.arguments?.path && (
                                                    <span className="text-gray-500 ml-1">{tc.arguments.path.split('/').pop()}</span>
                                                  )}
                                                  {tc.arguments?.command && (
                                                    <span className="text-gray-500 ml-1">{String(tc.arguments.command).slice(0, 40)}</span>
                                                  )}
                                                </span>
                                              ))}
                                            </div>
                                          )}
                                        </summary>
                                        <div className="border-t border-gray-800 p-2 space-y-2">
                                          {/* Request info */}
                                          {req.msg_count && (
                                            <div className="text-gray-600">
                                              Request: {req.msg_count} messages ({(req.roles || []).join(', ')})
                                            </div>
                                          )}
                                          {/* Tool call details */}
                                          {toolCalls.map((tc: any, tci: number) => (
                                            <div key={tci} className="border border-gray-800 rounded p-2">
                                              <div className="flex items-center gap-2 mb-1">
                                                <span className="font-mono font-bold text-indigo-400">{tc.name}</span>
                                                {tc.arguments?.path && (
                                                  <span className="font-mono text-gray-400">{tc.arguments.path}</span>
                                                )}
                                              </div>
                                              {tc.arguments?.content && (
                                                <pre className="text-gray-400 mt-1 whitespace-pre-wrap break-words max-h-48 overflow-y-auto bg-gray-900 rounded p-1.5 text-[10px] leading-relaxed">
                                                  {typeof tc.arguments.content === 'string'
                                                    ? tc.arguments.content.replace(/\.\.\. \(\d+ chars\)$/, '')
                                                    : JSON.stringify(tc.arguments.content, null, 2)}
                                                </pre>
                                              )}
                                              {tc.arguments?.command && (
                                                <pre className="text-amber-300/80 mt-1 whitespace-pre-wrap break-words bg-gray-900 rounded p-1.5 text-[10px]">
                                                  $ {tc.arguments.command}
                                                </pre>
                                              )}
                                            </div>
                                          ))}
                                          {/* Text response (no tool calls) */}
                                          {resp.content && toolCalls.length === 0 && (
                                            <pre className="text-gray-400 whitespace-pre-wrap break-words max-h-48 overflow-y-auto bg-gray-900 rounded p-1.5 text-[10px] leading-relaxed">
                                              {resp.content}
                                            </pre>
                                          )}
                                        </div>
                                      </details>
                                    )
                                  })}
                                </div>
                              </details>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )}

                  {/* SBOM tab */}
                  {activeTab === 'sbom' && (
                    <div className="max-h-96 overflow-y-auto">
                      {selectedTaskData.sbom === undefined ? (
                        <div className="text-gray-500 text-sm text-center py-4 animate-pulse">Loading SBOM...</div>
                      ) : selectedTaskData.sbom === null ? (
                        <div className="text-gray-500 text-sm text-center py-4">No SBOM available for this node</div>
                      ) : (
                        <div>
                          <div className="flex items-center gap-3 mb-2 text-xs text-gray-500">
                            <span>Image v{selectedTaskData.sbom.image_version}</span>
                            <span>{selectedTaskData.sbom.packages.length} packages</span>
                            <span>{selectedTaskData.sbom.format}</span>
                          </div>
                          <table className="w-full text-xs">
                            <thead>
                              <tr className="text-gray-600 text-left">
                                <th className="pb-1">Package</th>
                                <th className="pb-1">Version</th>
                                <th className="pb-1">Type</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-800">
                              {selectedTaskData.sbom.packages.slice(0, 50).map((pkg: any, i: number) => (
                                <tr key={i} className="text-gray-400">
                                  <td className="py-1 font-mono">{pkg.name}</td>
                                  <td className="py-1 font-mono text-gray-500">{pkg.version}</td>
                                  <td className="py-1">
                                    <span className={`px-1 py-0.5 rounded ${
                                      pkg.type === 'pip' ? 'bg-blue-900/30 text-blue-400' :
                                      pkg.type === 'apt' ? 'bg-orange-900/30 text-orange-400' :
                                      'bg-gray-700 text-gray-400'
                                    }`}>{pkg.type}</span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
{selectedTaskData.sbom.packages.length > 50 && (
                             <div className="text-xs text-gray-600 mt-2">
                               Showing 50 of {selectedTaskData.sbom.packages.length}{' '}
                               {selectedNode.task_id && (
                                 <Link href={`/tasks/${selectedNode.task_id}`} className="text-blue-400">view all &rarr;</Link>
                               )}
                             </div>
                           )}
                         </div>
                       )}
                     </div>
                   )}
                 </>
               )}

               {!selectedNode.task_id && (
                 <div className="text-gray-500 text-sm py-3">
                   This node has not started yet &mdash; no task data available.
                 </div>
               )}
             </>
           )}

           {/* Workspace manifest (outside task_id condition) */}
           {selectedNode && activeTab === 'workspace' && (
             <div className="card border-blue-500/30 mb-4">
               <div className="p-3">
                 <div className="text-xs uppercase tracking-wide text-cyan-300 mb-2">Workspace Manifest</div>
                 {!workspaceManifest ? (
                   <div className="text-gray-500 text-sm py-4">No workspace data available</div>
                 ) : (
                   <div>
                     <div className="flex items-center gap-3 mb-3 text-xs text-gray-500">
                       <span className="font-mono">{workspaceManifest.workspace_id}</span>
                       <span>{workspaceManifest.total_files} files</span>
                       <span>{workspaceManifest.steps_with_deliverables.length} steps</span>
                     </div>
                     {workspaceManifest.steps_with_deliverables.length === 0 ? (
                       <div className="text-gray-500 text-sm">No deliverables captured</div>
                     ) : (
                       <div className="space-y-2 max-h-64 overflow-y-auto">
                         {workspaceManifest.steps_with_deliverables.map(nodeId => (
                           <div key={nodeId} className="border border-gray-700 rounded bg-gray-900/30 p-2">
                             <div className="font-mono text-xs text-blue-300 mb-1">{nodeId}</div>
                             <div className="flex flex-wrap gap-1">
                               {(workspaceManifest.step_manifest[nodeId] || []).map(f => (
                                 <span key={f} className="font-mono text-[10px] bg-emerald-900/30 text-emerald-400 px-1.5 py-0.5 rounded">
                                   {f.split('/').pop() || f}
                                 </span>
                               ))}
                             </div>
                           </div>
                         ))}
                       </div>
                     )}
                   </div>
                 )}
               </div>
             </div>
           )}
         </div>
       )}

       {/* Node list (compact, when no node selected) */}
      {!selectedNodeId && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">All Nodes ({dag.nodes.length})</h2>
          {dag.nodes.map((node) => (
            <button
              key={node.node_id}
              onClick={() => { setSelectedNodeId(node.node_id); setActiveTab('overview') }}
              className="card w-full text-left hover:border-blue-500/50 transition-colors"
            >
              {(() => {
                const nodeFailureReason = summarizeNodeFailureReason(node, nodeState[node.node_id])
                return (
                  <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-semibold">{node.node_id}</span>
                  <StatusBadge status={node.status} />
                  {node.skill_id && (
                    <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">
                      {node.skill_id}
                    </span>
                  )}
                  {node.selected_skill_v2_id && (
                    <span
                      className="text-xs bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded cursor-help"
                      title={node.skill_selection_reason ?? 'v2 skill selected'}
                    >
                      v2✓
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-500">
                  {node.task_id && <span className="font-mono">task: {node.task_id}</span>}
                </div>
              </div>
              {node.description && <p className="text-sm text-gray-400 mt-1">{node.description}</p>}
              {node.status === 'failed' && nodeFailureReason && (
                <p className="text-xs text-red-300 mt-1">
                  Failure: {nodeFailureReason}
                </p>
              )}
              {node.depends_on.length > 0 && (
                <div className="text-xs text-gray-500 mt-1">Depends on: {node.depends_on.join(', ')}</div>
              )}
                  </>
                )
              })()}
            </button>
          ))}
        </div>
      )}

      {/* Output data (collapsed) */}
      {selectedNode?.output_data && (
        <details className="mt-4">
          <summary className="text-xs text-gray-500 cursor-pointer">Node Output Data</summary>
          <pre className="text-xs bg-gray-900 rounded p-2 mt-1 overflow-auto max-h-40">
            {JSON.stringify(selectedNode.output_data, null, 2)}
          </pre>
        </details>
      )}

      {/* Raw DAG JSON */}
      {dag.dag_json && (
        <details className="mt-4">
          <summary className="text-xs text-gray-500 cursor-pointer">Raw DAG JSON</summary>
          <pre className="card text-xs overflow-auto max-h-96 mt-2">
            {JSON.stringify(dag.dag_json, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
