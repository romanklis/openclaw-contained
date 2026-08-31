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
  locked: boolean
  template_params: { key: string; label: string; type: string; default: string | null; description: string | null }[]
  template_source_dag_id: string | null
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
  const [showAddNodeDialog, setShowAddNodeDialog] = useState(false)
  const [addNodeDesc, setAddNodeDesc] = useState('')
  const [addNodeImage, setAddNodeImage] = useState('openclaw')
  const [addNodeMode, setAddNodeMode] = useState<'after' | 'parallel' | 'custom'>('after')
  const [addNodeDeps, setAddNodeDeps] = useState<string[]>([])
  const [addNodeType, setAddNodeType] = useState<'agent' | 'decision' | 'input'>('agent')
  const [addNodeQuestion, setAddNodeQuestion] = useState('')
  const [addNodeOptions, setAddNodeOptions] = useState('Approve,approve\nRework,rework')
  const [addNodePrompt, setAddNodePrompt] = useState('')
  const [addNodeFields, setAddNodeFields] = useState('measurement,Measurement,number')
  const [showImageDialog, setShowImageDialog] = useState(false)
  const [nodeImage, setNodeImage] = useState('')
  const [showEditConnectionsDialog, setShowEditConnectionsDialog] = useState(false)
  const [editConnInputs, setEditConnInputs] = useState<string[]>([])
  const [editConnOutputs, setEditConnOutputs] = useState<string[]>([])
  const [dagEdges, setDagEdges] = useState<any[]>([])
  const [newEdgeFrom, setNewEdgeFrom] = useState('')
  const [newEdgeTo, setNewEdgeTo] = useState('')
  const [newEdgeCondition, setNewEdgeCondition] = useState('on_success')
  const [newEdgeType, setNewEdgeType] = useState('rework')
  const [graphMode, setGraphMode] = useState<'relations' | 'rework'>('relations')
  const [showSkillDialog, setShowSkillDialog] = useState(false)
  const [showRenameDialog, setShowRenameDialog] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [availableSkills, setAvailableSkills] = useState<any[]>([])
  const [selectedSkillId, setSelectedSkillId] = useState<string>('')
  const [skillDialogLoading, setSkillDialogLoading] = useState(false)
  const [showLockDialog, setShowLockDialog] = useState(false)
  const [lockParams, setLockParams] = useState<{ key: string; label: string; type: string; default: string; description: string }[]>([])
  const [lockSaving, setLockSaving] = useState(false)
  const [lockDialogLoading, setLockDialogLoading] = useState(false)
  const [showExecuteDialog, setShowExecuteDialog] = useState(false)
  const [executeValues, setExecuteValues] = useState<Record<string, string>>({})
  const [executeObjective, setExecuteObjective] = useState('')
  const [executeAutoStart, setExecuteAutoStart] = useState(false)
  const [executeSaving, setExecuteSaving] = useState(false)

  // Skill extraction state
  const [miningSkill, setMiningSkill] = useState(false)
  const [mineResult, setMineResult] = useState<string | null>(null)
  const [analysisResult, setAnalysisResult] = useState<any>(null)
  const [deepReviewLoading, setDeepReviewLoading] = useState(false)
  const [deepReviews, setDeepReviews] = useState<Record<string, any>>({})
  const [includeSkillInReview, setIncludeSkillInReview] = useState(true)
  const [skillFormat, setSkillFormat] = useState('pseudo-code')
  const [correctSkillLoading, setCorrectSkillLoading] = useState(false)
  const [correctSkillResult, setCorrectSkillResult] = useState<any>(null)

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

  const loadDeepReviews = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/skill-learning/deep-review?dag_id=${encodeURIComponent(dagId)}`)
      if (!res.ok) return
      const list = await res.json()
      const map: Record<string, any> = {}
      for (const r of list || []) {
        if (r.node_id) map[r.node_id] = r
      }
      setDeepReviews(map)
    } catch {
      // non-fatal: reviews simply won't be prefilled
    }
  }, [dagId])

  useEffect(() => { fetchDag() }, [fetchDag])
  useEffect(() => { loadDeepReviews() }, [loadDeepReviews])

  // ── Pending interactive steps (decision / input) ──────────────────────
  const [userRequests, setUserRequests] = useState<any[]>([])
  const [userRequestAnswers, setUserRequestAnswers] = useState<Record<number, any>>({})
  const [decisionPending, setDecisionPending] = useState<Record<number, { choice: string; label: string } | null>>({})
  const [decisionJustification, setDecisionJustification] = useState<Record<number, string>>({})
  const [userRequestBusy, setUserRequestBusy] = useState<number | null>(null)

  const loadUserRequests = async () => {
    try {
      const r = await fetch(`${API}/api/dags/${dagId}/user-requests?status=pending`)
      if (r.ok) setUserRequests(await r.json())
    } catch { /* ignore */ }
  }
  useEffect(() => {
    loadUserRequests()
    const iv = setInterval(loadUserRequests, 5000)
    return () => clearInterval(iv)
  }, [dagId])

  const answerUserRequest = async (id: number, answer?: any) => {
    setUserRequestBusy(id)
    try {
      const r = await fetch(`${API}/api/dags/${dagId}/user-requests/${id}/answer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: answer || userRequestAnswers[id] || {}, answered_by: 'web-ui' }),
      })
      if (!r.ok) throw new Error(await r.text())
      await loadUserRequests()
      await fetchDag()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUserRequestBusy(null)
    }
  }

  // Auto-refresh while running, and fetch once more when status changes from running
  useEffect(() => {
    if (dag?.status === 'running') {
      const interval = setInterval(fetchDag, 4000)
      return () => clearInterval(interval)
    }
    // When status changes from running -> completed/failed, do one final fetch
    if (dag && dag.status !== 'running') {
      fetchDag()
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

  const addNodeAfterSelected = async () => {
    if (!dag || !selectedNode) return
    setNodeActionLoading(true)
    try {
      const newId = 'step-' + Math.random().toString(36).slice(2, 8)
      // "after" -> depends on selected node; "parallel" -> same deps as selected;
      // "custom" -> explicitly chosen predecessors.
      let deps: string[]
      if (addNodeMode === 'parallel') {
        deps = selectedNode.depends_on?.length ? selectedNode.depends_on : []
      } else if (addNodeMode === 'custom') {
        deps = addNodeDeps
      } else {
        deps = [selectedNode.node_id]
      }
      const config: any = {
        base_image: addNodeImage.trim() || 'openclaw',
        llm_model: dag.llm_model || undefined,
      }
      if (addNodeType === 'decision') {
        config.type = 'decision'
        config.question = addNodeQuestion.trim() || 'Proceed?'
        config.payload = {
          options: addNodeOptions.split('\n').filter(Boolean).map((line) => {
            const [label, value] = line.split(',').map((s) => s.trim())
            return { label: label || value, value: value || label }
          }),
        }
      } else if (addNodeType === 'input') {
        config.type = 'input'
        config.prompt = addNodePrompt.trim() || 'Provide the requested input'
        config.payload = {
          fields: addNodeFields.split('\n').filter(Boolean).map((line) => {
            const [key, label, type] = line.split(',').map((s) => s.trim())
            return { key: key || label, label: label || key, type: type || 'text' }
          }),
        }
      }
      const res = await fetch(`${API}/api/dags/${dagId}/nodes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: newId,
          description: addNodeDesc.trim() || 'New step',
          depends_on: deps,
          node_type: addNodeType,
          config,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      setShowAddNodeDialog(false)
      setAddNodeDesc('')
      setAddNodeDeps([])
      setShowNodeActions(false)
      await applyDagMutationResult(data as DAGDetail, true)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const openEditConnectionsDialog = () => {
    if (!selectedNode || !dag) return
    setEditConnInputs(selectedNode.depends_on || [])
    setEditConnOutputs((dag.nodes || []).filter(n => (n.depends_on || []).includes(selectedNode.node_id)).map(n => n.node_id))
    setDagEdges(Array.isArray((dag as any).edges) ? (dag as any).edges : [])
    setNewEdgeFrom('')
    setNewEdgeTo('')
    setNewEdgeCondition('on_success')
    setNewEdgeType('rework')
    setShowEditConnectionsDialog(true)
    setShowNodeActions(false)
  }

  const openRenameDialog = () => {
    if (!selectedNode) return
    setRenameValue(selectedNode.node_id)
    setShowRenameDialog(true)
    setShowNodeActions(false)
  }

  const saveRename = async () => {
    if (!dag || !selectedNode) return
    const newId = renameValue.trim()
    if (!newId || newId === selectedNode.node_id) { setShowRenameDialog(false); return }
    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${encodeURIComponent(selectedNode.node_id)}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: newId }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail ? (typeof (data as any).detail === 'string' ? (data as any).detail : JSON.stringify((data as any).detail)) : `HTTP ${res.status}`)
      setShowRenameDialog(false)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const openChangeSkillDialog = async () => {    if (!selectedNode || !dag) return
    setShowNodeActions(false)
    setSelectedSkillId(selectedNode.selected_skill_v2_id || '')
    setSkillDialogLoading(true)
    setShowSkillDialog(true)
    try {
      const img = selectedNode.config?.base_image || 'openclaw'
      const res = await fetch(`${API}/api/skill-learning/skills?image_id=${encodeURIComponent(img)}&limit=200&exclude_archived=true`)
      if (res.ok) setAvailableSkills(await res.json())
    } catch {
      setAvailableSkills([])
    } finally {
      setSkillDialogLoading(false)
    }
  }

  const saveSkillAssignment = async () => {
    if (!dag || !selectedNode) return
    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selected_skill_v2_id: selectedSkillId || null,
          skill_selection_reason: selectedSkillId ? 'Manually assigned in DAG editor' : '',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      setShowSkillDialog(false)
      setShowNodeActions(false)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const openLockDialog = async () => {
    const existing = dag?.template_params || []
    setLockParams(existing.length > 0
      ? existing.map(p => ({ key: p.key, label: p.label, type: p.type || 'string', default: p.default || '', description: p.description || '' }))
      : [])
    setShowLockDialog(true)
    if (existing.length === 0) {
      setLockDialogLoading(true)
      try {
        const res = await fetch(`${API}/api/dags/${dagId}/propose-parameters`, { method: 'POST' })
        if (res.ok) {
          const proposed = await res.json()
          if (Array.isArray(proposed) && proposed.length > 0) {
            setLockParams(proposed.map((p: any) => ({ key: p.key, label: p.label || p.key, type: p.type || 'string', default: p.default || '', description: p.description || '' })))
          }
        }
      } catch {
        // leave empty rows; user can add manually
      } finally {
        setLockDialogLoading(false)
      }
    }
  }

  const addLockParam = () => {
    setLockParams(prev => [...prev, { key: '', label: '', type: 'string', default: '', description: '' }])
  }

  const updateLockParam = (idx: number, field: string, value: string) => {
    setLockParams(prev => prev.map((p, i) => i === idx ? { ...p, [field]: value } : p))
  }

  const removeLockParam = (idx: number) => {
    setLockParams(prev => prev.filter((_, i) => i !== idx))
  }

  const saveLock = async () => {
    if (!dag) return
    setLockSaving(true)
    try {
      const params = lockParams.filter(p => p.key.trim()).map(p => ({ key: p.key.trim(), label: p.label.trim() || p.key.trim(), type: p.type || 'string', default: p.default || null, description: p.description || null }))
      const res = await fetch(`${API}/api/dags/${dagId}/lock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parameters: params }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      setShowLockDialog(false)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLockSaving(false)
    }
  }

  const unlockDag = async () => {
    if (!dag) return
    setLockSaving(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/unlock`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      await applyDagMutationResult(data as DAGDetail, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLockSaving(false)
    }
  }

  const openExecuteDialog = () => {
    const vals: Record<string, string> = {}
    for (const p of dag?.template_params || []) {
      vals[p.key] = p.default ?? ''
    }
    setExecuteValues(vals)
    setExecuteObjective(dag?.objective || '')
    setExecuteAutoStart(false)
    setShowExecuteDialog(true)
  }

  const runExecute = async () => {
    if (!dag) return
    setExecuteSaving(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/instantiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective: executeObjective, parameters: executeValues, auto_start: executeAutoStart }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      setShowExecuteDialog(false)
      router.push(`/dags/${(data as any).id}`)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setExecuteSaving(false)
    }
  }

  const applyGraphPatch = async (nodeDependencies: Record<string, string[]>, edges?: any[]): Promise<DAGDetail> => {
    const body: any = { node_dependencies: nodeDependencies }
    if (edges) body.edges = edges
    const res = await fetch(`${API}/api/dags/${dagId}/graph`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error((data as any).detail ? (typeof (data as any).detail === 'string' ? (data as any).detail : JSON.stringify((data as any).detail)) : `HTTP ${res.status}`)
    return data as DAGDetail
  }

  const saveEditConnections = async () => {
    if (!dag || !selectedNode) return
    setNodeActionLoading(true)
    try {
      const nodeDependencies: Record<string, string[]> = {}
      nodeDependencies[selectedNode.node_id] = editConnInputs
      for (const n of dag.nodes) {
        if (n.node_id === selectedNode.node_id) continue
        const hasEdge = (n.depends_on || []).includes(selectedNode.node_id)
        const wantEdge = editConnOutputs.includes(n.node_id)
        if (hasEdge !== wantEdge) {
          const deps = (n.depends_on || []).filter(d => d !== selectedNode.node_id)
          if (wantEdge) deps.push(selectedNode.node_id)
          nodeDependencies[n.node_id] = deps
        }
      }
      let edgesToSave = dagEdges
      // Forgiving UX: if the add-edge selects are filled, include that draft
      // edge even if "+ Add edge" was not clicked before Save.
      if (newEdgeFrom && newEdgeTo) {
        edgesToSave = [...dagEdges, { from_node: newEdgeFrom, to_node: newEdgeTo, condition: newEdgeCondition, edge_type: newEdgeType }]
      }
      const updated = await applyGraphPatch(nodeDependencies, edgesToSave)
      setShowEditConnectionsDialog(false)
      setShowNodeActions(false)
      await applyDagMutationResult(updated, false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const handleGraphConnect = async (source: string, target: string) => {
    if (!dag) return
    if (source === target) return

    if (graphMode === 'rework') {
      // Rework mode: add an explicit loop-back edge (NOT a depends_on — a loop
      // must not add the source to the target's dependencies).
      const edges = ((dag as any).edges || []).filter(
        (e: any) => !((e.from_node || e.from) === source && (e.to_node || e.to) === target)
      )
      edges.push({ from_node: source, to_node: target, condition: 'on_success', edge_type: 'loop' })
      setNodeActionLoading(true)
      try {
        const updated = await applyGraphPatch({}, edges)
        await applyDagMutationResult(updated, false)
      } catch (err: any) {
        setError(err.message)
        await applyDagMutationResult(null, false)
      } finally {
        setNodeActionLoading(false)
      }
      return
    }

    const targetNode = dag.nodes.find(n => n.node_id === target)
    if (!targetNode || (targetNode.depends_on || []).includes(source)) return
    setNodeActionLoading(true)
    try {
      const updated = await applyGraphPatch({ [target]: [...(targetNode.depends_on || []), source] })
      await applyDagMutationResult(updated, false)
    } catch (err: any) {
      setError(err.message)
      await applyDagMutationResult(null, false)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const handleGraphDisconnect = async (source: string, target: string) => {
    if (!dag) return
    const targetNode = dag.nodes.find(n => n.node_id === target)
    const hasDep = targetNode && (targetNode.depends_on || []).includes(source)
    const edges = (dag as any).edges || []
    const hadEdge = edges.some((e: any) => (e.from_node || e.from) === source && (e.to_node || e.to) === target)
    const nodeDeps: Record<string, string[]> = hasDep ? { [target]: (targetNode.depends_on || []).filter(d => d !== source) } : {}
    const newEdges = hadEdge ? edges.filter((e: any) => !((e.from_node || e.from) === source && (e.to_node || e.to) === target)) : undefined
    if (!hasDep && !hadEdge) return
    setNodeActionLoading(true)
    try {
      const updated = await applyGraphPatch(nodeDeps, newEdges)
      await applyDagMutationResult(updated, false)
    } catch (err: any) {
      setError(err.message)
      await applyDagMutationResult(null, false)
    } finally {
      setNodeActionLoading(false)
    }
  }

  const changeSelectedNodeImage = async () => {
    if (!dag || !selectedNode) return
    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}/image`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_image: nodeImage.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      setShowImageDialog(false)
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

  const downloadAuditLogs = async (taskId: string) => {
    try {
      // Use the new dedicated export endpoint for complete data
      const res = await fetch(`${API}/api/tasks/${taskId}/audit-logs/export`)
      if (!res.ok) throw new Error('Failed to fetch audit logs')
      const data = await res.json()

      // Create downloadable JSON
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit-logs-${taskId}-${Date.now()}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Download failed: ${(e as Error).message}`)
    }
  }

  const downloadAuditSummary = async (taskId: string) => {
    try {
      const res = await fetch(`${API}/api/tasks/${taskId}/audit-logs/summary`)
      if (!res.ok) throw new Error("Failed to fetch audit summary")
      const data = await res.json()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `audit-summary-${taskId}-${Date.now()}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      alert(`Download failed: ${(e as Error).message}`)
    }
  }

  const examineAndLearnSkill = async (taskId: string, nodeId: string) => {
    setMiningSkill(true)
    setMineResult(null)
    setAnalysisResult(null)
    try {
      // Call the new analyze endpoint (returns assessment + draft skill for review)
      const res = await fetch(`${API}/api/skill-learning/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          node_id: nodeId,
          dag_id: dag?.id,
          created_by: 'dag-review',
          include_skill: includeSkillInReview,
          skill_format: skillFormat,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setAnalysisResult(data)
      setMineResult(data.learning_potential
        ? '✅ Skill-learning potential detected — review the draft below'
        : 'ℹ️ No reusable skill extracted. See assessment.')
    } catch (e: any) {
      setMineResult(`❌ Error: ${e.message}`)
    } finally {
      setMiningSkill(false)
    }
  }

  const deepReviewTask = async (taskId: string, nodeId: string) => {
    setDeepReviewLoading(true)
    setMineResult(null)
    try {
      const res = await fetch(`${API}/api/skill-learning/deep-review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          node_id: nodeId,
          dag_id: dag?.id,
          created_by: 'dag-review',
          include_skill: includeSkillInReview,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setDeepReviews(prev => ({ ...prev, [nodeId]: data }))
      setMineResult(null)
    } catch (e: any) {
      setMineResult(`❌ Deep review error: ${e.message}`)
    } finally {
      setDeepReviewLoading(false)
    }
  }

  const correctSkillFromReview = async (taskId: string, nodeId: string) => {
    setCorrectSkillLoading(true)
    setCorrectSkillResult(null)
    setMineResult(null)
    try {
      const res = await fetch(`${API}/api/skill-learning/correct`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          node_id: nodeId,
          dag_id: dag?.id,
          created_by: 'dag-review',
          skill_format: skillFormat,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setCorrectSkillResult(data)
    } catch (e: any) {
      setMineResult(`❌ Skill correction error: ${e.message}`)
    } finally {
      setCorrectSkillLoading(false)
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
  // Graph-level editing (drag-to-connect / edge deletion) allowed when the DAG
  // itself is in a pre-run editable state, independent of node selection.
  const dagEditable = !!(dag?.status === 'failed' || dag?.status === 'ready')
  const selectedReview = deepReviews[selectedNode?.node_id || ''] || null
  // Read actions (download logs, examine logs) allowed on completed/failed/ready DAGs
  const nodeReadActionsAllowed = !!(
    selectedNode &&
    (dag?.status === 'failed' || dag?.status === 'ready' || dag?.status === 'completed')
  )
  // Write actions (delete, enhance, retry) only on failed/ready DAGs
  const nodeWriteActionsAllowed = !!(
    selectedNode && (dag?.status === 'failed' || dag?.status === 'ready')
  )
  // Legacy alias for backward compat (used in some places)
  const nodeActionsAllowed = nodeWriteActionsAllowed

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
            {dag.locked && <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">🔒 template</span>}
            {dag.template_source_dag_id && <span className="text-xs bg-cyan-500/20 text-cyan-300 px-2 py-0.5 rounded">follow-the-guidance</span>}
          </div>
          <p className="text-gray-400 text-sm mt-1 max-w-3xl">{dag.objective}</p>
        </div>
        <div className="flex gap-2">
          {(dag.status === 'ready' || dag.status === 'failed' || dag.status === 'cancelled' || dag.status === 'completed') && !dag.locked && (
            <button onClick={startDag} className="btn-success text-sm">
              {dag.status === 'completed' ? '▶ Run (all)' : (dag.status === 'failed' ? '▶ Retry (all)' : '▶ Start')}
            </button>
          )}
          {dag.status === 'running' && !dag.locked && (
            <button onClick={cancelDag} className="btn-danger text-sm">&#9209; Cancel</button>
          )}
          {(dag.status === 'completed' || dag.status === 'failed' || dag.status === 'cancelled') && !dag.locked && (
            <button
              onClick={() => setShowRevise(!showRevise)}
              className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded-md transition-colors"
            >
              {showRevise ? 'Cancel' : '♻️ Revise'}
            </button>
          )}
          {dag.locked && (
            <>
              <button onClick={openExecuteDialog} className="btn-success text-sm">▶ Execute routine</button>
              <button onClick={unlockDag} disabled={lockSaving} className="px-3 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-sm rounded-md transition-colors disabled:opacity-50">🔓 Unlock</button>
            </>
          )}
          {!dag.locked && (dag.status === 'completed' || dag.status === 'failed' || dag.status === 'ready') && (
            <button onClick={openLockDialog} className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded-md transition-colors">🔒 Lock as template</button>
          )}
        </div>
      </div>

      {/* Lock as template dialog */}
      {showLockDialog && (
        <div className="card p-4 mb-4 border border-purple-500/30">
          <label className="block text-sm font-medium text-gray-300 mb-1">Lock as template</label>
          <p className="text-xs text-gray-500 mb-2">Freeze this DAG into a reusable, parameterized routine. Define input parameters below; node objectives can reference them as {'{key}'}. Locking also LLM-generalizes each step's skill into a reviewable template skill (see Template Skills).</p>
          {lockDialogLoading && <p className="text-xs text-purple-300 mb-2">Proposing parameters with the LLM…</p>}
          {!lockDialogLoading && lockParams.length === 0 && (
            <p className="text-xs text-gray-500 mb-2">No parameters proposed — add them manually below.</p>
          )}
          <div className="space-y-2 mb-3">
            {lockParams.map((p, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input value={p.key} onChange={(e) => updateLockParam(i, 'key', e.target.value)} placeholder="key (e.g. category)" className="w-40 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs" />
                <input value={p.label} onChange={(e) => updateLockParam(i, 'label', e.target.value)} placeholder="label" className="w-36 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs" />
                <select value={p.type} onChange={(e) => updateLockParam(i, 'type', e.target.value)} className="w-28 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs">
                  <option value="string">string</option>
                  <option value="number">number</option>
                  <option value="boolean">boolean</option>
                </select>
                <input value={p.default} onChange={(e) => updateLockParam(i, 'default', e.target.value)} placeholder="default" className="w-32 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs" />
                <input value={p.description} onChange={(e) => updateLockParam(i, 'description', e.target.value)} placeholder="description" className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs" />
                <button onClick={() => removeLockParam(i)} className="text-red-400 text-xs">✕</button>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={addLockParam} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">+ Add parameter</button>
            <div className="flex-1" />
            <button onClick={() => setShowLockDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
            <button onClick={saveLock} disabled={lockSaving || lockDialogLoading} className="px-3 py-1 text-xs rounded bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-50">
              {lockSaving ? 'Locking…' : '🔒 Lock template'}
            </button>
          </div>
        </div>
      )}

      {/* Execute routine dialog */}
      {showExecuteDialog && (
        <div className="card p-4 mb-4 border border-cyan-500/30">
          <label className="block text-sm font-medium text-gray-300 mb-1">Execute routine (follow the guidance)</label>
          <p className="text-xs text-gray-500 mb-3">Run the locked procedure against new inputs. Each step will re-execute its learned skill, guided by what it did last time.</p>
          <label className="block text-xs text-gray-400 mb-1">Objective</label>
          <textarea value={executeObjective} onChange={(e) => setExecuteObjective(e.target.value)} rows={2} className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mb-3" />
          {(dag?.template_params || []).length > 0 && (
            <div className="space-y-2 mb-3">
              {(dag?.template_params || []).map((p) => (
                <div key={p.key}>
                  <label className="block text-xs text-gray-400">{p.label || p.key}{p.description ? <span className="text-gray-600"> — {p.description}</span> : null}</label>
                  <input
                    value={executeValues[p.key] ?? ''}
                    onChange={(e) => setExecuteValues(prev => ({ ...prev, [p.key]: e.target.value }))}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
                    placeholder={p.default ?? ''}
                  />
                </div>
              ))}
            </div>
          )}
          <label className="flex items-center gap-2 text-xs text-gray-400 mb-3">
            <input type="checkbox" checked={executeAutoStart} onChange={(e) => setExecuteAutoStart(e.target.checked)} />
            Auto-start after creation
          </label>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowExecuteDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
            <button onClick={runExecute} disabled={executeSaving} className="px-3 py-1 text-xs rounded bg-cyan-600 hover:bg-cyan-500 text-white disabled:opacity-50">
              {executeSaving ? 'Creating…' : '▶ Create & run'}
            </button>
          </div>
        </div>
      )}

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

      {/* Pending interactive steps (decision / input) */}
      {userRequests.length > 0 && (
        <div className="mb-4 rounded border border-amber-700/50 bg-amber-950/30 p-4">
          <div className="text-sm font-semibold text-amber-200 mb-3">
            ⏸ Waiting for your input ({userRequests.length})
          </div>
          <div className="space-y-3">
            {userRequests.map((req) => (
              <div key={req.id} className="rounded border border-amber-800/40 bg-black/10 p-3">
                <div className="text-xs font-mono text-amber-300 mb-1">{req.node_id}</div>
                <div className="text-sm text-amber-100 mb-2">
                  {req.kind === 'decision' ? '🛑 ' : '📥 '}
                  {req.prompt}
                </div>
                {req.kind === 'decision' ? (
                  <div>
                    <div className="flex flex-wrap gap-2">
                      {(req.payload?.options || []).map((opt: any) => {
                        const selected = decisionPending[req.id]?.choice === opt.value
                        return (
                          <button
                            key={opt.value}
                            onClick={() => setDecisionPending((prev) => ({ ...prev, [req.id]: { choice: opt.value, label: opt.label } }))}
                            className={`px-3 py-1.5 text-xs rounded ${selected ? 'bg-amber-500 text-black' : 'bg-indigo-600 hover:bg-indigo-500 text-white'}`}
                          >
                            {opt.label}
                          </button>
                        )
                      })}
                    </div>
                    <div className="mt-2 space-y-2">
                      <textarea
                        value={decisionJustification[req.id] || ''}
                        onChange={(e) => setDecisionJustification((prev) => ({ ...prev, [req.id]: e.target.value }))}
                        rows={2}
                        placeholder="Justification (optional — e.g. what the rework should focus on)"
                        className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                      />
                      <button
                        onClick={() => {
                          const choice = decisionPending[req.id]?.choice
                          if (!choice) { alert('Select a decision option first'); return }
                          const needsJust = req.payload?.require_justification === true ||
                            (req.payload?.options || []).find((o: any) => o.value === choice)?.require_justification === true
                          const j = (decisionJustification[req.id] || '').trim()
                          if (needsJust && !j) { alert('Justification is required for this option'); return }
                          answerUserRequest(req.id, { choice, justification: j })
                          setDecisionPending((prev) => ({ ...prev, [req.id]: null }))
                          setDecisionJustification((prev) => ({ ...prev, [req.id]: '' }))
                        }}
                        disabled={userRequestBusy === req.id}
                        className="px-3 py-1.5 text-xs rounded bg-amber-500 hover:bg-amber-400 text-black disabled:opacity-50"
                      >
                        Submit decision
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {(req.payload?.fields || []).map((f: any) => (
                      <div key={f.key}>
                        <label className="block text-xs text-gray-400 mb-1">{f.label || f.key}</label>
                        <input
                          type={f.type === 'number' ? 'number' : 'text'}
                          defaultValue=""
                          onChange={(e) => setUserRequestAnswers((prev) => ({
                            ...prev,
                            [req.id]: { ...(prev[req.id] || {}), fields: { ...((prev[req.id] || {}).fields || {}), [f.key]: e.target.value } },
                          }))}
                          className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                        />
                      </div>
                    ))}
                    <button
                      onClick={() => answerUserRequest(req.id, { fields: userRequestAnswers[req.id]?.fields || {} })}
                      disabled={userRequestBusy === req.id}
                      className="px-3 py-1.5 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50"
                    >
                      Submit
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

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
        {dagEditable && (
          <div className="flex items-center gap-2 mb-2 text-xs">
            <span className="text-gray-500">Draw mode:</span>
            <button
              onClick={() => setGraphMode('relations')}
              className={`px-2 py-1 rounded border ${graphMode === 'relations' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
            >
              ⇢ Relations (depends on)
            </button>
            <button
              onClick={() => setGraphMode('rework')}
              className={`px-2 py-1 rounded border ${graphMode === 'rework' ? 'border-amber-400 text-amber-200 bg-amber-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
            >
              ↻ Rework loop
            </button>
            {graphMode === 'rework' && (
              <span className="text-amber-300/80">Drag from the rework step back to the decision step to create a loop.</span>
            )}
          </div>
        )}
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
            node_type: (n as any).node_type,
          }))}
          edges={(dag as any).edges || []}
          onNodeClick={(nodeId) => {
            setSelectedNodeId(nodeId === selectedNodeId ? null : nodeId)
            setActiveTab('overview')
          }}
          editable={dagEditable}
          onConnect={dagEditable ? handleGraphConnect : undefined}
          onDisconnect={dagEditable ? handleGraphDisconnect : undefined}
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
              {nodeActionsAllowed && !selectedNode.task_id && (
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
                      <button
                        onClick={() => { setShowNodeActions(false); setShowAddNodeDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ➕ Add Step After
                      </button>
                      <button
                        onClick={openEditConnectionsDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🔗 Edit Connections
                      </button>
                      <button
                        onClick={openRenameDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-teal-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ✏️ Rename
                      </button>
                      <button
                        onClick={openChangeSkillDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-purple-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🎯 Change Skill
                      </button>
                      <button
                        onClick={() => { setShowNodeActions(false); setNodeImage(selectedNode.config?.base_image || 'openclaw'); setShowImageDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🖼️ Change Image
                      </button>
                      {(dag.status === 'failed' || dag.status === 'completed') && (
                        <button
                          onClick={retryFromSelectedNode}
                          disabled={nodeActionLoading}
                          className="w-full text-left px-3 py-2 text-xs text-emerald-300 hover:bg-gray-800 border-t border-gray-700 disabled:opacity-50"
                        >
                          ▶ Run From This Step
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
              {/* Read-only actions available on completed/failed/ready DAGs */}
              {selectedNode.task_id && nodeReadActionsAllowed && !nodeWriteActionsAllowed && (
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
                    <div className="absolute right-0 mt-1 w-64 rounded border border-gray-700 bg-gray-900 shadow-xl z-50">
                      <button
                        onClick={() => downloadAuditLogs(selectedNode.task_id!)}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        📥 Download Execution Logs
                      </button>
                      <button
                        onClick={() => downloadAuditSummary(selectedNode.task_id!)}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        📋 Download Summary
                      </button>
                      <button
                        onClick={() => examineAndLearnSkill(selectedNode.task_id!, selectedNode.node_id)}
                        disabled={nodeActionLoading || miningSkill}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🧠 Examine Logs → Learn Skill
                      </button>
                      <button
                        onClick={() => deepReviewTask(selectedNode.task_id!, selectedNode.node_id)}
                        disabled={nodeActionLoading || deepReviewLoading}
                        className="w-full text-left px-3 py-2 text-xs text-amber-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🔍 Deep Review (hallucination / synthetic data)
                      </button>
                      <div className="border-t border-gray-700 my-1 px-3 py-2">
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={includeSkillInReview}
                            onChange={(e) => setIncludeSkillInReview(e.target.checked)}
                            className="accent-amber-500"
                          />
                          <span className="text-xs text-gray-400">Include skill context in review</span>
                        </label>
                        <div className="mt-2">
                          <label className="block text-[10px] text-gray-500 mb-1">Skill format (learn / correct)</label>
                          <select
                            value={skillFormat}
                            onChange={(e) => setSkillFormat(e.target.value)}
                            className="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-[11px] text-gray-300"
                          >
                            <option value="pseudo-code">pseudo-code (algorithmic)</option>
                            <option value="easy">easy (structured steps)</option>
                            <option value="code">code (ready-to-run script in original language)</option>
                          </select>
                        </div>
                      </div>
                      <div className="border-t border-gray-700 my-1" />
                      <button
                        onClick={() => { setShowNodeActions(false); setShowAddNodeDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ➕ Add Step After
                      </button>
                      <button
                        onClick={openEditConnectionsDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🔗 Edit Connections
                      </button>
                      <button
                        onClick={openRenameDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-teal-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ✏️ Rename
                      </button>
                      <button
                        onClick={openChangeSkillDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-purple-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🎯 Change Skill
                      </button>
                      <button
                        onClick={() => { setShowNodeActions(false); setNodeImage(selectedNode.config?.base_image || 'openclaw'); setShowImageDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🖼️ Change Image
                      </button>
                    </div>
                  )}
                </div>
              )}
              {/* Read-write actions on failed/ready DAGs (both read and write) */}
              {selectedNode.task_id && nodeWriteActionsAllowed && (
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
                    <div className="absolute right-0 mt-1 w-64 rounded border border-gray-700 bg-gray-900 shadow-xl z-50">
                      <button
                        onClick={() => downloadAuditLogs(selectedNode.task_id!)}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        📥 Download Execution Logs
                      </button>
                      <button
                        onClick={() => downloadAuditSummary(selectedNode.task_id!)}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        📋 Download Summary
                      </button>
                      <button
                        onClick={() => examineAndLearnSkill(selectedNode.task_id!, selectedNode.node_id)}
                        disabled={nodeActionLoading || miningSkill}
                        className="w-full text-left px-3 py-2 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🧠 Examine Logs → Learn Skill
                      </button>
                      <button
                        onClick={() => deepReviewTask(selectedNode.task_id!, selectedNode.node_id)}
                        disabled={nodeActionLoading || deepReviewLoading}
                        className="w-full text-left px-3 py-2 text-xs text-amber-200 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🔍 Deep Review (hallucination / synthetic data)
                      </button>
                      <div className="border-t border-gray-700 my-1 px-3 py-2">
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={includeSkillInReview}
                            onChange={(e) => setIncludeSkillInReview(e.target.checked)}
                            className="accent-amber-500"
                          />
                          <span className="text-xs text-gray-400">Include skill context in review</span>
                        </label>
                        <div className="mt-2">
                          <label className="block text-[10px] text-gray-500 mb-1">Skill format (learn / correct)</label>
                          <select
                            value={skillFormat}
                            onChange={(e) => setSkillFormat(e.target.value)}
                            className="w-full bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-[11px] text-gray-300"
                          >
                            <option value="pseudo-code">pseudo-code (algorithmic)</option>
                            <option value="easy">easy (structured steps)</option>
                            <option value="code">code (ready-to-run script in original language)</option>
                          </select>
                        </div>
                      </div>                      <div className="border-t border-gray-700 my-1" />
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
                      <button
                        onClick={() => { setShowNodeActions(false); setShowAddNodeDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ➕ Add Step After
                      </button>
                      <button
                        onClick={openEditConnectionsDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🔗 Edit Connections
                      </button>
                      <button
                        onClick={openRenameDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-teal-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        ✏️ Rename
                      </button>
                      <button
                        onClick={openChangeSkillDialog}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-purple-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🎯 Change Skill
                      </button>
                      <button
                        onClick={() => { setShowNodeActions(false); setNodeImage(selectedNode.config?.base_image || 'openclaw'); setShowImageDialog(true) }}
                        disabled={nodeActionLoading}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                      >
                        🖼️ Change Image
                      </button>
                      {(dag.status === 'failed' || dag.status === 'completed') && (
                        <button
                          onClick={retryFromSelectedNode}
                          disabled={nodeActionLoading}
                          className="w-full text-left px-3 py-2 text-xs text-emerald-300 hover:bg-gray-800 border-t border-gray-700 disabled:opacity-50"
                        >
                          ▶ Run From This Step
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

          {selectedNode.config?.template_guidance && (
            <details className="mb-3 p-2 bg-cyan-950/40 border border-cyan-800/50 rounded text-xs text-cyan-200">
              <summary className="cursor-pointer font-semibold text-cyan-300">📋 Previous run (follow-the-guidance reference)</summary>
              <pre className="whitespace-pre-wrap mt-1 text-cyan-200/80">{selectedNode.config.template_guidance}</pre>
            </details>
          )}

          {mineResult && (
            <div className="mb-3 p-2 rounded text-xs border border-gray-700 ${
              mineResult.startsWith('✅')
                ? 'bg-emerald-900/30 border-emerald-500/30 text-emerald-300'
                : 'bg-red-900/30 border-red-500/30 text-red-300'
            }">
              {mineResult}
            </div>
          )}

          {analysisResult && (
            <div className="mb-3 rounded border border-indigo-700/40 bg-indigo-950/20 p-3 text-xs">
              <div className="text-xs uppercase tracking-wide text-indigo-300 mb-2 flex items-center justify-between">
                <span>Skill Learning Analysis — {analysisResult.image_id} {analysisResult.image_tag && <span className="text-gray-500">({analysisResult.image_tag})</span>}</span>
                {analysisResult.skill_used_name && (
                  <span className="text-gray-400">used: {analysisResult.skill_used_name}</span>
                )}
              </div>

              <div className="mb-2 text-gray-300 whitespace-pre-wrap">{analysisResult.assessment}</div>

              {analysisResult.warnings?.length > 0 && (
                <div className="mb-2">
                  <div className="text-amber-300 font-semibold mb-1">⚠️ Warnings (potential hallucination / synthetic data)</div>
                  <ul className="list-disc list-inside text-amber-200 space-y-1">
                    {analysisResult.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}

              {analysisResult.suggested_improvements?.length > 0 && (
                <div className="mb-2">
                  <div className="text-blue-300 font-semibold mb-1">💡 Suggested improvements</div>
                  <ul className="list-disc list-inside text-blue-200 space-y-1">
                    {analysisResult.suggested_improvements.map((s: string, i: number) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}

              {analysisResult.extracted_skills?.length > 0 && (
                <div className="mt-3">
                  <div className="text-emerald-300 font-semibold mb-2">📚 Draft skill(s) for review</div>
                  {analysisResult.extracted_skills.map((sk: any, i: number) => (
                    <div key={i} className="mb-3 rounded border border-gray-700 bg-gray-900/60 p-3">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-semibold text-gray-200">{sk.name}</span>
                        <span className="text-emerald-300">{sk.status}</span>
                      </div>
                      {sk.description && <div className="text-gray-400 mb-2">{sk.description}</div>}
                      <pre className="whitespace-pre-wrap text-gray-300 mb-2 bg-gray-950/60 p-2 rounded">{sk.instructions}</pre>
                      {sk.tags?.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {sk.tags.map((t: string, j: number) => (
                            <span key={j} className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">{t}</span>
                          ))}
                        </div>
                      )}
                      <div className="mt-2 flex items-center gap-3">
                        <Link
                          href="/skill-studio"
                          className="px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white"
                        >
                          Review in Skill Studio
                        </Link>
                        <span className="text-gray-500">ID: {sk.id}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {selectedReview && (
            <div className="mb-3 rounded border border-amber-700/40 bg-amber-950/20 p-3 text-xs">
              <div className="text-xs uppercase tracking-wide text-amber-300 mb-2 flex items-center justify-between">
                <span>Deep Review — {selectedReview.image_id}{selectedReview.image_tag && <span className="text-gray-500"> ({selectedReview.image_tag})</span>}</span>
                {selectedReview.skill_used_name && <span className="text-gray-400">skill: {selectedReview.skill_used_name}</span>}
              </div>
              {selectedReview.model && (
                <div className="mb-2 text-[10px] text-gray-400 font-mono">
                  model: <span className="text-cyan-300">{selectedReview.model}</span>
                  {selectedReview.created_at && (
                    <span className="ml-2 text-gray-500">· reviewed {new Date(selectedReview.created_at).toLocaleString()}</span>
                  )}
                </div>
              )}

              <div className="mb-2 flex items-center gap-3">
                <span className={'px-2 py-0.5 rounded font-semibold ' + (selectedReview.verdict === 'clean' ? 'bg-emerald-800/50 text-emerald-200' : selectedReview.verdict === 'issues_found' ? 'bg-red-800/50 text-red-200' : 'bg-amber-800/50 text-amber-200')}>
                  verdict: {selectedReview.verdict}
                </span>
                <span className="text-gray-300">quality score: <span className="font-semibold text-amber-200">{selectedReview.score}/100</span></span>
              </div>

              <div className="mb-3 text-gray-300 whitespace-pre-wrap">{selectedReview.summary}</div>

              {selectedReview.issues?.length > 0 ? (
                <div className="mb-3">
                  <div className="text-red-300 font-semibold mb-2">⚠️ Issues found ({selectedReview.issues.length})</div>
                  <div className="space-y-2">
                    {selectedReview.issues.map((iss: any, i: number) => (
                      <div key={i} className="rounded border border-gray-700 bg-gray-900/60 p-2">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={'px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ' + (iss.severity === 'high' ? 'bg-red-800/60 text-red-100' : iss.severity === 'medium' ? 'bg-amber-800/60 text-amber-100' : 'bg-gray-700 text-gray-300')}>{iss.severity}</span>
                          <span className={'px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-indigo-900/60 text-indigo-200'}>{iss.category}</span>
                        </div>
                        <div className="text-gray-200 font-medium">{iss.finding}</div>
                        {iss.evidence && <div className="mt-1 text-gray-400 italic whitespace-pre-wrap">Evidence: {iss.evidence}</div>}
                        {iss.recommendation && <div className="mt-1 text-amber-200">→ {iss.recommendation}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mb-3 text-emerald-300">No integrity issues detected.</div>
              )}

              {selectedReview.positives?.length > 0 && (
                <div className="mb-2">
                  <div className="text-emerald-300 font-semibold mb-1">✅ Done correctly</div>
                  <ul className="list-disc list-inside text-emerald-200 space-y-1">
                    {selectedReview.positives.map((p: string, i: number) => <li key={i}>{p}</li>)}
                  </ul>
                </div>
              )}

              {selectedReview.issues?.length > 0 && (
                <button
                  onClick={() => correctSkillFromReview(selectedNode.task_id!, selectedNode.node_id)}
                  disabled={correctSkillLoading}
                  className="mt-2 w-full px-3 py-2 rounded bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold disabled:opacity-50"
                >
                  {correctSkillLoading ? '🛠️ Generating corrected skill…' : '🛠️ Correct Skill to Prevent Issues'}
                </button>
              )}

              {correctSkillResult && (
                <div className="mt-3 rounded border border-emerald-700/40 bg-emerald-950/20 p-3">
                  {correctSkillResult.unchanged ? (
                    <div className="text-emerald-300 text-xs">No issues found — no skill correction needed.</div>
                  ) : (
                    <>
                      <div className="text-xs uppercase tracking-wide text-emerald-300 mb-2">
                        ✅ Corrected skill created (child of "{correctSkillResult.skill_name}")
                      </div>
                      {correctSkillResult.corrected?.map((sk: any, i: number) => (
                        <div key={i} className="mb-3 rounded border border-gray-700 bg-gray-900/60 p-3">
                          <div className="flex items-center justify-between mb-1">
                            <span className="font-semibold text-gray-200">{sk.name}</span>
                            <span className="text-emerald-300 text-xs">{sk.status}</span>
                          </div>
                          {sk.description && <div className="text-gray-400 text-xs mb-2">{sk.description}</div>}
                          <pre className="whitespace-pre-wrap text-gray-300 text-xs mb-2 bg-gray-950/60 p-2 rounded">{sk.instructions}</pre>
                          {sk.addressed_issues?.length > 0 && (
                            <div className="mb-2">
                              <div className="text-amber-300 font-semibold text-xs mb-1">Addresses:</div>
                              <ul className="list-disc list-inside text-amber-200 text-xs space-y-1">
                                {sk.addressed_issues.map((a: string, j: number) => <li key={j}>{a}</li>)}
                              </ul>
                            </div>
                          )}
                          <a
                            href="/skill-studio"
                            className="inline-block mt-2 px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs"
                          >
                            Review in Skill Studio
                          </a>
                          <span className="ml-3 text-gray-500 text-xs">ID: {sk.skill_id}</span>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
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

          {showAddNodeDialog && selectedNode && (
            <div className="mb-3 rounded border border-indigo-700/40 bg-indigo-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-indigo-300 mb-2">
                {addNodeMode === 'parallel' ? 'Add Parallel Step (next to ' : addNodeMode === 'custom' ? 'Add Connected Step (depends on ' : 'Add Step After '}{selectedNode.node_id}
                {addNodeMode === 'parallel' ? ')' : addNodeMode === 'custom' ? ')' : ''}
              </div>
              <div className="flex gap-2 mb-2">
                <button
                  onClick={() => setAddNodeMode('after')}
                  className={`text-xs px-2 py-1 rounded border ${addNodeMode === 'after' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                >
                  After this step
                </button>
                <button
                  onClick={() => setAddNodeMode('parallel')}
                  className={`text-xs px-2 py-1 rounded border ${addNodeMode === 'parallel' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                >
                  Parallel (same deps)
                </button>
                <button
                  onClick={() => { setAddNodeMode('custom'); setAddNodeDeps(selectedNode.depends_on || []) }}
                  className={`text-xs px-2 py-1 rounded border ${addNodeMode === 'custom' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                >
                  Connect to step(s)
                </button>
              </div>
              {addNodeMode === 'parallel' && (
                <p className="text-[11px] text-gray-500 mb-2">Runs in parallel with "{selectedNode.node_id}" — depends on the same predecessors.</p>
              )}
              {addNodeMode === 'custom' && (
                <div className="mb-2">
                  <p className="text-[11px] text-gray-500 mb-1">Choose which existing step(s) this new step depends on:</p>
                  <div className="max-h-32 overflow-y-auto border border-gray-700 rounded p-1.5 space-y-1">
                    {(dag?.nodes || []).filter(n => n.node_id !== selectedNode.node_id).map(n => {
                      const checked = addNodeDeps.includes(n.node_id)
                      return (
                        <label key={n.node_id} className="flex items-center gap-2 cursor-pointer text-xs text-gray-300">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setAddNodeDeps(prev => e.target.checked ? [...prev, n.node_id] : prev.filter(d => d !== n.node_id))
                            }}
                            className="accent-indigo-500"
                          />
                          {n.node_id}
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}
              <div className="flex gap-2 mb-2">
                {(['agent', 'decision', 'input'] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setAddNodeType(t)}
                    className={`text-xs px-2 py-1 rounded border ${addNodeType === t ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                  >
                    {t === 'agent' ? '🤖 Agent' : t === 'decision' ? '🛑 Decision' : '📥 Input'}
                  </button>
                ))}
              </div>
              {addNodeType === 'decision' && (
                <div className="mb-2 space-y-2">
                  <input
                    value={addNodeQuestion}
                    onChange={(e) => setAddNodeQuestion(e.target.value)}
                    placeholder="Question (e.g. Proceed with the report?)"
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                  />
                  <textarea
                    value={addNodeOptions}
                    onChange={(e) => setAddNodeOptions(e.target.value)}
                    rows={3}
                    placeholder={'Options — one per line: Label,value\nApprove,approve\nRework,rework'}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                  />
                </div>
              )}
              {addNodeType === 'input' && (
                <div className="mb-2 space-y-2">
                  <input
                    value={addNodePrompt}
                    onChange={(e) => setAddNodePrompt(e.target.value)}
                    placeholder="Prompt (e.g. Enter the measured value)"
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                  />
                  <textarea
                    value={addNodeFields}
                    onChange={(e) => setAddNodeFields(e.target.value)}
                    rows={3}
                    placeholder={'Fields — one per line: key,label,type (text|number|select)\nmeasurement,Measurement,number'}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200"
                  />
                </div>
              )}
              <textarea
                value={addNodeDesc}
                onChange={(e) => setAddNodeDesc(e.target.value)}
                rows={2}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
                placeholder="Describe the new step..."
              />
              <label className="block text-xs text-gray-400 mb-1">Image</label>
              <select
                value={addNodeImage}
                onChange={(e) => setAddNodeImage(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
              >
                {['openclaw', 'browser', 'browser_v2', 'browser_v3', 'browser_v4', 'nanobot', 'picoclaw', 'octaveclaw', 'zeroclaw'].map(img => (
                  <option key={img} value={img}>{img}</option>
                ))}
              </select>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowAddNodeDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={addNodeAfterSelected} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Adding...' : 'Add Step'}
                </button>
              </div>
            </div>
          )}

          {showEditConnectionsDialog && selectedNode && (
            <div className="mb-3 rounded border border-indigo-700/40 bg-indigo-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-indigo-300 mb-2">
                Edit Connections for "{selectedNode.node_id}"
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <p className="text-[11px] text-gray-500 mb-1">Inputs — steps this step depends on:</p>
                  <div className="max-h-40 overflow-y-auto border border-gray-700 rounded p-1.5 space-y-1">
                    {(dag?.nodes || []).filter(n => n.node_id !== selectedNode.node_id).map(n => {
                      const checked = editConnInputs.includes(n.node_id)
                      return (
                        <label key={n.node_id} className="flex items-center gap-2 cursor-pointer text-xs text-gray-300">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setEditConnInputs(prev => e.target.checked ? [...prev, n.node_id] : prev.filter(d => d !== n.node_id))
                            }}
                            className="accent-indigo-500"
                          />
                          {n.node_id}
                        </label>
                      )
                    })}
                  </div>
                </div>
                <div>
                  <p className="text-[11px] text-gray-500 mb-1">Outputs — steps that depend on this step:</p>
                  <div className="max-h-40 overflow-y-auto border border-gray-700 rounded p-1.5 space-y-1">
                    {(dag?.nodes || []).filter(n => n.node_id !== selectedNode.node_id).map(n => {
                      const checked = editConnOutputs.includes(n.node_id)
                      return (
                        <label key={n.node_id} className="flex items-center gap-2 cursor-pointer text-xs text-gray-300">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setEditConnOutputs(prev => e.target.checked ? [...prev, n.node_id] : prev.filter(d => d !== n.node_id))
                            }}
                            className="accent-indigo-500"
                          />
                          {n.node_id}
                        </label>
                      )
                    })}
                  </div>
                </div>
              </div>

              {/* Explicit edges (conditional / loop) */}
              <div className="mt-3">
                <p className="text-[11px] text-gray-500 mb-1">
                  Edges — conditional routes &amp; rework loops (e.g. <code className="font-mono text-amber-300">edge_type: loop</code> back to a decision):
                </p>
                <div className="space-y-1 max-h-40 overflow-y-auto border border-gray-700 rounded p-1.5">
                  {dagEdges.length === 0 && <p className="text-[11px] text-gray-600">No explicit edges.</p>}
                  {dagEdges.map((e, idx) => (
                    <div key={idx} className="flex items-center gap-2 text-[11px] text-gray-300">
                      <span className="font-mono">{e.from_node || e.from}</span>
                      <span className="text-gray-600">→</span>
                      <span className="font-mono">{e.to_node || e.to}</span>
                      <span className="text-gray-500 font-mono">({e.condition || '—'}, {e.edge_type || 'rework'})</span>
                      <button
                        onClick={() => setDagEdges(prev => prev.filter((_, i) => i !== idx))}
                        className="ml-auto text-red-400 hover:text-red-300"
                        title="Remove edge"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-4 gap-1.5 mt-1.5">
                  <select value={newEdgeFrom} onChange={(e) => setNewEdgeFrom(e.target.value)} className="bg-gray-900 border border-gray-700 rounded p-1 text-[11px] text-gray-200">
                    <option value="">from…</option>
                    {(dag?.nodes || []).map(n => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}
                  </select>
                  <select value={newEdgeTo} onChange={(e) => setNewEdgeTo(e.target.value)} className="bg-gray-900 border border-gray-700 rounded p-1 text-[11px] text-gray-200">
                    <option value="">to…</option>
                    {(dag?.nodes || []).map(n => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}
                  </select>
                  <select value={newEdgeCondition} onChange={(e) => setNewEdgeCondition(e.target.value)} className="bg-gray-900 border border-gray-700 rounded p-1 text-[11px] text-gray-200">
                    {['on_success', 'on_failure', 'decision:accept', 'decision:reject', 'decision:cancel', 'loop'].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <select value={newEdgeType} onChange={(e) => setNewEdgeType(e.target.value)} className="bg-gray-900 border border-gray-700 rounded p-1 text-[11px] text-gray-200">
                    {['rework', 'loop', 'skip'].map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <button
                  onClick={() => {
                    if (!newEdgeFrom || !newEdgeTo) return
                    setDagEdges(prev => [...prev, { from_node: newEdgeFrom, to_node: newEdgeTo, condition: newEdgeCondition, edge_type: newEdgeType }])
                    setNewEdgeFrom(''); setNewEdgeTo('')
                  }}
                  className="mt-1.5 px-2 py-0.5 text-[11px] rounded bg-amber-600 hover:bg-amber-500 text-black"
                >
                  + Add edge
                </button>
              </div>

              <div className="flex justify-end gap-2 mt-2">
                <button onClick={() => setShowEditConnectionsDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={saveEditConnections} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Saving...' : 'Save Connections'}
                </button>
              </div>
            </div>
          )}

          {showRenameDialog && selectedNode && (
            <div className="mb-3 rounded border border-teal-700/40 bg-teal-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-teal-300 mb-2">
                Rename step "{selectedNode.node_id}"
              </div>
              <input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                className="input-field w-full text-sm"
                placeholder="new node id"
              />
              <div className="flex justify-end gap-2 mt-2">
                <button onClick={() => setShowRenameDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={saveRename} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-teal-600 hover:bg-teal-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Saving…' : 'Rename'}
                </button>
              </div>
            </div>
          )}

          {showSkillDialog && selectedNode && (
            <div className="mb-3 rounded border border-purple-700/40 bg-purple-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-purple-300 mb-2">
                Change Skill for "{selectedNode.node_id}"
              </div>
              <select
                value={selectedSkillId}
                onChange={(e) => setSelectedSkillId(e.target.value)}
                disabled={skillDialogLoading}
                className="input-field w-full text-sm"
              >
                <option value="">— None (no skill) —</option>
                {availableSkills.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}{s.image_id ? ` (${s.image_id})` : ''}
                  </option>
                ))}
              </select>
              {skillDialogLoading && <p className="text-[11px] text-gray-500 mt-1">Loading skills…</p>}
              {selectedSkillId && (() => {
                const sk = availableSkills.find((s) => s.id === selectedSkillId)
                return sk?.description ? <p className="text-[11px] text-gray-400 mt-1">{sk.description}</p> : null
              })()}
              <div className="flex justify-end gap-2 mt-2">
                <button onClick={() => setShowSkillDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={saveSkillAssignment} disabled={nodeActionLoading || skillDialogLoading} className="px-3 py-1 text-xs rounded bg-purple-600 hover:bg-purple-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Saving…' : 'Save Skill'}
                </button>
              </div>
            </div>
          )}

          {showImageDialog && selectedNode && (
            <div className="mb-3 rounded border border-indigo-700/40 bg-indigo-950/20 p-3">
              <div className="text-xs uppercase tracking-wide text-indigo-300 mb-2">Change Image for "{selectedNode.node_id}"</div>
              <label className="block text-xs text-gray-400 mb-1">Current: {selectedNode.config?.base_image || 'openclaw'}</label>
              <select
                value={nodeImage}
                onChange={(e) => setNodeImage(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
              >
                {['openclaw', 'browser', 'browser_v2', 'browser_v3', 'browser_v4', 'nanobot', 'picoclaw', 'octaveclaw', 'zeroclaw'].map(img => (
                  <option key={img} value={img}>{img}</option>
                ))}
              </select>
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowImageDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={changeSelectedNodeImage} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Saving...' : 'Change Image'}
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
                  {(node as any).node_type === 'decision' && (
                    <span className="text-xs bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded" title="Pauses for a user decision">🛑 Decision</span>
                  )}
                  {(node as any).node_type === 'input' && (
                    <span className="text-xs bg-cyan-500/20 text-cyan-300 px-2 py-0.5 rounded" title="Pauses for user-provided data">📥 Input</span>
                  )}
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
