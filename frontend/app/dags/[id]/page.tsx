'use client'

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import dynamic from 'next/dynamic'
import { StatusBadge } from '../../components/StatusComponents'
import DeliverablePreview, { RenderedMarkdown } from '../../components/DeliverablePreview'
import AuditReviewTab from '../../components/AuditReviewTab'
import DAGTree from '../../components/DAGTree'
import { API, API_GATEWAY, TEMPORAL_UI } from '../../lib/api'

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

function slugifyPurpose(text: string): string {
  return (
    text.toLowerCase().trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60)
  )
}

// Used until /api/agent-images responds; keeps the picker working offline.
const FALLBACK_AGENT_IMAGES = [
  'openclaw', 'browser', 'browser_v2', 'browser_v3', 'browser_v4',
  'nanobot', 'picoclaw', 'octaveclaw', 'zeroclaw',
]

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
  workflow_group_id?: string | null
  exploration_session_id?: string | null
  exploration_cycle?: number | null
  input_attachments?: DAGAttachment[]
}

interface DAGAttachment {
  name: string
  stored_as: string
  path: string
  size: number
  mime: string
  uploaded_at?: string
}

function formatAttachmentBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
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
  attachments?: DAGAttachment[]
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
  const deepLinkApplied = useRef(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'deliverables' | 'audit' | 'turns' | 'configuration'>('deliverables')
  const [previewMode, setPreviewMode] = useState<'rendered' | 'raw'>('rendered')
  const [previewCopied, setPreviewCopied] = useState(false)
  const [showDagMore, setShowDagMore] = useState(false)
  const [showDagSpec, setShowDagSpec] = useState(false)
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
  const [addNodeType, setAddNodeType] = useState<'agent' | 'decision' | 'input' | 'block'>('agent')
  const [blockLibrary, setBlockLibrary] = useState<any[]>([])
  const [addBlockId, setAddBlockId] = useState('')
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
  const [previewFile, setPreviewFile] = useState<{ url: string; filename: string } | null>(null)
  const [nodeTurnCounts, setNodeTurnCounts] = useState<Record<string, number>>({})

  // Skill extraction state
  const [miningSkill, setMiningSkill] = useState(false)
  const [driverPromoting, setDriverPromoting] = useState(false)
  const [blockPromoting, setBlockPromoting] = useState(false)
  const [blockEditOpen, setBlockEditOpen] = useState(false)
  const [blockEditId, setBlockEditId] = useState('')
  const [blockEditInputs, setBlockEditInputs] = useState('{}')
  const [blockLibrary2, setBlockLibrary2] = useState<any[]>([])
  const [blockLearnResult, setBlockLearnResult] = useState<any>(null)
  const [agentImageOptions, setAgentImageOptions] = useState<string[]>(FALLBACK_AGENT_IMAGES)

  // Image picker is backed by the registered agent images (banking, ann2snn, ...).
  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/agent-images`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: any[]) => {
        if (cancelled || !Array.isArray(rows)) return
        const ids = rows
          .filter((i) => i && i.enabled !== false && i.id)
          .map((i) => String(i.id))
        const merged = ['openclaw', ...ids.filter((x) => x !== 'openclaw')]
        setAgentImageOptions(Array.from(new Set(merged)))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    fetch(`${API}/api/llm/models`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const ids = (data?.models || [])
          .map((model: any) => model?.id)
          .filter(Boolean)
          .map(String)
        if (ids.length) setModelOptions(Array.from(new Set(ids)))
      })
      .catch(() => {})
  }, [])

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
  const [workspaceTree, setWorkspaceTree] = useState<{ path: string; name: string; size: number }[] | null>(null)
  const [workspaceTreeLoading, setWorkspaceTreeLoading] = useState(false)
  const [workspacePreview, setWorkspacePreview] = useState<{ path: string; content: string; isImage: boolean } | null>(null)
  const [viewMode, setViewMode] = useState<'graph' | 'tree'>('graph')
  const [showExecutionState, setShowExecutionState] = useState(false)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelChanging, setModelChanging] = useState(false)
  const [skillInfo, setSkillInfo] = useState<Record<string, any>>({})
  const [logIteration, setLogIteration] = useState<number | null>(null)
  const [inspectorWidth, setInspectorWidth] = useState(700)
  const [inspectorMaximized, setInspectorMaximized] = useState(false)
  const [showFullWorkspace, setShowFullWorkspace] = useState(false)
  const [showFullObjective, setShowFullObjective] = useState(false)
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null)

  useEffect(() => {
    deepLinkApplied.current = false
  }, [dagId])

  useEffect(() => {
    if (!dag || dag.id !== dagId || deepLinkApplied.current) return
    deepLinkApplied.current = true
    const query = new URLSearchParams(window.location.search)
    const nodeId = query.get('node')
    if (nodeId && dag.nodes.some((node) => node.node_id === nodeId)) {
      setSelectedNodeId(nodeId)
    }
    const tab = query.get('tab')
    if (tab === 'deliverables' || tab === 'audit' || tab === 'turns' || tab === 'configuration') {
      setActiveTab(tab)
    }
  }, [dag, dagId])

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

  // Live LLM-turn counter for running agent nodes (polled from the control-plane
  // in-memory interaction log; frozen once the node leaves the running state).
  const dagRef = useRef(dag)
  dagRef.current = dag
  const prevRunningRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (dag?.status !== 'running') {
      prevRunningRef.current = new Set()
      return
    }
    const iv = setInterval(async () => {
      const d = dagRef.current
      if (!d || d.status !== 'running') return
      const runningNodes = d.nodes.filter(n => n.status === 'running' && n.task_id)
      const nowRunning = new Set(runningNodes.map(n => n.node_id))
      for (const n of runningNodes) {
        if (!prevRunningRef.current.has(n.node_id)) {
          // Fresh run (e.g. loop re-entry) — restart its counter.
          setNodeTurnCounts(prev => ({ ...prev, [n.node_id]: 0 }))
        }
      }
      prevRunningRef.current = nowRunning

      const results = await Promise.all(runningNodes.map(async (n) => {
        try {
          const r = await fetch(`${API}/api/llm/interactions/${n.task_id}/count`)
          if (!r.ok) return null
          const data = await r.json()
          return [n.node_id, data.total] as const
        } catch {
          return null
        }
      }))
      setNodeTurnCounts(prev => {
        const next = { ...prev }
        for (const item of results) {
          if (!item) continue
          const [id, total] = item
          // Max-guard: the worker clears the interaction log right before the
          // node flips to completed, so never let the counter flicker down to 0.
          next[id] = Math.max(next[id] ?? 0, total)
        }
        return next
      })
    }, 2000)
    return () => clearInterval(iv)
  }, [dag?.status, dagId])

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

  // Load audit turns when the Turns & Logs tab is active
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node?.task_id || activeTab !== 'turns') return
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

  // Load SBOM when the configuration tab is active
  useEffect(() => {
    const node = dag?.nodes.find(n => n.node_id === selectedNodeId)
    if (!node?.task_id || activeTab !== 'configuration') return
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

   // Load workspace manifest when deliverables tab is active
   useEffect(() => {
     if (!dag || activeTab !== 'deliverables' || workspaceManifest) return

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
    if (!(dag.status === 'failed' || dag.status === 'ready' || dag.status === 'cancelled')) return

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
      const purpose = addNodeDesc.trim()
      let purposeSeed = ''
      if (!purpose) {
        if (addNodeType === 'block' && addBlockId) purposeSeed = slugifyPurpose(addBlockId) || 'step'
        else if (addNodeType === 'decision') purposeSeed = slugifyPurpose(addNodeQuestion.trim()) || 'step'
        else if (addNodeType === 'input') purposeSeed = 'collect-input'
      }
      const baseId = slugifyPurpose(purpose || purposeSeed) || 'step'
      const takenIds = new Set((dag.nodes || []).map((n) => n.node_id))
      let newId = baseId
      for (let i = 2; takenIds.has(newId); i++) newId = `${baseId}-${i}`
      const nodeDesc = purpose || (purposeSeed === 'collect-input' ? 'Collect user input' : purposeSeed && purposeSeed !== 'step' ? purposeSeed.replace(/-/g, ' ') : 'New step')
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
      } else if (addNodeType === 'block') {
        if (!addBlockId) throw new Error('Select a block from the library')
        config.type = 'block'
        config.block = addBlockId
      }
      const res = await fetch(`${API}/api/dags/${dagId}/nodes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          node_id: newId,
          description: nodeDesc,
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
    if (!(dag.status === 'failed' || dag.status === 'ready' || dag.status === 'cancelled')) return

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
    if (!(dag.status === 'failed' || dag.status === 'completed' || dag.status === 'cancelled')) return

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

  const promoteRunToDriver = async (taskId: string) => {
    if (!taskId) return
    setDriverPromoting(true)
    try {
      const imageId = selectedNode?.config?.base_image || 'browser_v4'
      const res = await fetch(`${API}/api/skill-learning/skills/promote-from-task`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          image_id: imageId,
          name: `${selectedNode?.node_id || 'Driver'} (task-learned)`,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        alert(data.detail || 'Promote failed')
        return
      }
      alert(`Created draft driver skill ${data.id} — review & activate in Skill Studio.`)
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setDriverPromoting(false)
    }
  }

  const promoteRunToBlock = async (taskId: string) => {
    if (!taskId) return
    setBlockPromoting(true)
    setBlockLearnResult(null)
    try {
      const res = await fetch(`${API}/api/blocks/learn-from-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          dag_id: dag?.id,
          node_id: selectedNode?.node_id,
          created_by: 'dag-review',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { setMineResult(`❌ Learn block failed: ${data.detail || `HTTP ${res.status}`}`); return }
      setBlockLearnResult(data)
      setMineResult(null)
    } catch (e: any) { setMineResult(`❌ Learn block error: ${e.message}`) } finally { setBlockPromoting(false) }
  }

  const openBlockEditor = async () => {
    if (!selectedNode) return
    const cfg = (selectedNode as any).config || {}
    setBlockEditId(cfg.block || '')
    setBlockEditInputs(JSON.stringify(cfg.inputs || {}, null, 2))
    try {
      const res = await fetch(`${API}/api/blocks?status_filter=active`)
      if (res.ok) setBlockLibrary2(await res.json())
    } catch { setBlockLibrary2([]) }
    setBlockEditOpen(true)
  }

  const saveBlockEditor = async () => {
    if (!selectedNode) return
    let inputs: any = {}
    try { inputs = JSON.parse(blockEditInputs || '{}') } catch { alert('inputs must be valid JSON'); return }
    setNodeActionLoading(true)
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: { type: 'block', block: blockEditId, inputs } }),
      })
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${res.status}`) }
      setBlockEditOpen(false)
      await fetchDag()
    } catch (e: any) { alert(`Error: ${e.message}`) } finally { setNodeActionLoading(false) }
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
      setActiveTab('audit')
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

  useEffect(() => {
    const skillId = selectedNode?.selected_skill_v2_id
    if (!skillId || skillInfo[skillId]) return
    let cancelled = false
    fetch(`${API}/api/skill-learning/skills/${skillId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) {
          setSkillInfo((prev) => ({ ...prev, [skillId]: data }))
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [selectedNode?.selected_skill_v2_id, skillInfo])

  useEffect(() => {
    if ((activeTab !== 'deliverables' && activeTab !== 'audit') || !dag) return
    let cancelled = false
    setWorkspaceTreeLoading(true)
    fetch(`${API}/api/dags/${dagId}/workspace/tree`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled) setWorkspaceTree(data?.entries || [])
      })
      .catch(() => {
        if (!cancelled) setWorkspaceTree([])
      })
      .finally(() => {
        if (!cancelled) setWorkspaceTreeLoading(false)
      })
    return () => { cancelled = true }
  }, [activeTab, dagId, dag])

  const effectiveNodeModel = selectedNode?.config?.llm_model || dag?.llm_model || ''

  const changeNodeModel = async (model: string) => {
    if (!selectedNode || !model || model === effectiveNodeModel) return
    setModelChanging(true)
    setError(null)
    try {
      const resp = await fetch(`${API}/api/dags/${dagId}/nodes/${selectedNode.node_id}/model`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ llm_model: model }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      setDag((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          nodes: prev.nodes.map((node) =>
            node.node_id === selectedNode.node_id
              ? { ...node, config: { ...(node.config || {}), llm_model: model } }
              : node
          ),
        }
      })
      await fetchDag()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setModelChanging(false)
    }
  }

  const openWorkspaceFile = async (path: string) => {
    if (!path) return
    const encoded = path.split('/').map((part) => encodeURIComponent(part)).join('/')
    const url = `${API}/api/dags/${dagId}/workspace/files/${encoded}?inline=1`
    const ext = (path.split('.').pop() || '').toLowerCase()
    const image = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'].includes(ext)
    const framed = ['pdf', 'html', 'htm'].includes(ext)
    if (image || framed) {
      setWorkspacePreview({ path, content: url, isImage: image })
      return
    }
    setWorkspacePreview({ path, content: 'Loading…', isImage: false })
    try {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error(await resp.text())
      const mime = resp.headers.get('content-type') || ''
      if (mime.startsWith('image/')) {
        setWorkspacePreview({ path, content: url, isImage: true })
        return
      }
      const text = await resp.text()
      setWorkspacePreview({
        path,
        content: text.length > 200_000 ? `${text.slice(0, 200_000)}\n\n… truncated (${text.length} chars)` : text,
        isImage: false,
      })
    } catch (err: any) {
      setWorkspacePreview({ path, content: `Failed to load ${path}: ${err.message}`, isImage: false })
    }
  }

  const startInspectorResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (typeof window === 'undefined' || window.innerWidth < 1280 || inspectorMaximized) return
    event.preventDefault()
    resizeRef.current = { startX: event.clientX, startWidth: inspectorWidth }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const moveInspectorResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!resizeRef.current) return
    const delta = resizeRef.current.startX - event.clientX
    setInspectorWidth(Math.min(1000, Math.max(460, resizeRef.current.startWidth + delta)))
  }

  const endInspectorResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    resizeRef.current = null
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      // pointer capture may already be released
    }
  }

  const workspaceOwnFiles = workspaceTree?.filter(
    (entry) => selectedNode && entry.path.startsWith(`${selectedNode.node_id}/`)
  ) || []
  const workspaceInputFiles = workspaceTree?.filter(
    (entry) =>
      selectedNode &&
      (selectedNode.depends_on || []).some((dep) => entry.path.startsWith(`${dep}/`))
  ) || []
  const workspaceAttachmentFiles = workspaceTree?.filter(
    (entry) => entry.path.startsWith('attachments/')
  ) || []

  const fileExt = (path: string) => {
    const base = path.split('/').pop() || path
    const dot = base.lastIndexOf('.')
    return dot >= 0 ? base.slice(dot + 1).toLowerCase() : ''
  }
  const fileTypeLabel = (path: string) => {
    const ext = fileExt(path)
    const base = (path.split('/').pop() || '').toLowerCase()
    if (!ext) return 'Script'
    if (['md', 'markdown'].includes(ext)) return 'Markdown'
    if (ext === 'tex') return 'LaTeX'
    if (ext === 'py' || ext === 'ipynb') return 'Python'
    if (ext === 'm') return 'Octave'
    if (ext === 'sh' || ext === 'bash') return 'Shell'
    if (ext === 'js' || ext === 'mjs' || ext === 'cjs' || ext === 'jsx') return 'JavaScript'
    if (ext === 'ts' || ext === 'tsx') return 'TypeScript'
    if (ext === 'json') return 'JSON'
    if (ext === 'yaml' || ext === 'yml') return 'YAML'
    if (ext === 'csv') return 'CSV'
    if (ext === 'tsv') return 'TSV'
    if (ext === 'pdf') return 'PDF'
    if (ext === 'log' || (ext === 'txt' && base.includes('log'))) return 'Log'
    if (ext === 'txt') return 'Text'
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'].includes(ext)) return 'Image'
    return ext ? ext.toUpperCase() : 'File'
  }
  const isKeyDeliverable = (path: string) => {
    const ext = fileExt(path)
    if (['md', 'markdown', 'tex', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'csv'].includes(ext)) return true
    return /(report|metrics|company_data|summary)/i.test(path) && ['json', 'yaml', 'yml', 'txt'].includes(ext)
  }
  const isScriptOrLog = (path: string) => {
    const ext = fileExt(path)
    const base = (path.split('/').pop() || '').toLowerCase()
    if (['py', 'm', 'sh', 'bash', 'js', 'ts', 'log', 'txt', 'ipynb'].includes(ext)) return true
    return base === 'results.json' || base === 'validate.py' || base.startsWith('run.') || base.startsWith('console')
  }
  const groupWorkspaceFiles = (entries: { path: string; size: number }[]) => {
    const key: { path: string; size: number }[] = []
    const scripts: { path: string; size: number }[] = []
    const other: { path: string; size: number }[] = []
    for (const entry of entries) {
      if (isKeyDeliverable(entry.path)) key.push(entry)
      else if (isScriptOrLog(entry.path)) scripts.push(entry)
      else other.push(entry)
    }
    return { key, scripts, other }
  }
  const activeGroups = groupWorkspaceFiles(showFullWorkspace ? (workspaceTree || []) : workspaceOwnFiles)
  const selectedPreviewEntry = workspacePreview
    ? (workspaceTree || []).find((entry) => entry.path === workspacePreview.path) || null
    : null
  const selectedIsMarkdown = workspacePreview ? ['md', 'markdown'].includes(fileExt(workspacePreview.path)) : false
  const selectedIsImageFile = workspacePreview ? ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'].includes(fileExt(workspacePreview.path)) : false
  const selectedIsFrame = workspacePreview ? ['pdf', 'html', 'htm'].includes(fileExt(workspacePreview.path)) : false
  const workspacePreviewUrl = workspacePreview
    ? `${API}/api/dags/${dagId}/workspace/files/${workspacePreview.path
        .split('/')
        .map((part) => encodeURIComponent(part))
        .join('/')}?inline=1`
    : ''

  const copyWorkspacePreview = async () => {
    if (!workspacePreview || workspacePreview.isImage) return
    const ext = (workspacePreview.path.split('.').pop() || '').toLowerCase()
    if (['pdf', 'html', 'htm'].includes(ext)) return
    try {
      await navigator.clipboard.writeText(workspacePreview.content)
      setPreviewCopied(true)
      setTimeout(() => setPreviewCopied(false), 1500)
    } catch {
      setError('Could not copy file contents to the clipboard')
    }
  }

  useEffect(() => {
    setWorkspacePreview(null)
    setPreviewMode('rendered')
    setCorrectSkillResult(null)
  }, [selectedNodeId])

  const explorerFileIcon = (path: string) => {
    const ext = fileExt(path)
    if (['md', 'markdown', 'txt', 'tex'].includes(ext)) return '📄'
    if (ext === 'pdf') return '📕'
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) return '🖼️'
    if (['csv', 'tsv'].includes(ext)) return '📊'
    if (ext === 'json' || ext === 'yaml' || ext === 'yml') return '🧾'
    if (ext === 'py' || ext === 'ipynb') return '🐍'
    if (ext === 'log') return '📜'
    if (['sh', 'bash'].includes(ext)) return '⌨️'
    return '📎'
  }

  const explorerFileRow = (entry: { path: string; size: number }) => (
    <button
      key={entry.path}
      onClick={() => openWorkspaceFile(entry.path)}
      className={`flex w-full items-start gap-2 rounded px-1.5 py-1 text-left text-[11px] ${
        workspacePreview?.path === entry.path
          ? 'bg-indigo-900/40 text-indigo-200'
          : 'text-gray-400 hover:bg-gray-800'
      }`}
      title={entry.path}
    >
      <span className="mt-0.5 shrink-0">{explorerFileIcon(entry.path)}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate font-mono text-[11px]">{entry.path.split('/').pop() || entry.path}</span>
          <span
            className={`ml-auto h-1.5 w-1.5 shrink-0 rounded-full ${
              selectedReview ? (selectedDrFlagged ? 'bg-amber-400' : 'bg-emerald-400') : 'bg-gray-600'
            }`}
            title={verificationLabel}
          />
        </span>
        <span className="mt-0.5 block text-[9px] text-gray-600">
          {formatAttachmentBytes(entry.size)} • {fileTypeLabel(entry.path)}
        </span>
      </span>
    </button>
  )

  const selectedTaskData = selectedNode?.task_id ? nodeTaskData[selectedNode.task_id] : null
const selectedNodeState = selectedNode ? nodeState[selectedNode.node_id] : null
   const selectedNodeAcceptance = selectedNode ? nodeAcceptance[selectedNode.node_id] : null
   const selectedFailureReason = selectedNode ? summarizeNodeFailureReason(selectedNode, selectedNodeState || undefined) : null
  // Graph-level editing (drag-to-connect / edge deletion) allowed when the DAG
  // itself is in a pre-run editable state, independent of node selection.
  const dagEditable = !!(dag?.status === 'failed' || dag?.status === 'ready' || dag?.status === 'cancelled')
  const selectedReview = deepReviews[selectedNode?.node_id || ''] || null
  // Compact per-node deep-review summaries (verdict/score/top finding) for the
  // graph nodes, so completed-but-flagged steps are visible at a glance.
  const deepReviewSummaries = useMemo(() => {
    const out: Record<string, { verdict?: string; score?: number; finding?: string }> = {}
    for (const [nid, r] of Object.entries(deepReviews)) {
      const rec = r as any
      const issues = Array.isArray(rec?.issues) ? rec.issues : []
      const top = (issues[0] as any)?.finding || rec?.summary || ''
      out[nid] = { verdict: rec?.verdict, score: rec?.score, finding: top }
    }
    return out
  }, [deepReviews])
  // Deep-review flag severity: red only when the quality score is below 50.
  const selectedDrFlagged = !!(selectedReview && (selectedReview.verdict === 'issues_found' || selectedReview.verdict === 'needs_attention'))
  const selectedReviewIssues = Array.isArray(selectedReview?.issues) ? selectedReview.issues.length : 0
  const selectedReviewScore = typeof selectedReview?.score === 'number' ? selectedReview.score : null
  const reviewPillClass =
    selectedReviewScore === null
      ? 'bg-gray-800 text-gray-400'
      : selectedReviewScore >= 80
        ? 'bg-emerald-500/15 text-emerald-300'
        : selectedReviewScore >= 50
          ? 'bg-amber-500/15 text-amber-300'
          : 'bg-red-500/15 text-red-300'
  const nodeDuration = (() => {
    if (!selectedNode?.started_at) return null
    const start = new Date(selectedNode.started_at).getTime()
    if (Number.isNaN(start)) return null
    const end = selectedNode.completed_at ? new Date(selectedNode.completed_at).getTime() : Date.now()
    if (Number.isNaN(end)) return null
    const seconds = Math.max(0, Math.round((end - start) / 1000))
    if (seconds < 60) return `${seconds}s`
    const minutes = Math.floor(seconds / 60)
    const rest = seconds % 60
    if (minutes < 60) return `${minutes}m ${rest}s`
    const hours = Math.floor(minutes / 60)
    return `${hours}h ${minutes % 60}m`
  })()
  const deliverableCount = workspaceOwnFiles.length
  const turnCount = selectedTaskData?.outputs.length || selectedTaskData?.auditTurns?.total_turns || 0
  const verificationLabel = selectedReview
    ? selectedDrFlagged
      ? 'Needs Review ⚠'
      : 'Verification Passed (Local) ✓'
    : 'Not audited'
  const verificationClass = selectedReview
    ? selectedDrFlagged
      ? 'text-amber-300'
      : 'text-emerald-300'
    : 'text-gray-500'
  const selectedTopIssue = (Array.isArray(selectedReview?.issues) ? [...selectedReview.issues] : [])
    .sort((a: any, b: any) => {
      const rank: Record<string, number> = { high: 0, medium: 1, low: 2 }
      return (rank[String(a?.severity || '').toLowerCase()] ?? 3) - (rank[String(b?.severity || '').toLowerCase()] ?? 3)
    })[0]
  const qualityWarningText = String(selectedTopIssue?.finding || selectedReview?.summary || '').trim() || 'Review flagged issues that need attention.'
  const selectedDrScore = selectedReview?.score
  const drRed = selectedDrFlagged && selectedDrScore !== undefined && selectedDrScore !== null && selectedDrScore < 50
  // Read actions (download logs, examine logs) allowed on completed/failed/ready DAGs
  const nodeReadActionsAllowed = !!(
    selectedNode &&
    (dag?.status === 'failed' || dag?.status === 'ready' || dag?.status === 'completed' || dag?.status === 'cancelled')
  )
  // Write actions (delete, enhance, retry) only on failed/ready DAGs
  const nodeWriteActionsAllowed = !!(
    selectedNode && (dag?.status === 'failed' || dag?.status === 'ready' || dag?.status === 'cancelled')
  )
  // Legacy alias for backward compat (used in some places)
  const nodeActionsAllowed = nodeWriteActionsAllowed

  if (error) return <div className="text-red-400 p-8">Error: {error}</div>
  if (!dag) return <div className="text-gray-500 p-8">Loading...</div>

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
            {dag.exploration_session_id && (
              <Link href={`/explorations/${dag.exploration_session_id}`} className="text-xs bg-cyan-500/15 text-cyan-300 px-2 py-0.5 rounded hover:bg-cyan-500/25">
                🧭 Exploration cycle {dag.exploration_cycle ?? '?'} &rarr;
              </Link>
            )}
          </div>
          <div className="mt-1 max-w-3xl">
            <p className={`text-gray-400 text-sm ${showFullObjective ? '' : 'line-clamp-3'}`}>{dag.objective}</p>
            {dag.objective.length > 320 && (
              <button
                onClick={() => setShowFullObjective((value) => !value)}
                className="mt-1 text-[11px] text-indigo-400 hover:text-indigo-300"
              >
                {showFullObjective ? 'Show less' : 'Show full objective'}
              </button>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowDagSpec(true)}
            className="btn-secondary text-sm"
            title="View the DAG specification"
          >
            View Spec
          </button>
          <button
            onClick={() => {
              const blob = new Blob([JSON.stringify(dag.dag_json || dag, null, 2)], { type: 'application/json' })
              const url = URL.createObjectURL(blob)
              const anchor = document.createElement('a')
              anchor.href = url
              anchor.download = `${dag.id}.json`
              document.body.appendChild(anchor)
              anchor.click()
              anchor.remove()
              URL.revokeObjectURL(url)
            }}
            className="btn-secondary text-sm"
          >
            Export
          </button>
          {(dag.status === 'ready' || dag.status === 'failed' || dag.status === 'cancelled' || dag.status === 'completed') && !dag.locked && (
            <button onClick={startDag} className="btn-success text-sm">
              {dag.status === 'completed' ? '▶ Run (all)' : (dag.status === 'failed' ? '▶ Retry (all)' : '▶ Run')}
            </button>
          )}
          <div className="relative">
            <button
              onClick={() => setShowDagMore((value) => !value)}
              className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm rounded-md border border-gray-700"
              title="More pipeline actions"
            >
              More ▾
            </button>
            {showDagMore && (
              <div className="absolute right-0 z-50 mt-1 w-56 rounded border border-gray-700 bg-gray-900 shadow-xl">
                {(dag.status === 'completed' || dag.status === 'failed' || dag.status === 'cancelled') && !dag.locked && (
                  <button
                    onClick={() => { setShowDagMore(false); setShowRevise((value) => !value) }}
                    className="w-full px-3 py-2 text-left text-xs text-indigo-300 hover:bg-gray-800"
                  >
                    {showRevise ? 'Close revise panel' : '♻️ Revise'}
                  </button>
                )}
                {!dag.locked && (dag.status === 'completed' || dag.status === 'failed' || dag.status === 'ready') && (
                  <button
                    onClick={() => { setShowDagMore(false); openLockDialog() }}
                    className="w-full px-3 py-2 text-left text-xs text-purple-300 hover:bg-gray-800"
                  >
                    🔒 Lock as template
                  </button>
                )}
                {dag.status === 'running' && !dag.locked && (
                  <button
                    onClick={() => { setShowDagMore(false); cancelDag() }}
                    className="w-full px-3 py-2 text-left text-xs text-red-300 hover:bg-gray-800"
                  >
                    ⏹ Cancel
                  </button>
                )}
                {dag.locked && (
                  <button
                    onClick={() => { setShowDagMore(false); openExecuteDialog() }}
                    className="w-full px-3 py-2 text-left text-xs text-emerald-300 hover:bg-gray-800"
                  >
                    ▶ Execute routine
                  </button>
                )}
                {dag.locked && (
                  <button
                    onClick={() => { setShowDagMore(false); unlockDag() }}
                    disabled={lockSaving}
                    className="w-full px-3 py-2 text-left text-xs text-amber-300 hover:bg-gray-800 disabled:opacity-50"
                  >
                    🔓 Unlock
                  </button>
                )}
                {dag.locked && dag.status !== 'running' && (
                  <div className="border-t border-gray-800 px-3 py-2 text-[10px] text-gray-500">
                    Routine is locked as a reusable template.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Attached reference files */}
      {dag.input_attachments && dag.input_attachments.length > 0 && (
        <div className="card p-4 mb-4 border border-cyan-500/20">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wide text-cyan-300">
              Attachments ({dag.input_attachments.length})
            </div>
            <span className="text-[11px] text-gray-500">read-only · /workspace/attachments/</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {dag.input_attachments.map((att) => (
              <a
                key={att.stored_as}
                href={`${API}/api/dags/${dagId}/attachments/${encodeURIComponent(att.stored_as)}`}
                className="flex items-center gap-2 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs hover:border-cyan-500/50"
                title={`${att.mime} · ${formatAttachmentBytes(att.size)}`}
              >
                <span className="font-mono text-gray-300">{att.name}</span>
                <span className="text-gray-500">{formatAttachmentBytes(att.size)}</span>
                <span className="text-cyan-400">↓</span>
              </a>
            ))}
          </div>
        </div>
      )}

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
                onClick={() => { setSelectedNodeId(nodeId); setActiveTab('deliverables') }}
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

      {/* Central canvas: graph for design, tree for large DAGs */}
      <div
        className={`grid grid-cols-1 gap-4 items-start ${
          selectedNode && !inspectorMaximized
            ? 'xl:grid-cols-[minmax(0,1fr)_var(--inspector-width)]'
            : ''
        }`}
        style={{ '--inspector-width': `${inspectorWidth}px` } as any}
      >
      <div className={`mb-4 min-w-0 relative ${inspectorMaximized ? 'hidden' : ''}`}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <button
            onClick={() => setViewMode('graph')}
            className={`px-2 py-1 rounded border ${viewMode === 'graph' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
          >
            ◉ Graph
          </button>
          <button
            onClick={() => setViewMode('tree')}
            className={`px-2 py-1 rounded border ${viewMode === 'tree' ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
          >
            ☰ Tree
          </button>
          <span className="text-gray-500">{dag.nodes.length} nodes</span>
        </div>
        {dagEditable && viewMode === 'graph' && (
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
        {viewMode === 'graph' ? (
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
            deliverables_keys: (n as any).deliverables_keys
              || (n as any).output_data?.deliverables_keys
              || Object.keys(((n as any).output_data?.deliverables) || {})
              || [],
            selected_skill_v2_id: (n as any).selected_skill_v2_id,
            base_image: (n as any).config?.base_image,
            error: (n as any).output_data?.error,
            gate_failure: (n as any).output_data?.gate_failure,
            output_message: (n as any).output_data?.output || (n as any).output_data?.compact_summary || '',
            dag_id: dagId,
            iteration: (n as any).output_data?.iteration,
          }))}
          edges={(dag as any).edges || []}
          onNodeClick={(nodeId) => {
            setSelectedNodeId(nodeId)
            setActiveTab('deliverables')
          }}
          onSelectNode={(nodeId) => {
            setSelectedNodeId(nodeId)
            setActiveTab('deliverables')
          }}
          editable={dagEditable}
          onConnect={dagEditable ? handleGraphConnect : undefined}
          onDisconnect={dagEditable ? handleGraphDisconnect : undefined}
          onOpenDeliverable={(url, filename) => setPreviewFile({ url, filename })}
          turnCounts={nodeTurnCounts}
          deepReviews={deepReviewSummaries}
        />
        ) : (
          <DAGTree
            nodes={dag.nodes}
            selectedNodeId={selectedNodeId}
            onSelectNode={(nodeId) => { setSelectedNodeId(nodeId); setActiveTab('deliverables') }}
            deepReviews={deepReviewSummaries}
            turnCounts={nodeTurnCounts}
          />
        )}
        {selectedNode && !inspectorMaximized && (
          <div
            onPointerDown={startInspectorResize}
            onPointerMove={moveInspectorResize}
            onPointerUp={endInspectorResize}
            onPointerCancel={endInspectorResize}
            className="hidden xl:block absolute -right-3 top-0 z-20 h-full w-2 cursor-col-resize rounded bg-transparent hover:bg-indigo-500/40"
            title="Drag to resize the inspector"
          />
        )}
      </div>

      {/* Selected Node Detail Panel */}
      {selectedNode && (
        <div className={`card border-blue-500/30 mb-4 min-w-0 ${inspectorMaximized ? '' : 'xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:overflow-y-auto'}`}>
          {/* Node header */}
          <div className="mb-3 border-b border-gray-800 pb-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wide text-gray-500">NODE:</span>
                  <span className="font-mono text-sm font-bold text-white">{selectedNode.node_id}</span>
                  <StatusBadge status={selectedNode.status} />
                  <span className="ml-1 text-[10px] uppercase tracking-wide text-gray-500">Audit:</span>
                  <button
                    onClick={() => {
                      setActiveTab('audit')
                      if (selectedNode.task_id && !selectedReview) deepReviewTask(selectedNode.task_id, selectedNode.node_id)
                    }}
                    className={`rounded px-1.5 py-0.5 text-[10px] ${reviewPillClass}`}
                    title={selectedReview?.summary || 'Open Audit & Review'}
                  >
                    {selectedReview
                      ? `⚠ ${selectedReviewScore ?? '?'}/100 (${selectedReviewIssues} Issue${selectedReviewIssues === 1 ? '' : 's'})`
                      : 'Not audited'}
                  </button>
                </div>
                {selectedNode.description && (
                  <p className="mt-1 line-clamp-2 text-xs italic text-gray-400" title={selectedNode.description}>
                    &quot;{selectedNode.description}&quot;
                  </p>
                )}
                <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[11px] text-gray-500">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-gray-300">{effectiveNodeModel || '—'}</span>
                    <span className="text-gray-700">•</span>
                    <span className="text-gray-300">{selectedNode.config?.dag_image || selectedNode.config?.base_image || 'openclaw'}</span>
                    <span className="text-gray-700">•</span>
                    <span className="text-gray-300">⏱ {nodeDuration || '—'}</span>
                    {selectedNode.depends_on.length > 0 && (
                      <>
                        <span className="text-gray-700">•</span>
                        <span className="text-gray-500">Deps:</span>
                        {selectedNode.depends_on.map((dep) => (
                          <button
                            key={dep}
                            onClick={() => { setSelectedNodeId(dep); setActiveTab('deliverables') }}
                            className="rounded border border-gray-700 px-1.5 py-0.5 font-mono text-[10px] text-blue-300 hover:border-blue-500/60 hover:text-blue-200"
                            title={`Open dependency ${dep}`}
                          >
                            {dep}
                          </button>
                        ))}
                      </>
                    )}
                  </div>
                  <div className="ml-auto flex shrink-0 items-center gap-3">
                    {selectedNode.task_id && (
                      <Link href={`/tasks/${selectedNode.task_id}`} className="text-blue-400 hover:text-blue-300">
                        Full task view &#8599;
                      </Link>
                    )}
                    <a
                      href={getNodeWorkflowLink(selectedNode.node_id)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-400 hover:text-blue-300"
                    >
                      Temporal &#8599;
                    </a>
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5 text-xs">
                {(dag.status === 'failed' || dag.status === 'completed' || dag.status === 'cancelled') && (
                  <button
                    onClick={retryFromSelectedNode}
                    disabled={nodeActionLoading}
                    className="btn-secondary text-[10px] px-2 py-1"
                    title="Run this step and continue downstream"
                  >
                    ▷ Run Step
                  </button>
                )}
                {((selectedNode.task_id && nodeReadActionsAllowed) || nodeWriteActionsAllowed) && (
                  <div className="relative">
                    <button
                      onClick={() => setShowNodeActions(v => !v)}
                      disabled={nodeActionLoading}
                      className="rounded bg-gray-800 px-2 py-1 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                      title="Step actions"
                    >
                      More ▾
                    </button>
                    {showNodeActions && (
                      <div className="absolute right-0 z-50 mt-1 max-h-[70vh] w-64 overflow-y-auto rounded border border-gray-700 bg-gray-900 shadow-xl">
                        {selectedNode.task_id && nodeReadActionsAllowed && (
                          <>
                            <button
                              onClick={() => { setShowNodeActions(false); downloadAuditLogs(selectedNode.task_id!) }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                              📥 Download Execution Logs
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); downloadAuditSummary(selectedNode.task_id!) }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                              📋 Download Summary
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); examineAndLearnSkill(selectedNode.task_id!, selectedNode.node_id) }}
                              disabled={nodeActionLoading || miningSkill}
                              className="w-full px-3 py-2 text-left text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🧠 Examine Logs → Learn Skill
                            </button>
                            {(selectedNode as any).node_type === 'block' && dagEditable && (
                              <button
                                onClick={() => { setShowNodeActions(false); openBlockEditor() }}
                                disabled={nodeActionLoading}
                                className="w-full px-3 py-2 text-left text-xs text-cyan-300 hover:bg-gray-800 disabled:opacity-50"
                              >
                                🔧 Change Block / Inputs
                              </button>
                            )}
                            {selectedNode.status === 'completed' && (
                              <button
                                onClick={() => { setShowNodeActions(false); promoteRunToBlock(selectedNode.task_id!) }}
                                disabled={nodeActionLoading || blockPromoting}
                                className="w-full px-3 py-2 text-left text-xs text-emerald-300 hover:bg-gray-800 disabled:opacity-50"
                                title="Promote this successful run's .py driver deliverables into a draft Block"
                              >
                                🧩 Learn Block from This Run
                              </button>
                            )}
                            {selectedNode.status === 'completed' && (
                              <button
                                onClick={() => { setShowNodeActions(false); promoteRunToDriver(selectedNode.task_id!) }}
                                disabled={nodeActionLoading || driverPromoting}
                                className="w-full px-3 py-2 text-left text-xs text-cyan-300 hover:bg-gray-800 disabled:opacity-50"
                                title="Promote this successful run's .py driver deliverables into a draft code skill"
                              >
                                🧪 Learn Code Driver from This Run
                              </button>
                            )}
                            <button
                              onClick={() => { setShowNodeActions(false); deepReviewTask(selectedNode.task_id!, selectedNode.node_id) }}
                              disabled={nodeActionLoading || deepReviewLoading}
                              className="w-full px-3 py-2 text-left text-xs text-amber-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🔍 Deep Review (hallucination / synthetic data)
                            </button>
                            <div className="border-t border-gray-700 px-3 py-2">
                              <label className="flex cursor-pointer items-center gap-2">
                                <input
                                  type="checkbox"
                                  checked={includeSkillInReview}
                                  onChange={(e) => setIncludeSkillInReview(e.target.checked)}
                                  className="accent-amber-500"
                                />
                                <span className="text-xs text-gray-400">Include skill context in review</span>
                              </label>
                              <div className="mt-2">
                                <label className="mb-1 block text-[10px] text-gray-500">Skill format (learn / correct)</label>
                                <select
                                  value={skillFormat}
                                  onChange={(e) => setSkillFormat(e.target.value)}
                                  className="w-full rounded border border-gray-700 bg-gray-800 px-1.5 py-1 text-[11px] text-gray-300"
                                >
                                  <option value="pseudo-code">pseudo-code (algorithmic)</option>
                                  <option value="easy">easy (structured steps)</option>
                                  <option value="code">code (ready-to-run script in original language)</option>
                                </select>
                              </div>
                            </div>
                          </>
                        )}
                        {nodeWriteActionsAllowed && (
                          <>
                            <div className="border-t border-gray-700" />
                            <button
                              onClick={() => { setShowNodeActions(false); setShowEnhanceDialog(true) }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                              ✨ Enhance Step
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); deleteSelectedNode() }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-red-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🗑 Delete Step
                            </button>
                          </>
                        )}
                        {(nodeReadActionsAllowed || nodeWriteActionsAllowed) && (
                          <>
                            <div className="border-t border-gray-700" />
                            <button
                              onClick={() => { setShowNodeActions(false); setShowAddNodeDialog(true) }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              ➕ Add Step After
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); openEditConnectionsDialog() }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🔗 Edit Connections
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); openRenameDialog() }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-teal-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              ✏️ Rename
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); openChangeSkillDialog() }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-purple-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🎯 Change Skill
                            </button>
                            <button
                              onClick={() => { setShowNodeActions(false); setNodeImage(selectedNode.config?.base_image || 'openclaw'); setShowImageDialog(true) }}
                              disabled={nodeActionLoading}
                              className="w-full px-3 py-2 text-left text-xs text-indigo-300 hover:bg-gray-800 disabled:opacity-50"
                            >
                              🖼️ Change Image
                            </button>
                          </>
                        )}
                        {nodeWriteActionsAllowed && (
                          <>
                            {(dag.status === 'failed' || dag.status === 'completed' || dag.status === 'cancelled') && (
                              <button
                                onClick={() => { setShowNodeActions(false); retryFromSelectedNode() }}
                                disabled={nodeActionLoading}
                                className="w-full px-3 py-2 text-left text-xs text-emerald-300 hover:bg-gray-800 disabled:opacity-50"
                              >
                                ▶ Run From This Step
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
                <button
                  onClick={() => setInspectorMaximized((value) => !value)}
                  className="rounded px-2 py-1 text-[10px] text-gray-400 hover:bg-gray-800 hover:text-gray-200"
                  title={inspectorMaximized ? 'Restore split view' : 'Pop out the node inspector'}
                >
                  {inspectorMaximized ? '⤡ Restore' : '⤢ Pop-out'}
                </button>
                <button onClick={() => setSelectedNodeId(null)} className="text-gray-500 hover:text-gray-300">&times;</button>
              </div>
            </div>
          </div>

          {selectedNode.status === 'failed' && selectedFailureReason && (
            <div className="mb-3 p-4 rounded border border-red-700/60 bg-red-950/50">
              <div className="text-[11px] uppercase tracking-wide text-red-300 mb-1">Failure Reason</div>
              <div className="text-base font-medium text-red-100">{selectedFailureReason}</div>
            </div>
          )}

          {selectedNode.status === 'completed' && selectedDrFlagged && (
            <div className={`mb-3 flex flex-wrap items-center gap-3 rounded border px-3 py-2 text-xs ${drRed ? 'border-red-700/60 bg-red-950/50' : 'border-amber-700/60 bg-amber-950/40'}`}>
              <span className={`shrink-0 font-semibold ${drRed ? 'text-red-300' : 'text-amber-300'}`}>
                ⚠ Quality Warning:
              </span>
              <span
                className={`min-w-0 flex-1 truncate ${drRed ? 'text-red-100/90' : 'text-amber-100/90'}`}
                title={qualityWarningText}
              >
                {qualityWarningText}
              </span>
              <button
                onClick={() => setActiveTab('audit')}
                className={`shrink-0 underline underline-offset-2 ${drRed ? 'text-red-200 hover:text-red-100' : 'text-amber-200 hover:text-amber-100'}`}
              >
                View {selectedReviewIssues} Issue{selectedReviewIssues === 1 ? '' : 's'} in Audit ({selectedReviewIssues}) &rarr;
              </button>
            </div>
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

          {blockLearnResult && (() => {
            const blk = blockLearnResult.block
            const files: Record<string, string> = blk?.code?.files || {}
            const provenanceVals = Object.values(blockLearnResult.provenance || {}) as string[]
            const sources = provenanceVals.filter((v, i) => provenanceVals.indexOf(v) === i).join('\n')
            return (
              <div className="mb-3 rounded border border-emerald-700/40 bg-emerald-950/20 p-3 text-xs">
                <div className="text-xs uppercase tracking-wide text-emerald-300 mb-2 flex items-center justify-between">
                  <span>🧩 Learned Block — {blk?.name || blk?.id}</span>
                  <span className="text-emerald-300">{blk?.status}</span>
                </div>
                <div className="mb-2 text-gray-300">
                  Deterministic block draft derived from this run via LLM introspection.
                  {blockLearnResult.model && (
                    <div className="text-[10px] text-gray-400 font-mono mt-1">
                      model: <span className="text-cyan-300">{blockLearnResult.model}</span>
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-gray-500 mb-1">Files</div>
                    <div className="font-mono text-gray-300">{Object.keys(files).join(', ') || '—'}</div>
                    <div className="text-gray-500 mt-2 mb-1">inputs_schema</div>
                    <pre className="whitespace-pre-wrap text-gray-300 bg-gray-950/60 p-2 rounded">{JSON.stringify(blk?.inputs_schema || {}, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="text-gray-500 mb-1">Sources used</div>
                    <div className="text-gray-300 whitespace-pre-wrap">{sources || '—'}</div>
                    {blk?.description && <div className="text-gray-400 mt-2 whitespace-pre-wrap">{blk.description}</div>}
                  </div>
                </div>
                <div className="mt-2 flex items-center gap-3">
                  <Link href="/blocks" className="px-3 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white">
                    Test & Activate on Blocks page
                  </Link>
                  <button onClick={() => setBlockLearnResult(null)} className="text-gray-400 hover:text-gray-200">Dismiss</button>
                  <span className="text-gray-500">ID: {blk?.id}</span>
                </div>
              </div>
            )
          })()}

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
                {(['agent', 'decision', 'input', 'block'] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => {
                      setAddNodeType(t)
                      if (t === 'block') {
                        fetch(`${API}/api/blocks?status_filter=active`)
                          .then((r) => r.json())
                          .then((d) => setBlockLibrary(Array.isArray(d) ? d : []))
                          .catch(() => setBlockLibrary([]))
                      }
                    }}
                    className={`text-xs px-2 py-1 rounded border ${addNodeType === t ? 'border-indigo-400 text-indigo-200 bg-indigo-900/40' : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}
                  >
                    {t === 'agent' ? '🤖 Agent' : t === 'decision' ? '🛑 Decision' : t === 'input' ? '📥 Input' : '🧩 Block'}
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
              {addNodeType === 'block' && (
                <div className="mb-2">
                  <label className="block text-xs text-gray-400 mb-1">Select a deterministic block</label>
                  <select
                    value={addBlockId}
                    onChange={(e) => setAddBlockId(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
                  >
                    <option value="">— choose block —</option>
                    {blockLibrary.map((b: any) => (
                      <option key={b.id} value={b.id}>{b.id} · {b.entrypoint}</option>
                    ))}
                  </select>
                  {blockLibrary.length === 0 && (
                    <p className="text-[10px] text-gray-500 mb-2">No active blocks in the library. Register one on the Blocks page first.</p>
                  )}
                </div>
              )}
              <textarea
                value={addNodeDesc}
                onChange={(e) => setAddNodeDesc(e.target.value)}
                rows={2}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
                placeholder="Describe the new step..."
              />
              {addNodeType !== 'block' && (<>
              <label className="block text-xs text-gray-400 mb-1">Image</label>
              <select
                value={addNodeImage}
                onChange={(e) => setAddNodeImage(e.target.value)}
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-2"
              >
                {agentImageOptions.map(img => (
                  <option key={img} value={img}>{img}</option>
                ))}
              </select>
              </>)}
              <div className="flex justify-end gap-2">
                <button onClick={() => setShowAddNodeDialog(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Cancel</button>
                <button onClick={addNodeAfterSelected} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50">
                  {nodeActionLoading ? 'Adding...' : 'Add Step'}
                </button>
              </div>
            </div>
          )}

          {blockEditOpen && selectedNode && (
            <div className="fixed inset-0 bg-black/60 z-[70] flex items-center justify-center p-4" onClick={() => setBlockEditOpen(false)}>
              <div className="bg-[#12121a] border border-[#232333] rounded-lg p-4 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
                <div className="text-sm font-semibold text-white mb-3">🔧 Edit block step: {selectedNode.node_id}</div>
                <label className="block text-xs text-gray-400 mb-1">Block</label>
                <select value={blockEditId} onChange={(e) => setBlockEditId(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 mb-3">
                  <option value="">— choose block —</option>
                  {blockLibrary2.map((b: any) => <option key={b.id} value={b.id}>{b.id}</option>)}
                </select>
                <label className="block text-xs text-gray-400 mb-1">Inputs (JSON)</label>
                <textarea value={blockEditInputs} onChange={(e) => setBlockEditInputs(e.target.value)} rows={5}
                  className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 font-mono mb-3" />
                <div className="flex justify-end gap-2">
                  <button onClick={() => setBlockEditOpen(false)} className="px-3 py-1 text-xs rounded border border-gray-700 text-gray-300">Cancel</button>
                  <button onClick={saveBlockEditor} disabled={nodeActionLoading} className="px-3 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50">Save</button>
                </div>
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
                {agentImageOptions.map(img => (
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

          {/* Node data tabs */}
          <>
              <div className="mb-3 flex flex-wrap gap-2">
                {([
                  { id: 'deliverables' as const, label: '📄 Deliverables', count: deliverableCount },
                  { id: 'audit' as const, label: '⚠ Audit & Review', count: selectedReviewIssues },
                  { id: 'turns' as const, label: '⏱ Turns & Logs', count: turnCount },
                  { id: 'configuration' as const, label: '⚙ Config & Spec', count: null },
                ]).map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      activeTab === tab.id
                        ? 'border-blue-500 bg-gray-700 text-white'
                        : 'border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-200'
                    }`}
                  >
                    {tab.label}
                    {tab.count !== null ? ` (${tab.count})` : ''}
                  </button>
                ))}
              </div>

              {activeTab === 'configuration' && (
                <div className="space-y-3">
                  <div className="rounded border border-gray-700 bg-gray-900/30 p-3">
                    <div className="mb-2 text-[10px] uppercase tracking-wide text-gray-500">Model & Runtime</div>
                    <div className="flex flex-wrap items-center gap-3">
                      <label className="text-xs text-gray-500">Model</label>
                      <select
                        value={effectiveNodeModel}
                        onChange={(event) => changeNodeModel(event.target.value)}
                        disabled={modelChanging}
                        className="max-w-[220px] rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300"
                      >
                        {effectiveNodeModel && !modelOptions.includes(effectiveNodeModel) && (
                          <option value={effectiveNodeModel}>{effectiveNodeModel}</option>
                        )}
                        {modelOptions.map((model) => (
                          <option key={model} value={model}>{model}</option>
                        ))}
                      </select>
                      {modelChanging && <span className="text-[10px] text-gray-500">Saving…</span>}
                    </div>
                    <div className="mt-2 grid grid-cols-1 gap-1 text-xs text-gray-400 md:grid-cols-2">
                      <div>Image: <span className="font-mono text-gray-300">{selectedNode.config?.dag_image || selectedNode.config?.base_image || 'openclaw'}</span></div>
                      <div>Runtime: <span className="font-mono text-gray-300">{selectedNode.config?.dag_image ? 'custom build' : 'base image'}</span></div>
                      {selectedNode.container_id && (
                        <div className="md:col-span-2">Container: <span className="font-mono text-gray-300">{selectedNode.container_id}</span></div>
                      )}
                    </div>
                  </div>

                  {dag.template_params && dag.template_params.length > 0 && (
                    <div className="rounded border border-cyan-800/40 bg-cyan-950/20 p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <div className="text-[10px] uppercase tracking-wide text-cyan-300">Template Parameters</div>
                        <button onClick={() => setShowDagSpec(true)} className="text-[10px] text-cyan-300 hover:text-cyan-200">View run spec →</button>
                      </div>
                      <div className="grid grid-cols-1 gap-1 text-xs text-gray-400 md:grid-cols-2">
                        {dag.template_params.map((param) => (
                          <div key={param.key}>
                            <span className="text-gray-300">{param.label || param.key}</span>
                            <span className="text-gray-600"> · {param.type} · default {param.default ?? '—'}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {selectedNode.skill_selection_reason && (
                    <div className="rounded border border-blue-800/50 bg-blue-950/40 p-2 text-xs text-blue-300">
                      <span className="font-semibold text-blue-400">Skill rationale: </span>
                      {selectedNode.skill_selection_reason}
                    </div>
                  )}

                  {selectedNode.selected_skill_v2_id && skillInfo[selectedNode.selected_skill_v2_id] && (
                    <div className="rounded border border-blue-800/40 bg-blue-950/30 p-2 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-blue-300">
                          Skill: {skillInfo[selectedNode.selected_skill_v2_id].name || selectedNode.selected_skill_v2_id}
                        </span>
                        {skillInfo[selectedNode.selected_skill_v2_id].status && (
                          <span className="text-gray-500">{skillInfo[selectedNode.selected_skill_v2_id].status}</span>
                        )}
                        {typeof skillInfo[selectedNode.selected_skill_v2_id].usage_count === 'number' && (
                          <span className="text-gray-600">used {skillInfo[selectedNode.selected_skill_v2_id].usage_count}</span>
                        )}
                        {typeof skillInfo[selectedNode.selected_skill_v2_id].success_count === 'number' && (
                          <span className="text-gray-600">success {skillInfo[selectedNode.selected_skill_v2_id].success_count}</span>
                        )}
                        <Link href="/skill-studio" className="ml-auto text-indigo-400 hover:text-indigo-300">
                          Open Skill Studio →
                        </Link>
                      </div>
                      {skillInfo[selectedNode.selected_skill_v2_id].description && (
                        <p className="mt-1 text-blue-200/80">{skillInfo[selectedNode.selected_skill_v2_id].description}</p>
                      )}
                    </div>
                  )}

                  {selectedNode.skill_id && (
                    <div className="rounded border border-purple-800/40 bg-purple-950/20 p-2 text-xs text-purple-300">
                      Legacy skill: <span className="font-mono">{selectedNode.skill_id}</span>
                    </div>
                  )}

                  {selectedNode.config?.template_guidance && (
                    <details className="rounded border border-cyan-800/50 bg-cyan-950/40 p-2 text-xs text-cyan-200">
                      <summary className="cursor-pointer font-semibold text-cyan-300">📋 Previous run (follow-the-guidance reference)</summary>
                      <pre className="mt-1 whitespace-pre-wrap text-cyan-200/80">{selectedNode.config.template_guidance}</pre>
                    </details>
                  )}

          {/* Execution state continuity panel */}
          <div className="mb-3 rounded border border-cyan-700/40 bg-cyan-950/20">
            <button
              onClick={() => setShowExecutionState((value) => !value)}
              className="flex w-full items-center justify-between p-3 text-left"
            >
              <span className="text-xs uppercase tracking-wide text-cyan-300">Execution State</span>
              <span className="text-[10px] text-gray-500">
                {selectedNodeState?.latest
                  ? `${selectedNodeState.latest.phase} · ${selectedNodeState.latest.status}`
                  : selectedNodeState
                    ? 'No snapshot'
                    : 'Loading…'}{' '}
                {showExecutionState ? '▾' : '▸'}
              </span>
            </button>
            {showExecutionState && (
            <div className="px-3 pb-3">
            {!selectedNodeState ? (
              <div className="text-xs text-gray-500">Loading node state...</div>
            ) : !selectedNodeState.latest ? (
              <div className="text-xs text-gray-500">
                No captured state snapshot yet.{' '}
                {selectedTaskData?.currentState
                  ? `Task state: ${selectedTaskData.currentState.status} · ${selectedTaskData.outputs.length} iteration${selectedTaskData.outputs.length === 1 ? '' : 's'}. Use the Turns & Logs tab for details.`
                  : 'Use the Turns & Logs tab for this run.'}
              </div>
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
            )}
          </div>

                  <details className="rounded border border-gray-700 bg-gray-900/30 p-3 text-xs">
                    <summary className="cursor-pointer text-gray-400">Raw node output / state</summary>
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-[10px] text-gray-400">
{JSON.stringify({ config: selectedNode.config, input_mapping: selectedNode.input_mapping, output_data: selectedNode.output_data, latest_state: selectedNodeState?.latest || null }, null, 2)}
                    </pre>
                  </details>
                </div>
              )}

              {/* Audit tab */}
              {activeTab === 'audit' && (
                <AuditReviewTab
                  review={selectedReview}
                  nodeId={selectedNode.node_id}
                  taskId={selectedNode.task_id || null}
                  dagId={dagId}
                  deepReviewLoading={deepReviewLoading}
                  correctSkillLoading={correctSkillLoading}
                  correctSkillResult={correctSkillResult}
                  workspaceFiles={workspaceTree || []}
                  onRunReview={() => {
                    if (selectedNode.task_id) deepReviewTask(selectedNode.task_id, selectedNode.node_id)
                  }}
                  onApplyFixes={() => {
                    if (selectedNode.task_id) correctSkillFromReview(selectedNode.task_id, selectedNode.node_id)
                  }}
                  onOpenFile={(path) => {
                    setActiveTab('deliverables')
                    void openWorkspaceFile(path)
                  }}
                />
              )}

              {nodeDataLoading && !selectedTaskData && activeTab === 'turns' && (
                <div className="text-gray-500 text-sm py-4 text-center animate-pulse">Loading task data...</div>
              )}

              {activeTab === 'turns' && !selectedNode.task_id && (
                <div className="text-gray-500 text-sm py-3">
                  This node has not started yet &mdash; no task data available.
                </div>
              )}

              {selectedTaskData && (
                <>
                  {/* Turns: execution summary */}
                  {activeTab === 'turns' && (
                    <div className="mb-4">
                      <div className="mb-2 text-[10px] uppercase tracking-wide text-gray-500">Execution Summary</div>
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

                  {/* Turns: iteration outputs */}
                  {activeTab === 'turns' && (
                    <div className="mb-4">
                      <div className="mb-2 text-[10px] uppercase tracking-wide text-gray-500">Iteration Outputs</div>
                      <div className="max-h-96 space-y-2 overflow-y-auto">
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
                    </div>
                  )}

                  {/* Turns: logs */}
                  {activeTab === 'turns' && (
                    <div>
                      <div className="mb-2 text-[10px] uppercase tracking-wide text-gray-500">Agent Logs</div>
                      {selectedTaskData.outputs.length === 0 ? (
                        <div className="text-gray-500 text-sm py-4">No iterations captured.</div>
                      ) : (() => {
                        const outputs = selectedTaskData.outputs
                        const index = Math.max(0, Math.min(logIteration ?? outputs.length - 1, outputs.length - 1))
                        const output = outputs[index]
                        return (
                          <div>
                            <div className="flex flex-wrap items-center gap-2 mb-2 text-xs">
                              <select
                                value={index}
                                onChange={(event) => setLogIteration(Number(event.target.value))}
                                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-300"
                              >
                                {outputs.map((item, i) => (
                                  <option key={`${item.id}-${i}`} value={i}>
                                    Iteration {item.iteration} · {item.model_used || 'model?'}
                                  </option>
                                ))}
                              </select>
                              <span className="text-gray-500">
                                {output.model_used || ''} · {output.duration_ms != null ? `${output.duration_ms}ms` : ''} · {output.completed === 'true' ? 'completed' : output.completed}
                              </span>
                              <a
                                href={`${API}/api/tasks/${selectedNode.task_id}/audit-logs/export`}
                                className="ml-auto text-blue-400 hover:text-blue-300"
                              >
                                Download logs
                              </a>
                            </div>
                            {output.error && (
                              <div className="mb-2 rounded border border-red-800/40 bg-red-950/30 p-2 text-xs text-red-300 whitespace-pre-wrap">
                                {output.error}
                              </div>
                            )}
                            <pre className="max-h-[55vh] overflow-auto text-[10px] text-gray-300 bg-[#0d0d14] border border-[#1a1a2a] rounded p-2 whitespace-pre-wrap break-words">
                              {output.agent_logs || 'No agent logs captured for this iteration.'}
                            </pre>
                          </div>
                        )
                      })()}
                    </div>
                  )}

                  {/* Turns: LLM audit trail */}
                  {activeTab === 'turns' && (
                    <details className="mt-4 rounded border border-gray-700 bg-gray-900/30">
                      <summary className="cursor-pointer px-3 py-2 text-xs text-gray-300">
                        LLM Audit Trail {selectedTaskData.auditTurns ? `(${selectedTaskData.auditTurns.total_turns} calls)` : ''}
                      </summary>
                      <div className="border-t border-gray-700 p-3">
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
                    </details>
                  )}

                      {/* Config: SBOM */}
                      {activeTab === 'configuration' && (
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

          </>

           {/* Deliverables explorer */}
           {selectedNode && activeTab === 'deliverables' && (
             <div className="mb-3 rounded border border-blue-500/30 bg-[#12121a] p-3">
               <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                 <div className="text-[10px] uppercase tracking-wide text-cyan-300">
                   GENERATED FILES
                 </div>
                 <label className="flex items-center gap-1 text-[10px] text-gray-500">
                   <input
                     type="checkbox"
                     checked={showFullWorkspace}
                     onChange={(event) => setShowFullWorkspace(event.target.checked)}
                   />
                   Show entire DAG workspace
                 </label>
               </div>
               <div className="flex flex-col gap-3 xl:flex-row">
                 <div className="min-w-[200px] shrink-0 rounded border border-gray-800 bg-gray-950/30 p-1.5 xl:w-[30%]">
                   {workspaceTreeLoading && <div className="p-2 text-[10px] text-gray-500">Loading workspace…</div>}
                   {!workspaceTreeLoading && (
                     <>
                       <details open className="mb-1">
                         <summary className="cursor-pointer px-1 py-1 text-[10px] uppercase tracking-wide text-cyan-300">
                           Key Deliverables ({activeGroups.key.length})
                         </summary>
                         <div className="space-y-0.5">
                           {activeGroups.key.map(explorerFileRow)}
                           {activeGroups.key.length === 0 && (
                             <div className="px-1 pb-1 text-[10px] text-gray-600">No key deliverables.</div>
                           )}
                         </div>
                       </details>

                       <details open className="mb-1">
                         <summary className="cursor-pointer px-1 py-1 text-[10px] uppercase tracking-wide text-cyan-300">
                           Scripts &amp; Execution Logs ({activeGroups.scripts.length})
                         </summary>
                         <div className="space-y-0.5">
                           {activeGroups.scripts.map(explorerFileRow)}
                           {activeGroups.scripts.length === 0 && (
                             <div className="px-1 pb-1 text-[10px] text-gray-600">No scripts or logs.</div>
                           )}
                         </div>
                       </details>

                       {activeGroups.other.length > 0 && (
                         <details className="mb-1">
                           <summary className="cursor-pointer px-1 py-1 text-[10px] uppercase tracking-wide text-gray-500">
                             Other Files ({activeGroups.other.length})
                           </summary>
                           <div className="space-y-0.5">
                             {activeGroups.other.map(explorerFileRow)}
                           </div>
                         </details>
                       )}

                       {!showFullWorkspace && workspaceInputFiles.length > 0 && (
                         <details className="mb-1">
                           <summary className="cursor-pointer px-1 py-1 text-[10px] uppercase tracking-wide text-gray-500">
                             Upstream Inputs ({workspaceInputFiles.length})
                           </summary>
                           <div className="space-y-0.5">
                             {workspaceInputFiles.map(explorerFileRow)}
                           </div>
                         </details>
                       )}

                       {!showFullWorkspace && (
                         <details className="mb-1">
                           <summary className="cursor-pointer px-1 py-1 text-[10px] uppercase tracking-wide text-gray-500">
                             Attachments ({(workspaceManifest?.attachments || []).length || workspaceAttachmentFiles.length})
                           </summary>
                           <div className="space-y-0.5">
                             {(workspaceManifest?.attachments || []).length > 0 ? (
                               (workspaceManifest?.attachments || []).map((att) => (
                                 <a
                                   key={att.stored_as}
                                   href={`${API}/api/dags/${dagId}/attachments/${encodeURIComponent(att.stored_as)}`}
                                   className="flex items-center gap-2 rounded px-1.5 py-1 text-[11px] text-cyan-300 hover:bg-gray-800"
                                   title={`${att.mime} · ${formatAttachmentBytes(att.size)}`}
                                 >
                                   <span>📎</span>
                                   <span className="truncate font-mono">{att.name}</span>
                                   <span className="ml-auto text-[9px] text-gray-600">{formatAttachmentBytes(att.size)}</span>
                                 </a>
                               ))
                             ) : workspaceAttachmentFiles.length > 0 ? (
                               workspaceAttachmentFiles.map(explorerFileRow)
                             ) : (
                               <div className="px-1 pb-1 text-[10px] text-gray-600">No attachments.</div>
                             )}
                           </div>
                         </details>
                       )}

                       {!showFullWorkspace && workspaceOwnFiles.length === 0 && (
                         <div className="px-1 py-1 text-[10px] text-gray-500">
                           No files captured for this step yet. Toggle “Show entire DAG workspace” to browse everything.
                         </div>
                       )}
                       {showFullWorkspace && workspaceTree?.length === 0 && (
                         <div className="px-1 py-1 text-[10px] text-gray-500">No files in the workspace.</div>
                       )}
                     </>
                   )}
                 </div>

                 <div className="flex min-w-0 flex-1 flex-col rounded border border-gray-800">
                   <div className="flex flex-wrap items-center gap-2 border-b border-gray-800 px-3 py-2">
                     <span className="min-w-0 truncate text-[10px] uppercase tracking-wide text-gray-500">
                       PREVIEW: <span className="font-mono text-gray-300">{workspacePreview ? (workspacePreview.path.split('/').pop() || workspacePreview.path) : '—'}</span>
                     </span>
                      <div className="ml-auto flex items-center gap-1.5">
                        {selectedIsMarkdown ? (
                          <>
                            <span className="text-[10px] text-gray-500">Mode:</span>
                            <select
                              value={previewMode}
                              onChange={(event) => setPreviewMode(event.target.value as 'rendered' | 'raw')}
                              className="rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300"
                            >
                              <option value="rendered">Rendered</option>
                              <option value="raw">Raw</option>
                            </select>
                          </>
                        ) : (
                          workspacePreview && !workspacePreview.isImage && !selectedIsImageFile && !selectedIsFrame ? (
                            <span className="text-[10px] text-gray-500">Mode: <span className="text-gray-400">Raw</span></span>
                          ) : null
                        )}
                       <button
                         onClick={copyWorkspacePreview}
                         disabled={!workspacePreview || workspacePreview.isImage || selectedIsFrame}
                         className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300 hover:text-white disabled:opacity-40"
                         title="Copy file contents"
                       >
                         {previewCopied ? '✓ Copied' : '⎘ Copy'}
                       </button>
                       {workspacePreview ? (
                         <a
                           href={`${API}/api/dags/${dagId}/workspace/files/${workspacePreview.path
                             .split('/')
                             .map((part) => encodeURIComponent(part))
                             .join('/')}?inline=0`}
                           className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-blue-300 hover:text-blue-200"
                         >
                           ⭳ Download
                         </a>
                       ) : (
                         <span className="rounded border border-gray-800 px-1.5 py-0.5 text-[10px] text-gray-600">⭳ Download</span>
                       )}
                     </div>
                   </div>

                   <div className="min-h-[18rem] max-h-[55vh] flex-1 overflow-auto bg-[#0d1117]">
                     {!workspacePreview ? (
                       <div className="flex h-full items-center justify-center p-6 text-xs text-gray-500">
                         Select a file to preview it here.
                       </div>
                     ) : workspacePreview.isImage || selectedIsImageFile ? (
                       <div className="p-3">
                         <img
                           src={workspacePreviewUrl}
                           alt={workspacePreview.path}
                           className="max-h-[48vh] max-w-full rounded border border-gray-800"
                         />
                       </div>
                     ) : selectedIsFrame ? (
                       <iframe src={workspacePreviewUrl} className="h-[50vh] w-full" title={`Preview: ${workspacePreview.path}`} />
                     ) : selectedIsMarkdown && previewMode === 'rendered' ? (
                       <div className="p-4">
                         <RenderedMarkdown content={workspacePreview.content} />
                       </div>
                     ) : (
                       <pre className="m-0 whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed text-gray-300">
                         {workspacePreview.content}
                       </pre>
                     )}
                   </div>

                   <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-gray-800 px-3 py-1.5 text-[10px] text-gray-500">
                     <span className="min-w-0 truncate">
                       Active: <span className="font-mono text-gray-400">{workspacePreview?.path || '—'}</span>
                       {selectedPreviewEntry ? ` (${formatAttachmentBytes(selectedPreviewEntry.size)})` : ''}
                     </span>
                     <span className={`ml-auto ${verificationClass}`}>Deliverable Health: {verificationLabel}</span>
                   </div>
                 </div>
               </div>
             </div>
           )}
          </div>
        )}
      </div>

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

      {/* DAG specification modal */}
      {showDagSpec && (
        <div className="fixed inset-0 z-[65] flex items-center justify-center p-4" onClick={() => setShowDagSpec(false)}>
          <div className="absolute inset-0 bg-black/60" />
          <div
            className="relative flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-[#232333] bg-[#12121a]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-[#232333] px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-white">DAG Specification</div>
                <div className="font-mono text-[10px] text-gray-500">{dag.id}</div>
              </div>
              <button onClick={() => setShowDagSpec(false)} className="text-gray-500 hover:text-gray-200">✕</button>
            </div>
            <div className="space-y-4 overflow-y-auto p-4 text-xs text-gray-300">
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Objective</div>
                <p className="whitespace-pre-wrap text-gray-300">{dag.objective}</p>
              </div>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                <div className="rounded bg-gray-900/60 p-2">
                  <div className="text-[10px] text-gray-500">Status</div>
                  <div className="font-mono">{dag.status}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <div className="text-[10px] text-gray-500">Model</div>
                  <div className="font-mono">{dag.llm_model || '—'}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <div className="text-[10px] text-gray-500">Nodes</div>
                  <div className="font-mono">{dag.nodes.length}</div>
                </div>
                <div className="rounded bg-gray-900/60 p-2">
                  <div className="text-[10px] text-gray-500">Locked</div>
                  <div className="font-mono">{dag.locked ? 'template' : 'no'}</div>
                </div>
              </div>
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Steps</div>
                <div className="overflow-x-auto rounded border border-gray-800">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-gray-800 text-left text-gray-500">
                        <th className="px-2 py-1">Node</th>
                        <th className="px-2 py-1">Depends on</th>
                        <th className="px-2 py-1">Model</th>
                        <th className="px-2 py-1">Image</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dag.nodes.map((node) => (
                        <tr key={node.node_id} className="border-b border-gray-900 align-top">
                          <td className="px-2 py-1 font-mono text-gray-300">{node.node_id}</td>
                          <td className="px-2 py-1 font-mono text-gray-500">{node.depends_on.join(', ') || '—'}</td>
                          <td className="px-2 py-1 font-mono text-gray-400">{node.config?.llm_model || dag.llm_model || 'default'}</td>
                          <td className="px-2 py-1 font-mono text-gray-400">{node.config?.dag_image || node.config?.base_image || 'openclaw'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <details>
                <summary className="cursor-pointer text-[10px] uppercase tracking-wide text-gray-500">Raw dag_json</summary>
                <pre className="mt-2 max-h-72 overflow-auto rounded bg-[#0d1117] p-2 text-[10px] text-gray-400">
{JSON.stringify(dag.dag_json || {}, null, 2)}
                </pre>
              </details>
            </div>
          </div>
        </div>
      )}

      {/* Deliverable preview drawer */}
      {previewFile && (
        <div className="fixed inset-0 z-[60] flex justify-end">
          <div className="absolute inset-0 bg-black/50" onClick={() => setPreviewFile(null)} />
          <div className="relative w-[640px] max-w-[85vw] h-full bg-[#0e0e18] border-l border-[#1a1a2a] shadow-2xl flex flex-col overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a2a]">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm text-gray-300">📄</span>
                <span className="text-sm font-mono text-white truncate">{previewFile.filename}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  href={previewFile.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-2 py-1 text-xs rounded border border-gray-700 text-gray-300 hover:text-white"
                  title="Open in new tab"
                >
                  ↗ Open
                </a>
                <button
                  onClick={() => setPreviewFile(null)}
                  className="text-gray-500 hover:text-gray-200 p-1 rounded hover:bg-[#1c1c28]"
                  title="Close preview"
                >
                  ✕
                </button>
              </div>
            </div>
            <div className="flex-1 bg-[#0d1117] min-h-0 overflow-hidden">
              <DeliverablePreview url={previewFile.url} filename={previewFile.filename} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
