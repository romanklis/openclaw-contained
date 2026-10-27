'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { API } from '../lib/api'
import { useProject } from '../lib/ProjectContext'
import NewExplorationForm from '../components/NewExplorationForm'
import { explorationStatus, type ExplorationSession } from '../lib/explorations'

export default function ExplorationsPage() {
  const router = useRouter()
  const { activeProject } = useProject()
  const [sessions, setSessions] = useState<ExplorationSession[]>([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ limit: '100' })
      if (activeProject) qs.set('project_id', activeProject)
      const res = await fetch(`${API}/api/explorations?${qs}`)
      const data = await res.json().catch(() => [])
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      setSessions(Array.isArray(data) ? data : [])
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeProject])
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('new') === '1') setShowNew(true)
  }, [])

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.25em] text-indigo-400">TaskForge Studio</div>
          <h1 className="mt-2 text-2xl font-bold text-white">Explorations</h1>
          <p className="mt-1 max-w-2xl text-xs text-gray-500">
            Continuous, feedback-driven workflows. Each cycle is planned, reviewed, run, and then replanned from finalized outcomes.
          </p>
        </div>
        <button onClick={() => setShowNew((value) => !value)} className="btn-primary text-sm">
          + New Exploration
        </button>
      </div>

      {error && <div className="mb-3 rounded border border-red-500/30 bg-red-900/20 p-3 text-xs text-red-300">{error}</div>}

      {showNew && (
        <div className="mb-5">
          <NewExplorationForm onCreated={(sessionId) => router.push(`/explorations/${sessionId}`)} />
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-20 animate-pulse rounded-lg border border-gray-800 bg-gray-900/40" />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-700 p-10 text-center text-sm text-gray-500">
          No explorations yet. Start one above with an objective and optional files.
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map((session) => {
            const status = explorationStatus(session.status)
            return (
              <Link
                key={session.id}
                href={`/explorations/${session.id}`}
                className="block rounded-lg border border-gray-800 bg-[#12121a] p-4 transition-colors hover:border-gray-600"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-white">{session.title}</span>
                      <span className={`rounded border px-1.5 py-0.5 text-[9px] uppercase ${status.className}`}>{status.label}</span>
                      {session.model && <span className="text-[9px] text-gray-600">{session.model}</span>}
                    </div>
                    <p className="mt-1 line-clamp-1 text-xs text-gray-500">{session.objective}</p>
                  </div>
                  <div className="text-right text-[10px] text-gray-500">
                    <div>{session.cycle_count ?? session.current_cycle} cycle{(session.cycle_count ?? session.current_cycle) === 1 ? '' : 's'}</div>
                    <div>{session.last_activity_at ? new Date(session.last_activity_at).toLocaleString() : ''}</div>
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
