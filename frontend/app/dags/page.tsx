'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { API } from '../lib/api'
import { useProject } from '../lib/ProjectContext'
import WorkflowTableRow from '../components/WorkflowTableRow'
import MergeWorkflowDialog from '../components/MergeWorkflowDialog'
import TemplateRunDialog, { type TemplateDag } from '../components/TemplateRunDialog'
import {
  type WorkflowStage,
  type WorkflowSummary,
} from '../lib/workflows'

interface DAGRecord extends TemplateDag {
  locked: boolean
  archived: boolean
  status: string
}

const STAGE_ORDER: WorkflowStage[] = ['hardened', 'locked', 'experimenting']
type SortMode = 'active' | 'name' | 'stage' | 'score'

export default function WorkflowStudioPage() {
  const router = useRouter()
  const { activeProject, projects } = useProject()
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([])
  const [dags, setDags] = useState<DAGRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [stage, setStage] = useState<'all' | 'exploration' | WorkflowStage>('all')
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [importJson, setImportJson] = useState('')
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const [template, setTemplate] = useState<DAGRecord | null>(null)
  const [mergeTarget, setMergeTarget] = useState<WorkflowSummary | null>(null)
  const [focusWorkflow, setFocusWorkflow] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState<SortMode>('active')
  const [expandedWorkflowId, setExpandedWorkflowId] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const projectNameById = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  )

  const fetchWorkflows = async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams({
        archived: String(showArchived),
        limit: '200',
      })
      if (activeProject) qs.set('project_id', activeProject)
      const dagQs = new URLSearchParams({
        archived: String(showArchived),
        limit: '200',
      })
      if (activeProject) dagQs.set('project_id', activeProject)
      const [workflowRes, dagRes] = await Promise.all([
        fetch(`${API}/api/workflows?${qs}`),
        fetch(`${API}/api/dags?${dagQs}`),
      ])
      const workflowData = await workflowRes.json().catch(() => [])
      const dagData = await dagRes.json().catch(() => [])
      if (!workflowRes.ok) throw new Error(`HTTP ${workflowRes.status}`)
      setWorkflows(Array.isArray(workflowData) ? workflowData : [])
      setDags(Array.isArray(dagData) ? dagData : [])
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchWorkflows()
  }, [showArchived, activeProject])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const stageParam = params.get('stage')
    if (stageParam === 'exploration' || stageParam === 'experimenting' || stageParam === 'locked' || stageParam === 'hardened') {
      setStage(stageParam)
    }
    const workflowParam = params.get('workflow')
    if (workflowParam) {
      setStage('all')
      setSearch('')
      setFocusWorkflow(workflowParam)
    }
  }, [])

  useEffect(() => {
    if (!focusWorkflow || loading) return
    const target = document.getElementById(`workflow-${focusWorkflow}`)
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setFocusWorkflow(null)
    }
  }, [focusWorkflow, loading, workflows])

  const counts = useMemo(() => {
    const nonExploration = workflows.filter((group) => group.mode !== 'exploration')
    return {
      all: workflows.length,
      explorations: workflows.filter((group) => group.mode === 'exploration').length,
      experimenting: nonExploration.filter((group) => group.stage === 'experimenting').length,
      locked: nonExploration.filter((group) => group.stage === 'locked').length,
      hardened: nonExploration.filter((group) => group.stage === 'hardened').length,
    }
  }, [workflows])

  const determinism = counts.hardened > 0
    ? Math.round(
        workflows
          .filter((group) => group.mode !== 'exploration' && group.stage === 'hardened')
          .reduce((sum, group) => sum + group.determinism_pct, 0) / counts.hardened,
      )
    : 0

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return workflows.filter((group) => {
      if (stage === 'exploration') {
        if (group.mode !== 'exploration') return false
      } else if (stage !== 'all') {
        if (group.mode === 'exploration' || group.stage !== stage) return false
      }
      if (!term) return true
      return (
        group.title.toLowerCase().includes(term) ||
        group.objective.toLowerCase().includes(term) ||
        (group.latest_run?.id || '').toLowerCase().includes(term)
      )
    })
  }, [workflows, stage, search])

  const sorted = useMemo(() => {
    const list = [...filtered]
    if (sortBy === 'name') {
      list.sort((a, b) => a.title.localeCompare(b.title))
    } else if (sortBy === 'stage') {
      list.sort((a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage))
    } else if (sortBy === 'score') {
      list.sort((a, b) => (b.latest_score ?? -1) - (a.latest_score ?? -1))
    } else {
      list.sort(
        (a, b) =>
          new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime(),
      )
    }
    return list
  }, [filtered, sortBy])

  const openLatest = (group: WorkflowSummary) => {
    if (group.latest_run?.id) router.push(`/dags/${group.latest_run.id}`)
  }

  const openTemplate = (group: WorkflowSummary) => {
    const lockedTemplate = dags.find(
      (dag) => dag.locked && dag.workflow_group_id === group.id,
    )
    if (lockedTemplate) router.push(`/dags/${lockedTemplate.id}`)
    else openLatest(group)
  }

  const runWorkflow = (group: WorkflowSummary) => {
    if (!group.latest_run?.id) return
    if (group.locked) {
      const lockedTemplate = dags.find(
        (dag) => dag.locked && dag.workflow_group_id === group.id,
      )
      if (lockedTemplate) {
        setTemplate(lockedTemplate)
        return
      }
    }
    router.push(`/dags/${group.latest_run.id}`)
  }

  const convertStep = (group: WorkflowSummary) => {
    if (!group.latest_run?.id) return
    const agent = group.latest_nodes.find((node) => node.node_type === 'agent')
    router.push(`/dags/${group.latest_run.id}${agent ? `?node=${encodeURIComponent(agent.node_id)}` : ''}`)
  }

  const rerunFailed = (group: WorkflowSummary) => {
    if (!group.latest_run?.id) return
    router.push(`/dags/${group.latest_run.id}${group.latest_active_node_id ? `?node=${encodeURIComponent(group.latest_active_node_id)}` : ''}`)
  }

  const archiveRun = async (dagId: string) => {
    if (!window.confirm(`Archive run ${dagId}? It will be hidden from the active workflow list.`)) return
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/archive`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      fetchWorkflows()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const deleteRun = async (dagId: string) => {
    if (!window.confirm(`Permanently delete run ${dagId} and all its data? This cannot be undone.`)) return
    try {
      const res = await fetch(`${API}/api/dags/${dagId}`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error((data as any).detail || `HTTP ${res.status}`)
      }
      fetchWorkflows()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const importManualDag = async () => {
    setImporting(true)
    setImportMsg(null)
    try {
      const doc = JSON.parse(importJson)
      const nodes = Array.isArray(doc.nodes) ? doc.nodes : []
      if (!nodes.length) throw new Error('JSON must contain a "nodes" array')
      const res = await fetch(`${API}/api/dags/manual`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          objective: doc.objective || 'manual dag',
          default_image: doc.default_image || 'openclaw',
          default_llm: doc.default_llm || 'deepseek-v4-flash',
          nodes,
          edges: Array.isArray(doc.edges) ? doc.edges : [],
          project_id: activeProject || undefined,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setImportMsg(`❌ ${(data as any).detail || res.status}`)
        return
      }
      setImportMsg(`✅ Created ${(data as any).id}.`)
      setImportJson('')
      fetchWorkflows()
    } catch (err: any) {
      setImportMsg(`❌ ${err.message}`)
    } finally {
      setImporting(false)
    }
  }

  const filterButtons: { id: 'all' | 'exploration' | WorkflowStage; label: string; count: number }[] = [
    { id: 'all', label: 'All Workflows', count: counts.all },
    { id: 'exploration', label: '🧭 Explorations', count: counts.explorations },
    { id: 'experimenting', label: '🧪 Experiments', count: counts.experimenting },
    { id: 'locked', label: '🔒 Templates', count: counts.locked },
    { id: 'hardened', label: '⚡ Hardened', count: counts.hardened },
  ]

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Workflows &amp; Pipelines</h1>
          <p className="mt-1 max-w-3xl text-xs text-gray-500">
            Manage workflow reliability: iterate with AI → lock into templates → convert steps to deterministic blocks.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={searchRef}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search workflows... (⌘K)"
            className="w-64 rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-gray-300 placeholder:text-gray-600"
          />
          <Link href="/?new=1" className="btn-primary text-sm">
            + New Workflow
          </Link>
          <div className="relative">
            <button
              onClick={() => setShowMore((value) => !value)}
              className="rounded border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:border-gray-500"
            >
              More ▾
            </button>
            {showMore && (
              <div className="absolute right-0 z-40 mt-1 w-56 rounded border border-gray-700 bg-gray-900 shadow-xl">
                <Link href="/template-skills" className="block px-3 py-2 text-left text-xs text-purple-300 hover:bg-gray-800">
                  🧩 Template Skills
                </Link>
                <Link href="/blocks" className="block px-3 py-2 text-left text-xs text-cyan-300 hover:bg-gray-800">
                  ⚡ Blocks Library
                </Link>
                <div className="border-t border-gray-800" />
                <button
                  onClick={() => { setShowMore(false); setShowArchived((value) => !value) }}
                  className="block w-full px-3 py-2 text-left text-xs text-gray-300 hover:bg-gray-800"
                >
                  {showArchived ? '📁 Show active workflows' : '📁 Show archived workflows'}
                </button>
                <button
                  onClick={() => { setShowMore(false); setShowImport((value) => !value) }}
                  className="block w-full px-3 py-2 text-left text-xs text-cyan-300 hover:bg-gray-800"
                >
                  ⚙ Import Manual (Deterministic) DAG
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">
        <span className="font-medium text-gray-300">{counts.all} Total</span>
        <span className="text-gray-700">•</span>
        <span><span className="text-cyan-300">{counts.explorations}</span> 🧭 Explorations</span>
        <span className="text-gray-700">•</span>
        <span><span className="text-amber-300">{counts.experimenting}</span> 🧪 Experiments</span>
        <span className="text-gray-700">•</span>
        <span><span className="text-purple-300">{counts.locked}</span> 🔒 Templates</span>
        <span className="text-gray-700">•</span>
        <span><span className="text-emerald-300">{counts.hardened}</span> ⚡ Hardened ({determinism}% deterministic)</span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {filterButtons.map((button) => (
          <button
            key={button.id}
            onClick={() => setStage(button.id)}
            className={`rounded border px-2.5 py-1 text-[10px] ${
              stage === button.id
                ? 'border-blue-500 bg-gray-700 text-white'
                : 'border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-200'
            }`}
          >
            {button.label} ({button.count})
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 text-[10px] text-gray-500">
          <span>Sort:</span>
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as SortMode)}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-gray-300"
          >
            <option value="active">Last Active</option>
            <option value="name">Name</option>
            <option value="stage">Stage</option>
            <option value="score">Latest Score</option>
          </select>
        </div>
      </div>

      {error && <div className="mb-3 rounded border border-red-500/30 bg-red-900/20 p-3 text-xs text-red-300">{error}</div>}

      {showImport && (
        <div className="card mb-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold text-white">Import a manual DAG (no LLM planner)</div>
            <button onClick={() => setShowImport(false)} className="text-xs text-gray-500 hover:text-gray-300">✕</button>
          </div>
          <p className="text-[11px] text-gray-500">
            Use <code className="text-cyan-300">&quot;node_type&quot;: &quot;block&quot;</code> with{' '}
            <code className="text-cyan-300">config.block</code> for deterministic steps.
          </p>
          <textarea
            value={importJson}
            onChange={(event) => setImportJson(event.target.value)}
            rows={8}
            spellCheck={false}
            className="w-full rounded border border-gray-700 bg-gray-900 p-2 font-mono text-xs text-gray-200"
            placeholder={'{"objective":"...","nodes":[{"node_id":"b1","node_type":"block","depends_on":[],"config":{"type":"block","block":"echo","inputs":{"stage":1}},"input_mapping":{}}],"edges":[]}'}
          />
          <div className="flex items-center gap-3">
            <button onClick={importManualDag} disabled={importing} className="btn-success text-sm disabled:opacity-50">
              {importing ? 'Importing...' : 'Import & validate'}
            </button>
            {importMsg && <span className="text-xs text-gray-300">{importMsg}</span>}
          </div>
        </div>
      )}

      {loading ? (
        <div className="overflow-hidden rounded-lg border border-gray-800">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="h-12 animate-pulse border-b border-gray-800 bg-gray-900/40" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-700 p-12 text-center text-sm text-gray-500">
          {showArchived
            ? 'No archived workflows.'
            : workflows.length === 0
              ? 'No workflows yet. Start with a new workflow.'
              : 'No workflows match this filter.'}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full min-w-[980px] border-collapse">
            <thead className="bg-[#15151f] text-left text-[9px] uppercase tracking-wide text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium">Workflow name &amp; objective</th>
                <th className="px-3 py-2 font-medium">Maturity stage</th>
                <th className="px-3 py-2 font-medium">Latest run</th>
                <th className="px-3 py-2 font-medium">History (last 5)</th>
                <th className="px-3 py-2 font-medium">Executions</th>
                <th className="px-3 py-2 font-medium">Last active</th>
                <th className="px-3 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((group) => (
                <WorkflowTableRow
                  key={group.id}
                  group={group}
                  projectName={group.project_id ? projectNameById.get(group.project_id) : undefined}
                  expanded={expandedWorkflowId === group.id}
                  onToggleExpand={() => setExpandedWorkflowId((current) => current === group.id ? null : group.id)}
                  onRun={runWorkflow}
                  onResume={rerunFailed}
                  onOpenLatest={openLatest}
                  onOpenTemplate={openTemplate}
                  onRerunProblem={rerunFailed}
                  onConvert={convertStep}
                  onMerge={setMergeTarget}
                  onArchiveRun={archiveRun}
                  onDeleteRun={deleteRun}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {template && (
        <TemplateRunDialog
          template={template}
          onClose={() => setTemplate(null)}
          onCreated={(dagId) => router.push(`/dags/${dagId}`)}
        />
      )}

      {mergeTarget && (
        <MergeWorkflowDialog
          target={mergeTarget}
          groups={workflows}
          onClose={() => setMergeTarget(null)}
          onMerged={() => {
            setMergeTarget(null)
            fetchWorkflows()
          }}
        />
      )}
    </div>
  )
}
