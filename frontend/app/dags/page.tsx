'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { API } from '../lib/api'
import { useProject } from '../lib/ProjectContext'
import ExplorationTableRow from '../components/ExplorationTableRow'
import WorkflowTableRow from '../components/WorkflowTableRow'
import MergeWorkflowDialog from '../components/MergeWorkflowDialog'
import TemplateRunDialog, { type TemplateDag } from '../components/TemplateRunDialog'
import {
  type WorkflowStage,
  type WorkflowSummary,
} from '../lib/workflows'
import { type ExplorationSession } from '../lib/explorations'

interface DAGRecord extends TemplateDag {
  locked: boolean
  archived: boolean
  status: string
}

interface ExplorationRow {
  session: ExplorationSession
  group: WorkflowSummary | null
}

type SectionId = 'explorations' | 'workflows' | 'templates'
type SortMode = 'active' | 'name' | 'score'
type FocusStage = 'exploration' | WorkflowStage

function groupMatches(group: WorkflowSummary, term: string, projectName?: string): boolean {
  if (!term) return true
  return (
    group.title.toLowerCase().includes(term) ||
    group.objective.toLowerCase().includes(term) ||
    group.id.toLowerCase().includes(term) ||
    (group.latest_run?.id || '').toLowerCase().includes(term) ||
    (projectName || '').toLowerCase().includes(term)
  )
}

function sortGroups(list: WorkflowSummary[], sortBy: SortMode): WorkflowSummary[] {
  const sorted = [...list]
  if (sortBy === 'name') {
    sorted.sort((a, b) => a.title.localeCompare(b.title))
  } else if (sortBy === 'score') {
    sorted.sort((a, b) => (b.latest_score ?? -1) - (a.latest_score ?? -1))
  } else {
    sorted.sort(
      (a, b) =>
        new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime(),
    )
  }
  return sorted
}

