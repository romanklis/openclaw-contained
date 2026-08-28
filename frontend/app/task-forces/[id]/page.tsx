'use client'

import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { StatusDot, StatusBadge } from '../../components/StatusComponents'
import { API, API_GATEWAY } from '../../lib/api'

// ─── Types ────────────────────────────────────────────────

interface TaskForceMember {
  id: number
  agent_profile: string
  role: string
  responsibilities: string
  llm_model?: string
  base_image?: string
  execution_order: number
  task_id?: string
  status: string
}

interface TaskForceCeremony {
  id: number
  name: string
  ceremony_type: string
  mode: string
  sequence_order: number
  participant_member_ids?: number[] | null
  description: string
  trigger_condition: string
  timeout_minutes: number
  status: string
  started_at?: string
  completed_at?: string
  result_summary?: string
}

interface TaskForceDetail {
  id: string
  name: string
  description: string
  objective: string
  execution_environment: string
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
  icon: string
  base_image: string
  llm_model: string
  metadata: { runtime: string; strengths: string[] }
}

const CEREMONY_ICONS: Record<string, string> = {
  planning: '📋',
  sync: '🔄',
  peer_review: '🔍',
  aggregation: '📦',
  custom: '⚙️',
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'text-gray-400 bg-gray-500/10 border-gray-500/20',
  running: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  completed: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  failed: 'text-red-400 bg-red-500/10 border-red-500/20',
  paused: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  pending: 'text-gray-400 bg-gray-500/10 border-gray-500/20',
  active: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
  created: 'text-gray-400 bg-gray-500/10 border-gray-500/20',
}

// ─── Component ────────────────────────────────────────────

