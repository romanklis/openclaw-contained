'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { StatusBadge } from '../components/StatusComponents'
import { API, TEMPORAL_UI } from '../lib/api'
import { useProject } from '../lib/ProjectContext'

interface DAG {
  id: string
  objective: string
  status: string
  workspace_id: string
  llm_model: string
  workflow_id: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  locked: boolean
  archived: boolean
}

interface Skill {
  id: string
  name: string
  description: string | null
}

export default function DAGsPage() {
  const [dags, setDags] = useState<DAG[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [objective, setObjective] = useState('')
  const [autoStart, setAutoStart] = useState(false)
  const [skills, setSkills] = useState<Skill[]>([])
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([])
  const [modelDefaults, setModelDefaults] = useState<{planning_model: string, agent_model: string} | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const { activeProject } = useProject()

  const fetchDags = async () => {
    try {
      const qs = new URLSearchParams({ archived: String(showArchived) })
      if (activeProject) qs.set('project_id', activeProject)
      const res = await fetch(`${API}/api/dags?${qs}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDags(await res.json())
    } catch (err: any) {
      setError(err.message)
    }
  }

  const fetchSkills = async () => {
    try {
      const res = await fetch(`${API}/api/skills`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSkills(await res.json())
    } catch (err: any) {
      console.error('Failed to fetch skills:', err)
    }
  }

  const fetchModelDefaults = async () => {
    try {
      const res = await fetch(`${API}/api/dags/model-defaults`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setModelDefaults(await res.json())
    } catch (err: any) {
      console.error('Failed to fetch model defaults:', err)
    }
  }

  useEffect(() => { fetchDags(); fetchSkills(); fetchModelDefaults() }, [showArchived, activeProject])

  const createDag = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/dags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          objective,
          auto_start: autoStart,
          skill_ids: selectedSkillIds.length > 0 ? selectedSkillIds : null,
          project_id: activeProject || null,
        }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      setObjective('')
      setSelectedSkillIds([])
      setShowForm(false)
      fetchDags()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const startDag = async (dagId: string) => {
    try {
      await fetch(`${API}/api/dags/${dagId}/start`, { method: 'POST' })
      fetchDags()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const archiveDag = async (dagId: string) => {
    if (!window.confirm(`Archive DAG ${dagId}? It will be hidden from the list.`)) return
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/archive`, { method: 'POST' })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      fetchDags()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const unarchiveDag = async (dagId: string) => {
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/unarchive`, { method: 'POST' })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      fetchDags()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const deleteDag = async (dagId: string) => {
    if (!window.confirm(`Permanently delete DAG ${dagId} and ALL its data (tasks, outputs, reviews, workspace)? This cannot be undone.`)) return
    try {
      const res = await fetch(`${API}/api/dags/${dagId}`, { method: 'DELETE' })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      fetchDags()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const getWorkflowLink = (workflowId: string) => {
    return `${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(workflowId)}`
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">DAGs</h1>
        <div className="flex gap-2 items-center">
          <Link href="/template-skills" className="btn-primary text-sm !bg-purple-700 hover:!bg-purple-600">🧩 Template Skills</Link>
          <button onClick={() => setShowArchived(!showArchived)} className="btn-primary text-sm">
            {showArchived ? 'Show active' : '📁 Show archived'}
          </button>
          <button onClick={() => setShowForm(!showForm)} className="btn-primary text-sm">
            {showForm ? 'Cancel' : '+ New DAG'}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-900/20 border border-red-500/30 rounded p-3 mb-4 text-red-300 text-sm">{error}</div>}

      {showForm && (
        <form onSubmit={createDag} className="card mb-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Objective</label>
            <textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
              rows={3}
              placeholder="Describe what you want to accomplish..."
              required
            />
          </div>
          <div className="flex gap-4">
            <div className="flex-1">
              <label className="block text-sm text-gray-400 mb-1">LLM Models</label>
              {modelDefaults ? (
                <div className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-300">
                  <span className="text-gray-500">Planning:</span> <span className="font-mono">{modelDefaults.planning_model}</span>
                  <span className="mx-2 text-gray-600">|</span>
                  <span className="text-gray-500">Agents:</span> <span className="font-mono">{modelDefaults.agent_model}</span>
                  <span className="ml-2 text-xs text-gray-600">(change in <a href="/llm-providers" className="text-indigo-400 hover:text-indigo-300">LLM Router</a>)</span>
                </div>
              ) : (
                <div className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-500">Loading defaults…</div>
              )}
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm text-gray-400">
                <input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} />
                Auto-start
              </label>
            </div>
          </div>
          {skills.length > 0 && (
            <div>
              <label className="block text-sm text-gray-400 mb-1">
                Skills <span className="text-gray-600">(optional — select which skills the planner can use)</span>
              </label>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 max-h-48 overflow-y-auto bg-gray-800/50 border border-gray-700 rounded p-2">
                {skills.map((skill) => (
                  <label key={skill.id} className={`flex items-start gap-2 p-2 rounded cursor-pointer hover:bg-gray-700/50 text-sm ${selectedSkillIds.includes(skill.id) ? 'bg-blue-500/10 border border-blue-500/30' : 'border border-transparent'}`}>
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={selectedSkillIds.includes(skill.id)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedSkillIds([...selectedSkillIds, skill.id])
                        } else {
                          setSelectedSkillIds(selectedSkillIds.filter(id => id !== skill.id))
                        }
                      }}
                    />
                    <div>
                      <div className="font-medium text-gray-200">{skill.name}</div>
                      {skill.description && <div className="text-xs text-gray-500 line-clamp-1">{skill.description}</div>}
                    </div>
                  </label>
                ))}
              </div>
              {selectedSkillIds.length > 0 && (
                <div className="text-xs text-blue-400 mt-1">{selectedSkillIds.length} skill(s) selected</div>
              )}
            </div>
          )}
          <button type="submit" disabled={loading} className="btn-success text-sm">
            {loading ? 'Planning...' : 'Create DAG'}
          </button>
        </form>
      )}

      <div className="space-y-3">
        {dags.map((dag) => (
          <Link key={dag.id} href={`/dags/${dag.id}`} className="card block hover:border-blue-500/50 transition-colors">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <span className="font-mono text-sm text-gray-400">{dag.id}</span>
                  <StatusBadge status={dag.status} />
                  {dag.locked && <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">🔒 template</span>}
                </div>
                <p className="text-sm text-gray-300 line-clamp-2">{dag.objective}</p>
              </div>
              <div className="text-right text-xs text-gray-500">
                <div>{dag.llm_model}</div>
                <div>{new Date(dag.created_at).toLocaleString()}</div>
                {dag.workflow_id && (
                  <a
                    href={getWorkflowLink(dag.workflow_id)}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="mt-1 inline-block text-blue-400 hover:text-blue-300"
                  >
                    Temporal workflow
                  </a>
                )}
                {dag.status === 'ready' && (
                  <button
                    onClick={(e) => { e.preventDefault(); startDag(dag.id) }}
                    className="mt-1 btn-success text-xs"
                  >
                    ▶ Start
                  </button>
                )}
                {!dag.locked && dag.status !== 'running' && (
                  <div className="mt-1 flex justify-end gap-1">
                    {showArchived ? (
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); unarchiveDag(dag.id) }}
                        className="btn-primary text-xs"
                        title="Restore to active list"
                      >
                        ↩ Restore
                      </button>
                    ) : (
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); archiveDag(dag.id) }}
                        className="btn-warning text-xs"
                        title="Hide from the list (can be restored)"
                      >
                        📁 Archive
                      </button>
                    )}
                    <button
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); deleteDag(dag.id) }}
                      className="btn-danger text-xs"
                      title="Permanently delete DAG and all its data"
                    >
                      🗑 Delete
                    </button>
                  </div>
                )}
              </div>
            </div>
          </Link>
        ))}
        {dags.length === 0 && (
          <div className="text-center text-gray-500 py-12">
            {showArchived ? 'No archived DAGs.' : 'No DAGs yet. Create one to get started.'}
          </div>
        )}
      </div>
    </div>
  )
}
