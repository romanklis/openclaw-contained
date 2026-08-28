'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { StatusDot, StatusBadge } from '../components/StatusComponents'
import { API, API_GATEWAY } from '../lib/api'

// ─── Types ────────────────────────────────────────────────

interface TaskForceMember {
  id?: number
  agent_profile: string
  role: string
  responsibilities: string
  llm_model?: string
  base_image?: string
  execution_order: number
  task_id?: string
  status?: string
}

interface TaskForceCeremony {
  id?: number
  name: string
  ceremony_type: 'planning' | 'sync' | 'peer_review' | 'aggregation' | 'custom'
  mode: 'synchronous' | 'asynchronous'
  sequence_order: number
  participant_member_ids?: number[] | null
  description: string
  trigger_condition: string
  timeout_minutes: number
  status?: string
}

interface TaskForce {
  id: string
  name: string
  description: string
  objective: string
  execution_environment: 'dind' | 'dedicated_vm'
  status: string
  workspace_id: string
  workflow_id?: string
  members: TaskForceMember[]
  ceremonies: TaskForceCeremony[]
  created_at: string
  started_at?: string
  completed_at?: string
}

interface AgentProfile {
  id: string
  name: string
  description: string
  base_image: string
  llm_model: string
  icon: string
  tags: string[]
  metadata: { runtime: string; strengths: string[] }
}

const CEREMONY_TYPES = [
  { value: 'planning', label: 'Planning Phase', icon: '📋', desc: 'Agents plan and coordinate before execution' },
  { value: 'sync', label: 'Sync Meeting', icon: '🔄', desc: 'Agents share progress and align on next steps' },
  { value: 'peer_review', label: 'Peer Review', icon: '🔍', desc: 'Agents review each other\'s work' },
  { value: 'aggregation', label: 'Final Aggregation', icon: '📦', desc: 'Collect and merge all outputs into a final result' },
  { value: 'custom', label: 'Custom', icon: '⚙️', desc: 'Custom coordination event' },
]

const ENV_OPTIONS = [
  { value: 'dind', label: 'Docker-in-Docker (DinD)', desc: 'Standard sandbox — lightweight isolation', icon: '🐳' },
  { value: 'dedicated_vm', label: 'Dedicated VM', desc: 'Hypervisor-level isolation for sensitive workloads', icon: '🖥️' },
]

// ─── Component ────────────────────────────────────────────

