'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { API } from '../lib/api'
import {
  explorationStatus,
  type ExplorationSession,
} from '../lib/explorations'
import {
  formatDuration,
  formatRelativeTime,
  formatTokens,
  sanitizeObjectiveText,
  type WorkflowRun,
  type WorkflowSummary,
} from '../lib/workflows'

interface ExplorationTableRowProps {
  session: ExplorationSession
  group: WorkflowSummary | null
  projectName?: string
}

function auditMeta(run: WorkflowRun | null): { label: string; icon: string; className: string } {
  if (!run) return { label: 'No audit', icon: '—', className: 'text-gray-600' }
  if (run.status === 'running') return { label: 'Running', icon: '◐', className: 'text-blue-400' }
  if (run.status === 'failed') return { label: 'Failed', icon: '✕', className: 'text-red-400' }
  if (run.status === 'cancelled') return { label: 'Cancelled', icon: '⊘', className: 'text-gray-500' }
  if (run.status === 'completed') {
    return run.flagged
      ? { label: 'Needs Review', icon: '⚠', className: 'text-amber-400' }
      : { label: 'Passed', icon: '✓', className: 'text-emerald-400' }
  }
  return { label: run.status, icon: '○', className: 'text-gray-400' }
}

export default function ExplorationTableRow({ session, group, projectName }: ExplorationTableRowProps) {
  const [showMenu, setShowMenu] = useState(false)
  const [tokenTotal, setTokenTotal] = useState<number | null>(null)
  const status = session.status === 'awaiting_feedback'
    ? { ...explorationStatus(session.status), label: 'Awaiting Review' }
    : explorationStatus(session.status)
  const latest = group?.latest_run || null
  const audit = auditMeta(latest)
  const objective = sanitizeObjectiveText(session.objective, 220)
  const latestDagId = session.latest_cycle?.dag_id || latest?.id || null
  const rowId = `workflow-${group?.id || session.id}`

  useEffect(() => {
    let cancelled = false
    fetch(`${API}/api/explorations/${session.id}/token-usage`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data?.total) setTokenTotal(Number(data.total.total || 0))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [session.id])

  return (
    <tr id={rowId} className="border-b border-gray-800/80 text-xs hover:bg-gray-900/50">
      <td className="px-3 py-2.5">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 shrink-0 text-sm">🧭</span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/explorations/${session.id}`}
                className="truncate font-medium text-gray-100 hover:text-cyan-200"
                title={session.title}
              >
                {session.title}
              </Link>
              {projectName && (
                <span className="shrink-0 text-[9px] uppercase tracking-wide text-gray-600">
                  {projectName}
                </span>
              )}
            </div>
            <div className="mt-0.5 max-w-[46vw] truncate text-[10px] text-gray-500" title={objective}>
              {objective}
            </div>
          </div>
        </div>
      </td>

      <td className="whitespace-nowrap px-3 py-2.5">
        <span className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] font-medium ${status.className}`}>
          {status.label}
        </span>
        <div className="mt-1 text-[9px] text-gray-600">
          Cycle {session.current_cycle} of {session.max_cycles}
        </div>
      </td>

      <td className="whitespace-nowrap px-3 py-2.5">
        <div className={`flex items-center gap-1.5 text-[10px] font-medium ${audit.className}`}>
          <span>{audit.icon}</span>
          <span>{audit.label}</span>
          {latest?.score !== null && latest?.score !== undefined && (
            <span className="text-gray-400">({latest.score})</span>
          )}
        </div>
        <div className="mt-1 text-[9px] text-gray-600">
          {latest
            ? `Run #${latest.run_number} • ${formatDuration(latest.duration_seconds)}`
            : 'No runs yet'}
          {tokenTotal !== null && tokenTotal > 0 && (
            <span className="ml-1 text-cyan-400/80">· 🪙 {formatTokens(tokenTotal)} tokens</span>
          )}
        </div>
      </td>

      <td className="whitespace-nowrap px-3 py-2.5 text-[10px] text-gray-500">
        {formatRelativeTime(session.last_activity_at || session.updated_at)}
      </td>

      <td className="whitespace-nowrap px-3 py-2.5" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-end gap-1.5">
          <Link
            href={`/explorations/${session.id}`}
            className="rounded bg-cyan-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-cyan-500"
          >
            💬 Open Chat
          </Link>
          <div className="relative">
            <button
              onClick={() => setShowMenu((value) => !value)}
              className="rounded border border-gray-700 px-1.5 py-1 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200"
              title="Exploration actions"
            >
              ···
            </button>
            {showMenu && (
              <div className="absolute right-0 z-40 mt-1 w-52 rounded border border-gray-700 bg-gray-900 shadow-xl">
                {latestDagId && (
                  <Link
                    href={`/dags/${latestDagId}`}
                    onClick={() => setShowMenu(false)}
                    className="block px-3 py-2 text-left text-[10px] text-gray-300 hover:bg-gray-800"
                  >
                    Open latest DAG
                  </Link>
                )}
                <Link
                  href={`/explorations/${session.id}`}
                  onClick={() => setShowMenu(false)}
                  className="block px-3 py-2 text-left text-[10px] text-cyan-300 hover:bg-gray-800"
                >
                  Open exploration chat
                </Link>
                <button
                  onClick={() => {
                    navigator.clipboard?.writeText?.(session.id)
                    setShowMenu(false)
                  }}
                  className="block w-full px-3 py-2 text-left text-[10px] text-gray-500 hover:bg-gray-800"
                >
                  Copy session id
                </button>
              </div>
            )}
          </div>
        </div>
      </td>
    </tr>
  )
}
