'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { API } from '../lib/api'
import {
  STAGE_META,
  formatDuration,
  formatRelativeTime,
  formatTokens,
  sanitizeObjectiveText,
  type WorkflowDetail,
  type WorkflowRun,
  type WorkflowSummary,
} from '../lib/workflows'

interface WorkflowTableRowProps {
  group: WorkflowSummary
  projectName?: string
  expanded: boolean
  variant?: 'workflow' | 'template'
  highlight?: boolean
  onToggleExpand: () => void
  onRun: (group: WorkflowSummary) => void
  onResume: (group: WorkflowSummary) => void
  onOpenLatest: (group: WorkflowSummary) => void
  onOpenTemplate: (group: WorkflowSummary) => void
  onEditTemplate?: (group: WorkflowSummary) => void
  onRerunProblem: (group: WorkflowSummary) => void
  onConvert: (group: WorkflowSummary) => void
  onMerge: (group: WorkflowSummary) => void
  onArchiveRun: (dagId: string) => void
  onDeleteRun: (dagId: string) => void
}

function latestStatus(group: WorkflowSummary): { label: string; className: string; icon: string } {
  const run = group.latest_run
  if (!run) return { label: 'No runs', className: 'text-gray-500', icon: '○' }
  if (run.status === 'running') return { label: 'Running', className: 'text-blue-400', icon: '◐' }
  if (run.status === 'failed') return { label: 'Failed', className: 'text-red-400', icon: '✕' }
  if (run.status === 'cancelled') return { label: 'Cancelled', className: 'text-gray-500', icon: '⊘' }
  if (run.status === 'completed') {
    return run.flagged
      ? { label: 'Needs Review', className: 'text-amber-400', icon: '⚠' }
      : { label: 'Completed', className: 'text-emerald-400', icon: '✓' }
  }
  return { label: run.status, className: 'text-gray-400', icon: '○' }
}

