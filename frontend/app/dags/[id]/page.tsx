'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
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

export default function DAGDetailPage() {
  const params = useParams()
  const dagId = params.id as string
  const [dag, setDag] = useState<DAGDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'outputs' | 'audit' | 'sbom'>('overview')

  // Per-node task data cache
  const [nodeTaskData, setNodeTaskData] = useState<Record<string, {
    timeline: any
    outputs: TaskOutput[]
    currentState: any
    auditTurns: any
    sbom: SBOMData | null | undefined
  }>>({})
  const [nodeDataLoading, setNodeDataLoading] = useState(false)

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

  const getWorkflowLink = (workflowId: string) =>
    `${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(workflowId)}`

  const getNodeWorkflowLink = (nodeId: string) =>
    getWorkflowLink(`dag-node-${dagId}-${nodeId}`)

  const selectedNode = dag?.nodes.find(n => n.node_id === selectedNodeId) || null
  const selectedTaskData = selectedNode?.task_id ? nodeTaskData[selectedNode.task_id] : null

  if (error) return <div className="text-red-400 p-8">Error: {error}</div>
  if (!dag) return <div className="text-gray-500 p-8">Loading...</div>

  const completedNodes = dag.nodes.filter(n => n.status === 'completed').length
  const runningNodes = dag.nodes.filter(n => n.status === 'running').length
  const failedNodes = dag.nodes.filter(n => n.status === 'failed').length
  const pendingApproval = dag.nodes.filter(n => n.status === 'pending_approval').length

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
        </div>
      </div>

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
            </div>
            <div className="flex items-center gap-3 text-xs">
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

          {selectedNode.description && (
            <p className="text-sm text-gray-400 mb-3">{selectedNode.description}</p>
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
                                    return (
                                      <div key={idx} className="bg-gray-950 rounded p-2 text-[11px]">
                                        <div className="flex items-center justify-between text-gray-500 mb-1">
                                          <div className="flex items-center gap-2">
                                            <span className="font-bold text-indigo-400">Turn {turn.turn_number || idx + 1}</span>
                                            <span>{td.provider || 'unknown'}</span>
                                          </div>
                                          <div className="flex gap-2">
                                            {usage.total_tokens && <span>{usage.total_tokens.toLocaleString()} tokens</span>}
                                          </div>
                                        </div>
                                        {toolCalls.length > 0 && (
                                          <div className="flex flex-wrap gap-1 mt-1">
                                            {toolCalls.map((tc: any, tci: number) => (
                                              <span key={tci} className="font-mono bg-indigo-500/15 text-indigo-400 px-1.5 py-0.5 rounded">
                                                {tc.name}
                                              </span>
                                            ))}
                                          </div>
                                        )}
                                        {resp.content && toolCalls.length === 0 && (
                                          <div className="text-gray-400 mt-1 line-clamp-2">{resp.content.slice(0, 200)}</div>
                                        )}
                                      </div>
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
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-semibold">{node.node_id}</span>
                  <StatusBadge status={node.status} />
                  {node.skill_id && (
                    <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">
                      {node.skill_id}
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-500">
                  {node.task_id && <span className="font-mono">task: {node.task_id}</span>}
                </div>
              </div>
              {node.description && <p className="text-sm text-gray-400 mt-1">{node.description}</p>}
              {node.depends_on.length > 0 && (
                <div className="text-xs text-gray-500 mt-1">Depends on: {node.depends_on.join(', ')}</div>
              )}
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