export default function TaskForcesPage() {
  const [taskForces, setTaskForces] = useState<TaskForce[]>([])
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [error, setError] = useState<string | null>(null)

  // ── Form state ──
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formObjective, setFormObjective] = useState('')
  const [formEnv, setFormEnv] = useState<'dind' | 'dedicated_vm'>('dind')
  const [formMembers, setFormMembers] = useState<TaskForceMember[]>([])
  const [formCeremonies, setFormCeremonies] = useState<TaskForceCeremony[]>([])
  const [creating, setCreating] = useState(false)

  // ── Fetch data ──
  const fetchTaskForces = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/task-forces`)
      if (res.ok) {
        const data = await res.json()
        setTaskForces(Array.isArray(data) ? data : [])
      }
    } catch (err) {
      console.error('Failed to fetch task forces:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchProfiles = useCallback(async () => {
    try {
      const res = await fetch(`${API_GATEWAY}/v1/agent-profiles`)
      if (res.ok) {
        const data = await res.json()
        setProfiles(data.profiles || [])
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetchTaskForces()
    fetchProfiles()
    const interval = setInterval(fetchTaskForces, 5000)
    return () => clearInterval(interval)
  }, [fetchTaskForces, fetchProfiles])

  // ── Member management ──
  const addMember = () => {
    const defaultProfile = profiles[0]?.id || 'general-assistant'
    setFormMembers([...formMembers, {
      agent_profile: defaultProfile,
      role: '',
      responsibilities: '',
      execution_order: formMembers.length,
    }])
  }

  const updateMember = (idx: number, updates: Partial<TaskForceMember>) => {
    setFormMembers(formMembers.map((m, i) => i === idx ? { ...m, ...updates } : m))
  }

  const removeMember = (idx: number) => {
    setFormMembers(formMembers.filter((_, i) => i !== idx))
  }

  // ── Ceremony management ──
  const addCeremony = () => {
    setFormCeremonies([...formCeremonies, {
      name: '',
      ceremony_type: 'sync',
      mode: 'synchronous',
      sequence_order: formCeremonies.length,
      description: '',
      trigger_condition: 'after_all_complete',
      timeout_minutes: 60,
    }])
  }

  const updateCeremony = (idx: number, updates: Partial<TaskForceCeremony>) => {
    setFormCeremonies(formCeremonies.map((c, i) => i === idx ? { ...c, ...updates } : c))
  }

  const removeCeremony = (idx: number) => {
    setFormCeremonies(formCeremonies.filter((_, i) => i !== idx))
  }

  // ── Create ──
  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (formMembers.length === 0) {
      setError('Add at least one member to the Task Force.')
      return
    }
    setCreating(true)
    setError(null)

    try {
      const res = await fetch(`${API}/api/task-forces`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formName,
          description: formDescription,
          objective: formObjective,
          execution_environment: formEnv,
          members: formMembers,
          ceremonies: formCeremonies,
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      // Reset form
      setFormName(''); setFormDescription(''); setFormObjective('')
      setFormMembers([]); setFormCeremonies([]); setFormEnv('dind')
      setShowCreate(false)
      fetchTaskForces()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create Task Force')
    } finally {
      setCreating(false)
    }
  }

  // ── Start Task Force ──
  const startTaskForce = async (tfId: string) => {
    try {
      const res = await fetch(`${API}/api/task-forces/${tfId}/start`, { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Failed' }))
        throw new Error(err.detail)
      }
      fetchTaskForces()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start')
    }
  }

  const profileMap = Object.fromEntries(profiles.map(p => [p.id, p]))

  const statusCounts = {
    all: taskForces.length,
    active: taskForces.filter(t => t.status === 'active').length,
    draft: taskForces.filter(t => t.status === 'draft').length,
    running: taskForces.filter(t => t.status === 'running').length,
    completed: taskForces.filter(t => t.status === 'completed').length,
  }

  const filtered = statusFilter === 'all'
    ? taskForces
    : taskForces.filter(t => t.status === statusFilter)

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">Task Forces</h1>
          <p className="text-sm text-gray-500">Define reusable agent teams — they appear as virtual agents for task submission</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className={showCreate ? 'btn-secondary text-sm' : 'btn-primary text-sm'}
        >
          {showCreate ? 'Cancel' : '+ Define Agent Team'}
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg p-3 mb-6">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red-500 hover:text-red-400">✕</button>
        </div>
      )}

      {/* ── CREATE FORM ──────────────────────────────── */}
      {showCreate && (
        <div className="card p-6 mb-6 animate-fade-in">
          <h2 className="font-semibold text-white mb-4">Create Task Force</h2>
          <form onSubmit={handleCreate} className="space-y-5">
            {/* Basic info */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Name</label>
                <input type="text" value={formName} onChange={e => setFormName(e.target.value)}
                  className="input-field" placeholder="e.g., Security Audit Team" required />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Execution Environment</label>
                <select value={formEnv} onChange={e => setFormEnv(e.target.value as any)} className="input-field">
                  {ENV_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.icon} {o.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Description</label>
              <input type="text" value={formDescription} onChange={e => setFormDescription(e.target.value)}
                className="input-field" placeholder="Brief description of this task force" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Objective</label>
              <textarea value={formObjective} onChange={e => setFormObjective(e.target.value)} rows={3}
                className="input-field" placeholder="What should this task force accomplish? Be specific..." required />
            </div>

            {/* ── MEMBERS ──────────────── */}
            <div className="border border-[#232333] rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-white">Team Members</h3>
                <button type="button" onClick={addMember}
                  className="text-xs text-indigo-400 hover:text-indigo-300 font-medium">+ Add Agent</button>
              </div>

              {formMembers.length === 0 ? (
                <p className="text-sm text-gray-600 text-center py-4">No agents added yet. Click "+ Add Agent" to build your team.</p>
              ) : (
                <div className="space-y-3">
                  {formMembers.map((member, idx) => {
                    const prof = profileMap[member.agent_profile]
                    return (
                      <div key={idx} className="bg-[#0e0e14] rounded-lg p-4 border border-[#1a1a2a]">
                        <div className="flex items-center justify-between mb-3">
                          <span className="text-xs text-gray-500 font-mono">Agent #{idx + 1}</span>
                          <button type="button" onClick={() => removeMember(idx)}
                            className="text-xs text-red-500 hover:text-red-400">Remove</button>
                        </div>
                        <div className="grid grid-cols-2 gap-3 mb-3">
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Agent Profile</label>
                            <select
                              value={member.agent_profile}
                              onChange={e => updateMember(idx, { agent_profile: e.target.value })}
                              className="input-field text-sm"
                            >
                              {profiles.map(p => (
                                <option key={p.id} value={p.id}>{p.icon} {p.name}</option>
                              ))}
                            </select>
                          </div>
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Role</label>
                            <input type="text" value={member.role}
                              onChange={e => updateMember(idx, { role: e.target.value })}
                              className="input-field text-sm" placeholder="e.g., Researcher, Developer" required />
                          </div>
                        </div>
                        <div>
                          <label className="block text-[11px] text-gray-500 mb-1">Responsibilities</label>
                          <textarea value={member.responsibilities}
                            onChange={e => updateMember(idx, { responsibilities: e.target.value })}
                            className="input-field text-sm" rows={2}
                            placeholder="What should this agent focus on?" />
                        </div>
                        {prof && (
                          <div className="flex items-center gap-2 mt-2 text-[11px] text-gray-500">
                            <span>{prof.icon}</span>
                            <span>{prof.base_image} · {prof.llm_model}</span>
                            {prof.metadata?.runtime && <span className="text-gray-600">({prof.metadata.runtime})</span>}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* ── CEREMONIES ──────────── */}
            <div className="border border-[#232333] rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-sm font-semibold text-white">Ceremonies & Meeting Points</h3>
                  <p className="text-[11px] text-gray-600 mt-0.5">Define how agents coordinate — optional</p>
                </div>
                <button type="button" onClick={addCeremony}
                  className="text-xs text-indigo-400 hover:text-indigo-300 font-medium">+ Add Ceremony</button>
              </div>

              {formCeremonies.length === 0 ? (
                <p className="text-sm text-gray-600 text-center py-4">
                  No ceremonies defined. Agents will run in parallel without coordination.
                </p>
              ) : (
                <div className="space-y-3">
                  {formCeremonies.map((ceremony, idx) => {
                    const cType = CEREMONY_TYPES.find(c => c.value === ceremony.ceremony_type)
                    return (
                      <div key={idx} className="bg-[#0e0e14] rounded-lg p-4 border border-[#1a1a2a]">
                        <div className="flex items-center justify-between mb-3">
                          <span className="text-xs text-gray-500 font-mono">
                            {cType?.icon} Ceremony #{idx + 1}
                          </span>
                          <button type="button" onClick={() => removeCeremony(idx)}
                            className="text-xs text-red-500 hover:text-red-400">Remove</button>
                        </div>
                        <div className="grid grid-cols-3 gap-3 mb-3">
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Name</label>
                            <input type="text" value={ceremony.name}
                              onChange={e => updateCeremony(idx, { name: e.target.value })}
                              className="input-field text-sm" placeholder="e.g., Code Review Sync" required />
                          </div>
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Type</label>
                            <select value={ceremony.ceremony_type}
                              onChange={e => updateCeremony(idx, { ceremony_type: e.target.value as any })}
                              className="input-field text-sm">
                              {CEREMONY_TYPES.map(t => (
                                <option key={t.value} value={t.value}>{t.icon} {t.label}</option>
                              ))}
                            </select>
                          </div>
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Mode</label>
                            <select value={ceremony.mode}
                              onChange={e => updateCeremony(idx, { mode: e.target.value as any })}
                              className="input-field text-sm">
                              <option value="synchronous">Synchronous</option>
                              <option value="asynchronous">Asynchronous</option>
                            </select>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3 mb-3">
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Trigger</label>
                            <select value={ceremony.trigger_condition}
                              onChange={e => updateCeremony(idx, { trigger_condition: e.target.value })}
                              className="input-field text-sm">
                              <option value="after_all_complete">After all agents complete</option>
                              <option value="manual">Manual trigger</option>
                            </select>
                          </div>
                          <div>
                            <label className="block text-[11px] text-gray-500 mb-1">Timeout (min)</label>
                            <input type="number" value={ceremony.timeout_minutes}
                              onChange={e => updateCeremony(idx, { timeout_minutes: parseInt(e.target.value) || 60 })}
                              className="input-field text-sm" min={5} max={480} />
                          </div>
                        </div>
                        <div>
                          <label className="block text-[11px] text-gray-500 mb-1">Description / Instructions</label>
                          <textarea value={ceremony.description}
                            onChange={e => updateCeremony(idx, { description: e.target.value })}
                            className="input-field text-sm" rows={2}
                            placeholder="Instructions for this coordination event..." />
                        </div>
                        {cType && (
                          <div className="mt-2 text-[11px] text-gray-600">{cType.desc}</div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            <button type="submit" disabled={creating} className="btn-success text-sm">
              {creating ? 'Creating...' : 'Create Agent Team'}
            </button>
          </form>
        </div>
      )}

      {/* ── FILTER TABS ──────────────────────────────── */}
      <div className="flex gap-1 mb-4 bg-[#12121a] rounded-lg p-1 w-fit">
        {(['all', 'active', 'draft', 'running', 'completed'] as const).map(f => (
          <button key={f} onClick={() => setStatusFilter(f)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors capitalize ${
              statusFilter === f ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
            }`}>
            {f} {(statusCounts as any)[f] > 0 && `(${(statusCounts as any)[f]})`}
          </button>
        ))}
      </div>

      {/* ── TASK FORCE LIST ──────────────────────────── */}
      <div className="space-y-3">
        {loading ? (
          <div className="card p-12 text-center">
            <p className="text-gray-500 text-sm">Loading task forces...</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="card p-12 text-center">
            <div className="text-4xl mb-3">🎯</div>
            <p className="text-gray-500 text-sm">
              {statusFilter === 'all'
                ? 'No task forces yet. Create one to orchestrate a team of agents!'
                : `No ${statusFilter} task forces`}
            </p>
          </div>
        ) : (
          filtered.map(tf => (
            <div key={tf.id} className="card card-hover p-5 animate-fade-in">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3 min-w-0 flex-1">
                  <StatusDot status={tf.status} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <Link href={`/task-forces/${tf.id}`}
                        className="font-medium text-white hover:text-indigo-400 transition-colors">
                        {tf.name}
                      </Link>
                      <StatusBadge status={tf.status} />
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
                        {tf.execution_environment}
                      </span>
                    </div>
                    {tf.description && (
                      <p className="text-sm text-gray-500 mb-2 line-clamp-1">{tf.description}</p>
                    )}
                    <p className="text-sm text-gray-400 mb-2 line-clamp-2">{tf.objective}</p>

                    {/* Members badges */}
                    <div className="flex items-center gap-2 flex-wrap mb-2">
                      <span className="text-xs text-gray-600">Team:</span>
                      {(tf.members || []).map((m, i) => {
                        const prof = profileMap[m.agent_profile]
                        return (
                          <span key={i}
                            className="inline-flex items-center gap-1 text-[11px] bg-[#12121a] border border-[#232333] rounded-lg px-2 py-0.5">
                            <span>{prof?.icon || '🤖'}</span>
                            <span className="text-gray-300">{m.role || m.agent_profile}</span>
                          </span>
                        )
                      })}
                    </div>

                    {/* Ceremonies */}
                    {(tf.ceremonies || []).length > 0 && (
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs text-gray-600">Ceremonies:</span>
                        {tf.ceremonies.map((c, i) => {
                          const cType = CEREMONY_TYPES.find(t => t.value === c.ceremony_type)
                          return (
                            <span key={i}
                              className="inline-flex items-center gap-1 text-[11px] bg-[#12121a] border border-[#232333] rounded-lg px-2 py-0.5">
                              <span>{cType?.icon || '⚙️'}</span>
                              <span className="text-gray-300">{c.name}</span>
                            </span>
                          )
                        })}
                      </div>
                    )}

                    <div className="flex items-center gap-3 text-xs text-gray-600 mt-2">
                      <span className="font-mono">{tf.id}</span>
                      <span>•</span>
                      <span>{new Date(tf.created_at).toLocaleString()}</span>
                      {tf.workflow_id && (
                        <>
                          <span>•</span>
                          <a href={`http://localhost:8088/namespaces/default/workflows/${tf.workflow_id}`}
                            target="_blank" rel="noopener noreferrer"
                            className="text-indigo-500 hover:text-indigo-400">Temporal ↗</a>
                        </>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {tf.status === 'active' && (
                    <span className="text-[10px] text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 px-2 py-1 rounded">
                      ✓ Available as Agent
                    </span>
                  )}
                  {tf.status === 'draft' && (
                    <button onClick={() => startTaskForce(tf.id)}
                      className="btn-success text-xs">▶ Launch</button>
                  )}
                  <Link href={`/task-forces/${tf.id}`} className="btn-secondary text-xs">Details →</Link>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