function SectionHeader({
  number,
  icon,
  title,
  total,
  matched,
  searchActive,
  description,
  collapsed,
  onToggle,
}: {
  number: number
  icon: string
  title: string
  total: number
  matched: number
  searchActive: boolean
  description: string
  collapsed: boolean
  onToggle: () => void
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-gray-800 bg-[#15151f] px-3 py-2.5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] font-medium text-gray-600">{number}.</span>
          <span className="text-sm">{icon}</span>
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-gray-200">
            {title} <span className="font-normal text-gray-500">({total})</span>
          </h2>
          {searchActive && matched !== total && (
            <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-300">
              {matched} matches
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[10px] text-gray-500">{description}</p>
      </div>
      <button
        onClick={onToggle}
        className="shrink-0 rounded border border-gray-700 px-2 py-0.5 text-[9px] text-gray-400 hover:border-gray-600 hover:text-gray-200"
      >
        {collapsed ? 'Expand' : 'Collapse'}
      </button>
    </div>
  )
}

export default function WorkflowStudioPage() {
  const router = useRouter()
  const { activeProject, projects } = useProject()
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([])
  const [dags, setDags] = useState<DAGRecord[]>([])
  const [sessions, setSessions] = useState<ExplorationSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
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
  const [focusStage, setFocusStage] = useState<FocusStage | null>(null)
  const [highlightStage, setHighlightStage] = useState<WorkflowStage | null>(null)
  const [sortBy, setSortBy] = useState<SortMode>('active')
  const [expandedWorkflowId, setExpandedWorkflowId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Record<SectionId, boolean>>({
    explorations: false,
    workflows: false,
    templates: false,
  })
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

  const fetchStudio = async () => {
    setLoading(true)
    setError(null)
    try {
      const workflowQs = new URLSearchParams({
        archived: String(showArchived),
        limit: '200',
      })
      const dagQs = new URLSearchParams({
        archived: String(showArchived),
        limit: '200',
      })
      const sessionQs = new URLSearchParams({ limit: '300' })
      if (activeProject) {
        workflowQs.set('project_id', activeProject)
        dagQs.set('project_id', activeProject)
        sessionQs.set('project_id', activeProject)
      }
      const [workflowRes, dagRes, sessionRes] = await Promise.all([
        fetch(`${API}/api/workflows?${workflowQs}`),
        fetch(`${API}/api/dags?${dagQs}`),
        fetch(`${API}/api/explorations?${sessionQs}`),
      ])
      const workflowData = await workflowRes.json().catch(() => [])
      const dagData = await dagRes.json().catch(() => [])
      const sessionData = await sessionRes.json().catch(() => [])
      if (!workflowRes.ok) throw new Error(`Workflows: HTTP ${workflowRes.status}`)
      if (!dagRes.ok) throw new Error(`DAGs: HTTP ${dagRes.status}`)
      if (!sessionRes.ok) throw new Error(`Explorations: HTTP ${sessionRes.status}`)
      setWorkflows(Array.isArray(workflowData) ? workflowData : [])
      setDags(Array.isArray(dagData) ? dagData : [])
      setSessions(Array.isArray(sessionData) ? sessionData : [])
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStudio()
  }, [showArchived, activeProject])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const workflowParam = params.get('workflow')
    if (workflowParam) {
      setSearch('')
      setFocusWorkflow(workflowParam)
      return
    }
    const stageParam = params.get('stage')
    if (
      stageParam === 'exploration' ||
      stageParam === 'experimenting' ||
      stageParam === 'hardened' ||
      stageParam === 'locked'
    ) {
      setFocusStage(stageParam)
      if (stageParam === 'experimenting' || stageParam === 'hardened') {
        setHighlightStage(stageParam)
      }
    }
  }, [])

  useEffect(() => {
    if (!highlightStage || loading) return
    const timer = window.setTimeout(() => setHighlightStage(null), 4000)
    return () => window.clearTimeout(timer)
  }, [highlightStage, loading])

  useEffect(() => {
    if (!focusStage || loading) return
    const section: SectionId =
      focusStage === 'exploration'
        ? 'explorations'
        : focusStage === 'locked'
          ? 'templates'
          : 'workflows'
    setCollapsed((current) => (current[section] ? { ...current, [section]: false } : current))
    const timer = window.setTimeout(() => {
      document.getElementById(`section-${section}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setFocusStage(null)
    }, 80)
    return () => window.clearTimeout(timer)
  }, [focusStage, loading])

  useEffect(() => {
    if (!focusWorkflow || loading) return
    const group = workflows.find((item) => item.id === focusWorkflow)
    if (!group) return
    const section: SectionId =
      group.mode === 'exploration'
        ? 'explorations'
        : group.stage === 'locked'
          ? 'templates'
          : 'workflows'
    setCollapsed((current) => (current[section] ? { ...current, [section]: false } : current))
    const timer = window.setTimeout(() => {
      document.getElementById(`workflow-${focusWorkflow}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setFocusWorkflow(null)
    }, 100)
    return () => window.clearTimeout(timer)
  }, [focusWorkflow, loading, workflows])

  const explorationGroupsBySession = useMemo(() => {
    const map = new Map<string, WorkflowSummary>()
    for (const group of workflows) {
      if (group.mode !== 'exploration' || !group.exploration_session_id) continue
      const existing = map.get(group.exploration_session_id)
      if (
        !existing ||
        new Date(group.updated_at || 0).getTime() > new Date(existing.updated_at || 0).getTime()
      ) {
        map.set(group.exploration_session_id, group)
      }
    }
    return map
  }, [workflows])

  const activeSessions = useMemo(
    () => sessions.filter((session) => session.status !== 'finished'),
    [sessions],
  )

  const workflowGroups = useMemo(
    () => workflows.filter((group) => group.mode !== 'exploration' && !group.locked),
    [workflows],
  )

  const templateGroups = useMemo(
    () => workflows.filter((group) => group.mode !== 'exploration' && group.locked),
    [workflows],
  )

  const hardenedGroups = useMemo(
    () => workflowGroups.filter((group) => group.stage === 'hardened'),
    [workflowGroups],
  )

  const determinism = hardenedGroups.length
    ? Math.round(
        hardenedGroups.reduce((sum, group) => sum + group.determinism_pct, 0) /
          hardenedGroups.length,
      )
    : 0

  const term = search.trim().toLowerCase()
  const searchActive = term.length > 0

  const explorationRows = useMemo(() => {
    const rows: ExplorationRow[] = activeSessions.map((session) => ({
      session,
      group: explorationGroupsBySession.get(session.id) || null,
    }))
    const filtered = rows.filter(({ session, group }) => {
      if (!term) return true
      const projectName = session.project_id ? projectNameById.get(session.project_id) : undefined
      return (
        session.title.toLowerCase().includes(term) ||
        session.objective.toLowerCase().includes(term) ||
        session.id.toLowerCase().includes(term) ||
        (projectName || '').toLowerCase().includes(term) ||
        (group ? groupMatches(group, term, projectName) : false)
      )
    })
    filtered.sort((a, b) => {
      if (sortBy === 'name') return a.session.title.localeCompare(b.session.title)
      if (sortBy === 'score') {
        return (b.group?.latest_score ?? -1) - (a.group?.latest_score ?? -1)
      }
      return (
        new Date(b.session.last_activity_at || b.session.updated_at || 0).getTime() -
        new Date(a.session.last_activity_at || a.session.updated_at || 0).getTime()
      )
    })
    return filtered
  }, [activeSessions, explorationGroupsBySession, term, sortBy, projectNameById])

  const filteredWorkflows = useMemo(() => {
    const filtered = workflowGroups.filter((group) =>
      groupMatches(group, term, group.project_id ? projectNameById.get(group.project_id) : undefined),
    )
    return sortGroups(filtered, sortBy)
  }, [workflowGroups, term, sortBy, projectNameById])

  const filteredTemplates = useMemo(() => {
    const filtered = templateGroups.filter((group) =>
      groupMatches(group, term, group.project_id ? projectNameById.get(group.project_id) : undefined),
    )
    return sortGroups(filtered, sortBy)
  }, [templateGroups, term, sortBy, projectNameById])

  const toggleSection = (section: SectionId) => {
    setCollapsed((current) => ({ ...current, [section]: !current[section] }))
  }

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
      fetchStudio()
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
      fetchStudio()
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
      fetchStudio()
    } catch (err: any) {
      setImportMsg(`❌ ${err.message}`)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Workflows &amp; Pipelines</h1>
          <p className="mt-1 max-w-3xl text-xs text-gray-500">
            Manage workflow reliability: Explore with AI → Harden DAG Workflows → Lock into Reusable Templates.
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
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as SortMode)}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-[10px] text-gray-300"
          >
            <option value="active">Sort: Last Active</option>
            <option value="name">Sort: Name</option>
            <option value="score">Sort: Latest Score</option>
          </select>
          <Link href="/explorations?new=1" className="rounded border border-cyan-700/60 px-3 py-1.5 text-sm text-cyan-300 hover:border-cyan-500 hover:text-cyan-200">
            + New Exploration
          </Link>
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
              <div className="absolute right-0 z-50 mt-1 w-56 rounded border border-gray-700 bg-gray-900 shadow-xl">
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
        <span>🧭 <span className="font-medium text-cyan-300">{activeSessions.length}</span> Active Explorations</span>
        <span className="text-gray-700">•</span>
        <span>🧪 <span className="font-medium text-amber-300">{workflowGroups.length}</span> Workflows in Hardening</span>
        <span className="text-gray-700">•</span>
        <span>🔒 <span className="font-medium text-purple-300">{templateGroups.length}</span> Locked Templates</span>
        <span className="text-gray-700">•</span>
        <span>⚡ <span className="font-medium text-emerald-300">{hardenedGroups.length}</span> Blockified ({determinism}%)</span>
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
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div key={index} className="overflow-hidden rounded-lg border border-gray-800">
              <div className="h-12 animate-pulse border-b border-gray-800 bg-gray-900/40" />
              {Array.from({ length: 4 }).map((__, row) => (
                <div key={row} className="h-14 animate-pulse border-b border-gray-800 bg-gray-900/20" />
              ))}
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          <section id="section-explorations" className="overflow-hidden rounded-lg border border-gray-800">
            <SectionHeader
              number={1}
              icon="🧭"
              title="Active Explorations"
              total={activeSessions.length}
              matched={explorationRows.length}
              searchActive={searchActive}
              description="Conversational agent sessions testing ideas and iterating through steering cycles."
              collapsed={collapsed.explorations}
              onToggle={() => toggleSection('explorations')}
            />
            {!collapsed.explorations && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] border-collapse">
                  <thead className="bg-[#15151f] text-left text-[9px] uppercase tracking-wide text-gray-600">
                    <tr>
                      <th className="px-3 py-2 font-medium">Exploration &amp; objective</th>
                      <th className="px-3 py-2 font-medium">Current state</th>
                      <th className="px-3 py-2 font-medium">Latest audit</th>
                      <th className="px-3 py-2 font-medium">Last active</th>
                      <th className="px-3 py-2 text-right font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {explorationRows.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="px-3 py-8 text-center text-[11px] text-gray-500">
                          {searchActive ? 'No explorations match this search.' : 'No active explorations.'}
                        </td>
                      </tr>
                    ) : (
                      explorationRows.map(({ session, group }) => (
                        <ExplorationTableRow
                          key={session.id}
                          session={session}
                          group={group}
                          projectName={session.project_id ? projectNameById.get(session.project_id) : undefined}
                        />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section id="section-workflows" className="overflow-hidden rounded-lg border border-gray-800">
            <SectionHeader
              number={2}
              icon="🧪"
              title="Workflows & DAG Hardening"
              total={workflowGroups.length}
              matched={filteredWorkflows.length}
              searchActive={searchActive}
              description="Multi-step execution pipelines undergoing iterative validation and conversion into deterministic Blocks."
              collapsed={collapsed.workflows}
              onToggle={() => toggleSection('workflows')}
            />
            {!collapsed.workflows && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1080px] border-collapse">
                  <thead className="bg-[#15151f] text-left text-[9px] uppercase tracking-wide text-gray-600">
                    <tr>
                      <th className="px-3 py-2 font-medium">Workflow name &amp; domain</th>
                      <th className="px-3 py-2 font-medium">Maturity &amp; blocks</th>
                      <th className="px-3 py-2 font-medium">Latest run</th>
                      <th className="px-3 py-2 font-medium">History (last 5)</th>
                      <th className="px-3 py-2 font-medium">Runs count</th>
                      <th className="px-3 py-2 text-right font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredWorkflows.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-3 py-8 text-center text-[11px] text-gray-500">
                          {searchActive ? 'No workflows match this search.' : 'No workflows in hardening.'}
                        </td>
                      </tr>
                    ) : (
                      filteredWorkflows.map((group) => (
                        <WorkflowTableRow
                          key={group.id}
                          group={group}
                          projectName={group.project_id ? projectNameById.get(group.project_id) : undefined}
                          expanded={expandedWorkflowId === group.id}
                          variant="workflow"
                          highlight={highlightStage === group.stage}
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
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section id="section-templates" className="overflow-hidden rounded-lg border border-gray-800">
            <SectionHeader
              number={3}
              icon="🔒"
              title="Locked Templates"
              total={templateGroups.length}
              matched={filteredTemplates.length}
              searchActive={searchActive}
              description="Predefined, validated workflows frozen for reliable, production-grade execution."
              collapsed={collapsed.templates}
              onToggle={() => toggleSection('templates')}
            />
            {!collapsed.templates && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1040px] border-collapse">
                  <thead className="bg-[#15151f] text-left text-[9px] uppercase tracking-wide text-gray-600">
                    <tr>
                      <th className="px-3 py-2 font-medium">Template name &amp; objective</th>
                      <th className="px-3 py-2 font-medium">Determinism level</th>
                      <th className="px-3 py-2 font-medium">Fleet stability</th>
                      <th className="px-3 py-2 font-medium">Total executions</th>
                      <th className="px-3 py-2 font-medium">Last run</th>
                      <th className="px-3 py-2 text-right font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredTemplates.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-3 py-8 text-center text-[11px] text-gray-500">
                          {searchActive ? 'No templates match this search.' : 'No locked templates.'}
                        </td>
                      </tr>
                    ) : (
                      filteredTemplates.map((group) => (
                        <WorkflowTableRow
                          key={group.id}
                          group={group}
                          projectName={group.project_id ? projectNameById.get(group.project_id) : undefined}
                          expanded={expandedWorkflowId === group.id}
                          variant="template"
                          onToggleExpand={() => setExpandedWorkflowId((current) => current === group.id ? null : group.id)}
                          onRun={runWorkflow}
                          onResume={rerunFailed}
                          onOpenLatest={openLatest}
                          onOpenTemplate={openTemplate}
                          onEditTemplate={openTemplate}
                          onRerunProblem={rerunFailed}
                          onConvert={convertStep}
                          onMerge={setMergeTarget}
                          onArchiveRun={archiveRun}
                          onDeleteRun={deleteRun}
                        />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>
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
            fetchStudio()
          }}
        />
      )}
    </div>
  )
}