export default function TaskForceDetailPage() {
  const params = useParams()
  const tfId = params.id as string

  const [tf, setTf] = useState<TaskForceDetail | null>(null)
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'members' | 'ceremonies' | 'timeline'>('overview')

  const fetchDetail = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/task-forces/${tfId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setTf(await res.json())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [tfId])

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
    fetchDetail()
    fetchProfiles()
    const interval = setInterval(fetchDetail, 5000)
    return () => clearInterval(interval)
  }, [fetchDetail, fetchProfiles])

  const profileMap = Object.fromEntries(profiles.map(p => [p.id, p]))

  const startTaskForce = async () => {
    try {
      const res = await fetch(`${API}/api/task-forces/${tfId}/start`, { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Failed' }))
        throw new Error(err.detail)
      }
      fetchDetail()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start')
    }
  }

  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <p className="text-gray-500 text-sm">Loading task force...</p>
      </div>
    )
  }

  if (!tf) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <div className="card p-12 text-center">
          <div className="text-4xl mb-3">❌</div>
          <p className="text-gray-500 text-sm">Task Force not found</p>
          <Link href="/task-forces" className="text-indigo-400 hover:text-indigo-300 text-sm mt-4 inline-block">← Back to Task Forces</Link>
        </div>
      </div>
    )
  }

  const membersDone = tf.members.filter(m => m.status === 'completed').length
  const membersTotal = tf.members.length
  const ceremoniesDone = tf.ceremonies.filter(c => c.status === 'completed').length
  const ceremoniesTotal = tf.ceremonies.length

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-2 text-sm text-gray-500 mb-4">
        <Link href="/task-forces" className="hover:text-gray-300">Task Forces</Link>
        <span>/</span>
        <span className="text-gray-300">{tf.name}</span>
      </div>

      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-bold text-white">{tf.name}</h1>
            <StatusBadge status={tf.status} />
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20 uppercase">
              {tf.execution_environment}
            </span>
          </div>
          {tf.description && <p className="text-sm text-gray-500">{tf.description}</p>}
          <div className="flex items-center gap-4 text-xs text-gray-600 mt-2">
            <span className="font-mono">{tf.id}</span>
            <span>Workspace: {tf.workspace_id}</span>
            {tf.workflow_id && (
              <a href={`http://localhost:8088/namespaces/default/workflows/${tf.workflow_id}`}
                target="_blank" rel="noopener noreferrer"
                className="text-indigo-500 hover:text-indigo-400">Temporal ↗</a>
            )}
          </div>
        </div>

        <div className="flex gap-2">
          {tf.status === 'draft' && (
            <button onClick={startTaskForce} className="btn-success text-sm">▶ Launch Task Force</button>
          )}
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg p-3 mb-6">
          {error}
        </div>
      )}

      {/* Progress overview */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="stat-card">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Members</div>
          <div className="text-2xl font-bold text-indigo-400">{membersTotal}</div>
          <div className="text-[11px] text-gray-600 mt-1">{membersDone} completed</div>
        </div>
        <div className="stat-card">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Ceremonies</div>
          <div className="text-2xl font-bold text-purple-400">{ceremoniesTotal}</div>
          <div className="text-[11px] text-gray-600 mt-1">{ceremoniesDone} completed</div>
        </div>
        <div className="stat-card">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Status</div>
          <div className="text-2xl font-bold text-white capitalize">{tf.status}</div>
          <div className="text-[11px] text-gray-600 mt-1">
            {tf.started_at ? `Started ${new Date(tf.started_at).toLocaleString()}` : 'Not started'}
          </div>
        </div>
        <div className="stat-card">
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Objective</div>
          <div className="text-sm text-gray-300 line-clamp-3">{tf.objective}</div>
        </div>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-1 mb-4 bg-[#12121a] rounded-lg p-1 w-fit">
        {(['overview', 'members', 'ceremonies', 'timeline'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors capitalize ${
              activeTab === tab ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
            }`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="mt-4">
        {/* ── OVERVIEW TAB ─── */}
        {activeTab === 'overview' && (
          <div className="space-y-4">
            <div className="card p-5">
              <h3 className="font-semibold text-white mb-3">Team Composition</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {tf.members.map(m => {
                  const prof = profileMap[m.agent_profile]
                  const statusColor = STATUS_COLORS[m.status] || STATUS_COLORS.pending
                  return (
                    <div key={m.id} className="bg-[#0e0e14] rounded-lg p-4 border border-[#1a1a2a]">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-xl">{prof?.icon || '🤖'}</span>
                        <div>
                          <div className="text-sm font-semibold text-white">{m.role}</div>
                          <div className="text-[11px] text-gray-500">{prof?.name || m.agent_profile}</div>
                        </div>
                        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border capitalize ${statusColor}`}>
                          {m.status}
                        </span>
                      </div>
                      {m.responsibilities && (
                        <p className="text-xs text-gray-500 mt-1 line-clamp-2">{m.responsibilities}</p>
                      )}
                      {m.task_id && (
                        <Link href={`/tasks/${m.task_id}`}
                          className="text-[11px] text-indigo-400 hover:text-indigo-300 mt-2 inline-block">
                          View Task →
                        </Link>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {tf.ceremonies.length > 0 && (
              <div className="card p-5">
                <h3 className="font-semibold text-white mb-3">Ceremony Pipeline</h3>
                <div className="relative">
                  {tf.ceremonies
                    .sort((a, b) => a.sequence_order - b.sequence_order)
                    .map((c, idx) => {
                      const icon = CEREMONY_ICONS[c.ceremony_type] || '⚙️'
                      const statusColor = STATUS_COLORS[c.status] || STATUS_COLORS.pending
                      return (
                        <div key={c.id} className="flex items-start gap-3 mb-4 last:mb-0">
                          {/* Connection line */}
                          <div className="flex flex-col items-center">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
                              c.status === 'completed' ? 'bg-emerald-500/20' :
                              c.status === 'active' ? 'bg-blue-500/20' :
                              'bg-[#1a1a2a]'
                            }`}>
                              <span className="text-sm">{icon}</span>
                            </div>
                            {idx < tf.ceremonies.length - 1 && (
                              <div className="w-0.5 h-8 bg-[#232333] mt-1" />
                            )}
                          </div>
                          <div className="flex-1 bg-[#0e0e14] rounded-lg p-3 border border-[#1a1a2a]">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-sm font-medium text-white">{c.name}</span>
                              <span className={`text-[10px] px-1.5 py-0.5 rounded border capitalize ${statusColor}`}>
                                {c.status}
                              </span>
                              <span className="text-[10px] text-gray-600 font-mono ml-auto">{c.mode}</span>
                            </div>
                            {c.description && (
                              <p className="text-xs text-gray-500">{c.description}</p>
                            )}
                            {c.result_summary && (
                              <div className="mt-2 text-xs text-gray-400 bg-[#12121a] rounded p-2 font-mono">
                                {c.result_summary}
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── MEMBERS TAB ─── */}
        {activeTab === 'members' && (
          <div className="space-y-3">
            {tf.members.map(m => {
              const prof = profileMap[m.agent_profile]
              const statusColor = STATUS_COLORS[m.status] || STATUS_COLORS.pending
              return (
                <div key={m.id} className="card p-5">
                  <div className="flex items-start gap-4">
                    <div className="text-3xl">{prof?.icon || '🤖'}</div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-lg font-semibold text-white">{m.role}</h3>
                        <span className={`text-xs px-2 py-0.5 rounded border capitalize ${statusColor}`}>
                          {m.status}
                        </span>
                      </div>
                      <div className="text-sm text-gray-500 mb-2">{prof?.name || m.agent_profile}</div>
                      {m.responsibilities && (
                        <p className="text-sm text-gray-400 mb-3">{m.responsibilities}</p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                          Profile: {m.agent_profile}
                        </span>
                        {(m.llm_model || prof?.llm_model) && (
                          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
                            LLM: {m.llm_model || prof?.llm_model}
                          </span>
                        )}
                        {(m.base_image || prof?.base_image) && (
                          <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            Image: {m.base_image || prof?.base_image}
                          </span>
                        )}
                        <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-gray-500/10 text-gray-400 border border-gray-500/20">
                          Order: {m.execution_order}
                        </span>
                      </div>
                      {m.task_id && (
                        <div className="mt-3 pt-3 border-t border-[#1a1a2a]">
                          <Link href={`/tasks/${m.task_id}`}
                            className="text-sm text-indigo-400 hover:text-indigo-300">
                            View Task: {m.task_id} →
                          </Link>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* ── CEREMONIES TAB ─── */}
        {activeTab === 'ceremonies' && (
          <div className="space-y-3">
            {tf.ceremonies.length === 0 ? (
              <div className="card p-12 text-center">
                <div className="text-4xl mb-3">🎭</div>
                <p className="text-gray-500 text-sm">No ceremonies defined. Agents run in parallel.</p>
              </div>
            ) : (
              tf.ceremonies
                .sort((a, b) => a.sequence_order - b.sequence_order)
                .map(c => {
                  const icon = CEREMONY_ICONS[c.ceremony_type] || '⚙️'
                  const statusColor = STATUS_COLORS[c.status] || STATUS_COLORS.pending
                  return (
                    <div key={c.id} className="card p-5">
                      <div className="flex items-center gap-3 mb-3">
                        <span className="text-2xl">{icon}</span>
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="font-semibold text-white">{c.name}</h3>
                            <span className={`text-xs px-2 py-0.5 rounded border capitalize ${statusColor}`}>
                              {c.status}
                            </span>
                          </div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            {c.ceremony_type} · {c.mode} · Timeout: {c.timeout_minutes}min
                          </div>
                        </div>
                        <span className="ml-auto text-xs text-gray-600 font-mono">#{c.sequence_order}</span>
                      </div>
                      {c.description && (
                        <p className="text-sm text-gray-400 mb-3">{c.description}</p>
                      )}
                      <div className="flex flex-wrap gap-2 text-[11px]">
                        <span className="font-mono px-2 py-0.5 rounded bg-[#12121a] text-gray-500 border border-[#232333]">
                          Trigger: {c.trigger_condition}
                        </span>
                        {c.participant_member_ids && (
                          <span className="font-mono px-2 py-0.5 rounded bg-[#12121a] text-gray-500 border border-[#232333]">
                            Participants: {c.participant_member_ids.join(', ')}
                          </span>
                        )}
                      </div>
                      {c.result_summary && (
                        <div className="mt-3 bg-[#0e0e14] rounded-lg p-3 border border-[#1a1a2a]">
                          <div className="text-[11px] text-gray-600 uppercase tracking-wider mb-1">Result</div>
                          <pre className="text-xs text-gray-400 whitespace-pre-wrap font-mono">{c.result_summary}</pre>
                        </div>
                      )}
                      {c.started_at && (
                        <div className="text-xs text-gray-600 mt-2">
                          Started: {new Date(c.started_at).toLocaleString()}
                          {c.completed_at && ` · Completed: ${new Date(c.completed_at).toLocaleString()}`}
                        </div>
                      )}
                    </div>
                  )
                })
            )}
          </div>
        )}

        {/* ── TIMELINE TAB ─── */}
        {activeTab === 'timeline' && (
          <div className="card p-5">
            <h3 className="font-semibold text-white mb-4">Execution Timeline</h3>
            <div className="relative pl-6">
              {/* Created */}
              <TimelineEvent
                time={tf.created_at}
                title="Task Force Created"
                desc={`${tf.members.length} members, ${tf.ceremonies.length} ceremonies`}
                icon="📝"
              />
              {/* Started */}
              {tf.started_at && (
                <TimelineEvent
                  time={tf.started_at}
                  title="Task Force Launched"
                  desc={`Execution environment: ${tf.execution_environment}`}
                  icon="🚀"
                />
              )}
              {/* Member tasks */}
              {tf.members.filter(m => m.task_id).map(m => {
                const prof = profileMap[m.agent_profile]
                return (
                  <TimelineEvent
                    key={m.id}
                    time={tf.started_at || tf.created_at}
                    title={`${m.role} task started`}
                    desc={`${prof?.name || m.agent_profile} → ${m.task_id}`}
                    icon={prof?.icon || '🤖'}
                    status={m.status}
                    linkTo={`/tasks/${m.task_id}`}
                  />
                )
              })}
              {/* Ceremonies */}
              {tf.ceremonies.filter(c => c.started_at).map(c => (
                <TimelineEvent
                  key={c.id}
                  time={c.started_at!}
                  title={`Ceremony: ${c.name}`}
                  desc={`${c.ceremony_type} (${c.mode})`}
                  icon={CEREMONY_ICONS[c.ceremony_type] || '⚙️'}
                  status={c.status}
                />
              ))}
              {/* Completed */}
              {tf.completed_at && (
                <TimelineEvent
                  time={tf.completed_at}
                  title="Task Force Completed"
                  desc="All agents and ceremonies finished"
                  icon="✅"
                />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Timeline Event ───────────────────────────────────────

function TimelineEvent({
  time, title, desc, icon, status, linkTo,
}: {
  time: string
  title: string
  desc: string
  icon: string
  status?: string
  linkTo?: string
}) {
  return (
    <div className="flex items-start gap-3 mb-4 last:mb-0 relative">
      <div className="absolute -left-6 top-0 bottom-0 w-0.5 bg-[#232333]" />
      <div className="absolute -left-[30px] w-3 h-3 rounded-full bg-[#232333] border-2 border-[#16161e] mt-1.5" />
      <div className="flex-1 bg-[#0e0e14] rounded-lg p-3 border border-[#1a1a2a]">
        <div className="flex items-center gap-2 mb-1">
          <span>{icon}</span>
          <span className="text-sm font-medium text-white">{title}</span>
          {status && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded border capitalize ${
              STATUS_COLORS[status] || STATUS_COLORS.pending
            }`}>
              {status}
            </span>
          )}
        </div>
        <p className="text-xs text-gray-500">{desc}</p>
        <div className="flex items-center justify-between mt-1">
          <span className="text-[11px] text-gray-600">{new Date(time).toLocaleString()}</span>
          {linkTo && (
            <Link href={linkTo} className="text-[11px] text-indigo-400 hover:text-indigo-300">View →</Link>
          )}
        </div>
      </div>
    </div>
  )
}