function runStatus(run: WorkflowRun): { label: string; className: string; icon: string } {
  if (run.status === 'running') return { label: 'Running', className: 'border-blue-500/40 bg-blue-500/10 text-blue-300', icon: '◐' }
  if (run.status === 'failed') return { label: 'Failed', className: 'border-red-500/40 bg-red-500/10 text-red-300', icon: '✕' }
  if (run.status === 'cancelled') return { label: 'Cancelled', className: 'border-gray-600 bg-gray-800/70 text-gray-400', icon: '⊘' }
  if (run.status === 'completed') {
    return run.flagged
      ? { label: 'Needs Review', className: 'border-amber-500/40 bg-amber-500/10 text-amber-300', icon: '⚠' }
      : { label: 'Passed', className: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300', icon: '✓' }
  }
  return { label: run.status, className: 'border-gray-700 bg-gray-800/70 text-gray-400', icon: '○' }
}

function templateStability(group: WorkflowSummary): { label: string; score: number | null; className: string; icon: string } {
  const score = group.latest_score ?? group.reliability_pct ?? null
  if (score === null) return { label: 'No data', score: null, className: 'text-gray-500', icon: '—' }
  if (group.latest_run?.status === 'failed') return { label: 'Fail', score, className: 'text-red-400', icon: '✕' }
  if (group.latest_run?.flagged) return { label: 'Review', score, className: 'text-amber-400', icon: '⚠' }
  if (score >= 90) return { label: 'Rock', score, className: 'text-emerald-400', icon: '✓' }
  if (score >= 70) return { label: 'Pass', score, className: 'text-emerald-300', icon: '✓' }
  return { label: 'Watch', score, className: 'text-amber-300', icon: '⚠' }
}

function dotClass(run: WorkflowRun): string {
  if (run.status === 'running') return 'bg-blue-400'
  if (run.status === 'failed') return 'bg-red-400'
  if (run.status === 'cancelled') return 'bg-gray-600'
  if (run.status === 'completed') return run.flagged ? 'bg-amber-400' : 'bg-emerald-400'
  return 'bg-gray-600'
}

export default function WorkflowTableRow({
  group,
  projectName,
  expanded,
  variant = 'workflow',
  highlight = false,
  onToggleExpand,
  onRun,
  onResume,
  onOpenLatest,
  onOpenTemplate,
  onEditTemplate,
  onRerunProblem,
  onConvert,
  onMerge,
  onArchiveRun,
  onDeleteRun,
}: WorkflowTableRowProps) {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [tokens, setTokens] = useState<Record<string, { total: number } | null>>({})
  const [tokensLoaded, setTokensLoaded] = useState(false)
  const [loadingTokens, setLoadingTokens] = useState(false)
  const [showMenu, setShowMenu] = useState(false)
  const stage = STAGE_META[group.stage]
  const status = latestStatus(group)
  const stability = templateStability(group)
  const objective = sanitizeObjectiveText(group.objective, 220)
  const recent = [...group.recent_runs].reverse()
  const latest = group.latest_run
  const isExperiment = group.stage === 'experimenting'
  const isTemplate = variant === 'template'

  useEffect(() => {
    setDetail(null)
    setTokens({})
    setTokensLoaded(false)
    setShowMenu(false)
    setLoadingTokens(false)
    setLoadingDetail(false)
  }, [group.id, group.run_count, group.updated_at])

  useEffect(() => {
    if (!expanded || detail) return
    let cancelled = false
    setLoadingDetail(true)
    fetch(`${API}/api/workflows/${group.id}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (!cancelled && data) setDetail(data) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingDetail(false) })
    return () => { cancelled = true }
  }, [expanded, group.id, detail])

  useEffect(() => {
    if (!expanded || tokensLoaded) return
    let cancelled = false
    setLoadingTokens(true)
    fetch(`${API}/api/workflows/${group.id}/run-tokens`)
      .then((res) => (res.ok ? res.json() : {}))
      .then((data) => { if (!cancelled) setTokens(data || {}) })
      .catch(() => { if (!cancelled) setTokens({}) })
      .finally(() => {
        if (!cancelled) {
          setTokensLoaded(true)
          setLoadingTokens(false)
        }
      })
    return () => { cancelled = true }
  }, [expanded, group.id, tokensLoaded])

  return (
    <>
      <tr
        id={`workflow-${group.id}`}
        className={`cursor-pointer border-b border-gray-800/80 text-xs transition-colors hover:bg-gray-900/50 ${
          expanded ? 'bg-gray-900/40' : highlight ? 'bg-amber-500/5' : ''
        }`}
        onClick={onToggleExpand}
      >
        <td className="px-3 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <span className="shrink-0 text-[9px] text-gray-600">{expanded ? '▼' : '▶'}</span>
            <span className="shrink-0 text-sm">{group.mode === 'exploration' ? '🧭' : stage.icon}</span>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium text-gray-100" title={group.title}>{group.title}</span>
                {group.locked && <span className="shrink-0 text-[9px] text-purple-300">🔒</span>}
                {projectName && <span className="shrink-0 text-[9px] uppercase tracking-wide text-gray-600">{projectName}</span>}
              </div>
              <div className="mt-0.5 max-w-[46vw] truncate text-[10px] text-gray-500" title={objective}>
                {objective}
              </div>
            </div>
          </div>
        </td>

        <td className="px-3 py-2.5">
          {isTemplate ? (
            <>
              <div className="text-[10px] font-medium text-purple-300">🔒 Locked Template</div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {group.step_count > 0
                  ? `${group.block_count}/${group.step_count} blocks (${group.determinism_pct}%)`
                  : 'No executable steps'}
              </div>
            </>
          ) : group.mode === 'exploration' ? (
            <>
              <div className="text-[10px] font-medium text-cyan-300">🧭 Exploration</div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                Maturity: {stage.short} · {group.determinism_pct}%
              </div>
            </>
          ) : (
            <>
              <div className={`text-[10px] font-medium ${group.stage === 'hardened' ? 'text-emerald-300' : group.stage === 'locked' ? 'text-purple-300' : 'text-amber-300'}`}>
                {stage.icon} {stage.short}
              </div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {group.step_count > 0
                  ? `${group.block_count}/${group.step_count} blocks (${group.determinism_pct}%)`
                  : 'No executable steps'}
              </div>
            </>
          )}
        </td>

        <td className="px-3 py-2.5">
          {isTemplate ? (
            <>
              <div className={`flex items-center gap-1 text-[10px] font-medium ${stability.className}`}>
                <span>{stability.icon}</span>
                {stability.score === null ? (
                  <span>{stability.label}</span>
                ) : (
                  <>
                    <span>{stability.score}</span>
                    <span className="text-gray-500">/100</span>
                    <span>({stability.label})</span>
                  </>
                )}
              </div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {group.reliability_pct !== null
                  ? `Fleet reliability ${group.reliability_pct}%`
                  : 'No fleet history'}
              </div>
            </>
          ) : (
            <>
              <div className={`flex items-center gap-1.5 text-[10px] font-medium ${status.className}`}>
                <span>{status.icon}</span>
                <span>{status.label}</span>
                {group.latest_score !== null && <span className="text-gray-400">• {group.latest_score}/100</span>}
              </div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {latest ? `Run #${latest.run_number} • ${formatDuration(latest.duration_seconds)}` : '—'}
              </div>
            </>
          )}
        </td>

        <td className="px-3 py-2.5">
          {isTemplate ? (
            <>
              <button
                onClick={onToggleExpand}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-gray-300 hover:bg-gray-800"
              >
                {group.run_count} run{group.run_count === 1 ? '' : 's'} {expanded ? '▴' : '▾'}
              </button>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {group.completed_count} passed
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1.5">
                {recent.length === 0 && <span className="text-[10px] text-gray-600">—</span>}
                {recent.map((run) => (
                  <span
                    key={run.id}
                    className={`h-2 w-2 rounded-full ${dotClass(run)}`}
                    title={`Run #${run.run_number} • ${run.status}${run.score !== null ? ` • ${run.score}/100` : ''}`}
                  />
                ))}
              </div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {group.run_count} run{group.run_count === 1 ? '' : 's'} • {group.latest_node_counts.completed} passed
              </div>
            </>
          )}
        </td>

        <td className="whitespace-nowrap px-3 py-2.5" onClick={(event) => event.stopPropagation()}>
          {isTemplate ? (
            <>
              <div className={`flex items-center gap-1.5 text-[10px] font-medium ${status.className}`}>
                <span>{status.icon}</span>
                <span>{status.label}</span>
              </div>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {latest
                  ? `${formatRelativeTime(latest.completed_at || latest.created_at)} • ${formatDuration(latest.duration_seconds)}`
                  : '—'}
              </div>
            </>
          ) : (
            <>
              <button
                onClick={onToggleExpand}
                className="flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-gray-300 hover:bg-gray-800"
              >
                {group.run_count} run{group.run_count === 1 ? '' : 's'} {expanded ? '▴' : '▾'}
              </button>
              <div className="mt-0.5 text-[9px] text-gray-600">
                {formatRelativeTime(group.updated_at)}
              </div>
            </>
          )}
        </td>

        <td className="px-3 py-2.5" onClick={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-end gap-1.5">
            {group.mode === 'exploration' && group.exploration_session_id ? (
              <Link
                href={`/explorations/${group.exploration_session_id}`}
                className="rounded bg-cyan-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-cyan-500"
              >
                🧭 Open
              </Link>
            ) : (
              <>
                <button
                  onClick={() => (group.locked ? onRun(group) : isExperiment ? onResume(group) : onRun(group))}
                  disabled={!isTemplate && !latest}
                  className={`rounded px-2.5 py-1 text-[10px] font-semibold text-white disabled:opacity-40 ${
                    group.locked ? 'bg-purple-600 hover:bg-purple-500' : isExperiment ? 'bg-indigo-600 hover:bg-indigo-500' : 'bg-emerald-600 hover:bg-emerald-500'
                  }`}
                >
                  {isTemplate ? '▷ Run' : group.locked ? '▷ New Run' : isExperiment ? '↗ Resume' : '▷ Run'}
                </button>
                {isTemplate && onEditTemplate && (
                  <button
                    onClick={() => onEditTemplate(group)}
                    className="rounded border border-purple-700/60 px-2 py-1 text-[10px] font-medium text-purple-300 hover:border-purple-500 hover:text-purple-200"
                  >
                    ✎ Edit
                  </button>
                )}
              </>
            )}
            <div className="relative">
              <button
                onClick={() => setShowMenu((value) => !value)}
                className="rounded border border-gray-700 px-1.5 py-1 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200"
                title="Workflow actions"
              >
                ···
              </button>
              {showMenu && (
                <div className="absolute right-0 z-40 mt-1 w-56 rounded border border-gray-700 bg-gray-900 shadow-xl">
                  <button onClick={() => { setShowMenu(false); onOpenLatest(group) }} className="block w-full px-3 py-2 text-left text-[10px] text-gray-300 hover:bg-gray-800">
                    Open latest run
                  </button>
                  {group.locked && (
                    <button onClick={() => { setShowMenu(false); onOpenTemplate(group) }} className="block w-full px-3 py-2 text-left text-[10px] text-purple-300 hover:bg-gray-800">
                      Open locked template
                    </button>
                  )}
                  {group.latest_active_node_id && (
                    <button onClick={() => { setShowMenu(false); onRerunProblem(group) }} className="block w-full px-3 py-2 text-left text-[10px] text-amber-300 hover:bg-gray-800">
                      Open problem node
                    </button>
                  )}
                  {group.latest_nodes.some((node) => node.node_type === 'agent') && (
                    <button onClick={() => { setShowMenu(false); onConvert(group) }} className="block w-full px-3 py-2 text-left text-[10px] text-cyan-300 hover:bg-gray-800">
                      ⚡ Convert a step to block
                    </button>
                  )}
                  <button onClick={() => { setShowMenu(false); onToggleExpand() }} className="block w-full px-3 py-2 text-left text-[10px] text-cyan-300 hover:bg-gray-800">
                    {expanded ? 'Hide execution history' : 'Show execution history'}
                  </button>
                  <button onClick={() => { setShowMenu(false); onMerge(group) }} className="block w-full px-3 py-2 text-left text-[10px] text-indigo-300 hover:bg-gray-800">
                    Merge into this workflow…
                  </button>
                  {latest && (
                    <button onClick={() => { setShowMenu(false); onArchiveRun(latest.id) }} className="block w-full px-3 py-2 text-left text-[10px] text-gray-300 hover:bg-gray-800">
                      Archive latest run
                    </button>
                  )}
                  {latest && !latest.locked && (
                    <button onClick={() => { setShowMenu(false); onDeleteRun(latest.id) }} className="block w-full px-3 py-2 text-left text-[10px] text-red-300 hover:bg-gray-800">
                      Delete latest run
                    </button>
                  )}
                  <button
                    onClick={() => {
                      navigator.clipboard?.writeText?.(group.id)
                      setShowMenu(false)
                    }}
                    className="block w-full px-3 py-2 text-left text-[10px] text-gray-500 hover:bg-gray-800"
                  >
                    Copy workflow id
                  </button>
                </div>
              )}
            </div>
          </div>
        </td>
      </tr>

      {expanded && (
        <tr className="border-b border-gray-800 bg-gray-950/50">
          <td colSpan={6} className="px-4 py-3">
            <div className="rounded border border-gray-800 bg-gray-900/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-[9px] uppercase tracking-wide text-gray-500">
                    Execution history ({group.run_count} runs)
                  </div>
                  <div className="mt-0.5 text-[10px] text-gray-600">
                    Select a run to inspect its DAG, Deliverables, and Audit Review.
                  </div>
                </div>
                {loadingTokens && <span className="text-[10px] text-gray-500">Loading token usage…</span>}
              </div>

              <div className="mt-2">
                {loadingDetail && <div className="text-[10px] text-gray-500">Loading runs…</div>}
                {!loadingDetail && !detail && <div className="text-[10px] text-gray-600">No run history available.</div>}
                {!loadingDetail && detail && detail.runs.length === 0 && (
                  <div className="text-[10px] text-gray-600">No runs recorded for this workflow.</div>
                )}
                {!loadingDetail && detail && detail.runs.length > 0 && (
                  <div className="divide-y divide-gray-800/80">
                    {detail.runs.map((run) => {
                      const statusInfo = runStatus(run)
                      const tokenInfo = tokens[run.id]
                      const tokenText = tokenInfo
                        ? formatTokens(tokenInfo.total)
                        : loadingTokens
                          ? '…'
                          : '—'
                      return (
                        <div key={run.id} className="py-2 text-[10px]">
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="font-mono font-semibold text-gray-200">[RUN #{run.run_number}]</span>
                            <span className="text-gray-500">{formatRelativeTime(run.completed_at || run.created_at)}</span>
                            <span className="text-gray-500">⏱ {formatDuration(run.duration_seconds)}</span>
                            <span className="text-gray-500">🪙 {tokenText} tokens</span>
                            <span className={`ml-auto inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${statusInfo.className}`}>
                              <span>{statusInfo.icon}</span>
                              <span>{statusInfo.label}</span>
                              {run.score !== null && <span className="opacity-80">{run.score}</span>}
                            </span>
                            <Link
                              href={`/dags/${run.id}`}
                              className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:border-gray-500"
                            >
                              ↗ Inspect Run #{run.run_number}
                            </Link>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-gray-500">
                            <span>
                              ↳ Output: <span className="font-mono text-cyan-300/80">{run.key_output || '—'}</span>
                            </span>
                            <span className="min-w-0 max-w-[70%] truncate" title={run.blocker || undefined}>
                              Blocker: {run.blocker || '—'}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
