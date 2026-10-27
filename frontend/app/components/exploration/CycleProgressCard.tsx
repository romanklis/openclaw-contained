'use client'

import { TEMPORAL_UI } from '../../lib/api'

interface CycleProgressCardProps {
  sessionStatus: string
  progress: any
  thinkingBudget?: string
  qualityBudget?: string
}

function formatElapsed(seconds: number | null | undefined) {
  if (!seconds) return '0s'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}m ${rest}s`
}

function statusIcon(status: string) {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✕'
  if (status === 'running') return '◐'
  if (status === 'waiting_input') return '⏸'
  return '○'
}

export default function CycleProgressCard({ sessionStatus, progress, thinkingBudget, qualityBudget }: CycleProgressCardProps) {
  if (!progress || !['running', 'awaiting_input', 'awaiting_approval'].includes(sessionStatus)) return null
  const done = progress.completed_nodes || 0
  const total = progress.total_nodes || 0
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const running = progress.nodes?.find((node: any) => node.status === 'running')

  const activeNode = running || null
  const detailsHref = activeNode
    ? `/dags/${progress.dag_id}?node=${encodeURIComponent(activeNode.node_id)}`
    : `/dags/${progress.dag_id}`

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-indigo-900/50 bg-[#0f1015]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-indigo-900/40 bg-indigo-950/20 px-3 py-2">
        <span className="rounded border border-cyan-700/40 bg-cyan-500/10 px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wide text-cyan-300">
          Agent · Live
        </span>
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-400" />
        </span>
        <span className="text-xs font-semibold text-gray-200">
          Cycle {progress.cycle_number} · {sessionStatus === 'awaiting_input' ? 'Needs input' : sessionStatus === 'awaiting_approval' ? 'Needs approval' : 'Running'}
        </span>
        <span className="text-[10px] text-gray-500">{done}/{total} steps</span>
        <span className="text-[10px] text-gray-500">{formatElapsed(progress.elapsed_seconds)}</span>
        {(thinkingBudget || qualityBudget) && (
          <span className="text-[10px] text-gray-600">🧠 {thinkingBudget || 'standard'} · 🛡 {qualityBudget || 'standard'}</span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[10px]">
          <a href={detailsHref} className="text-cyan-300 hover:text-cyan-200">Details</a>
          <a href={`/dags/${progress.dag_id}`} className="text-cyan-300 hover:text-cyan-200">👁 DAG</a>
          {progress.workflow_id && (
            <a
              href={`${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(progress.workflow_id)}`}
              target="_blank"
              rel="noreferrer"
              className="text-blue-300 hover:text-blue-200"
              title={progress.workflow_id}
            >
              Temporal ↗
            </a>
          )}
          {running?.turns !== null && running?.turns !== undefined && (
            <span className="text-indigo-300">Turn {running.turns}{running.turn_budget ? ` / ${running.turn_budget}` : ''}</span>
          )}
        </div>
      </div>

      <div className="px-3 pt-2">
        <div className="h-1 overflow-hidden rounded bg-gray-800">
          <div className="h-full bg-indigo-500 transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="mt-2 divide-y divide-gray-800/60 border-t border-gray-800/60">
        {(progress.nodes || []).map((node: any) => {
          const isRunning = node.status === 'running'
          return (
            <a
              key={node.node_id}
              href={`/dags/${progress.dag_id}?node=${encodeURIComponent(node.node_id)}`}
              className={`flex items-start gap-2 px-3 py-1.5 text-[10px] hover:bg-gray-900/60 ${isRunning ? 'border-l-2 border-indigo-500 pl-2' : ''}`}
              title={`Open ${node.node_id} in DAG Studio`}
            >
              <span className={`w-4 shrink-0 ${node.status === 'completed' ? 'text-emerald-400' : node.status === 'failed' ? 'text-red-400' : isRunning ? 'text-indigo-400' : 'text-gray-600'}`}>
                {statusIcon(node.status)}
              </span>
              <div className="min-w-0 flex-1">
                <div className={`truncate ${isRunning ? 'text-gray-200' : node.status === 'completed' ? 'text-gray-500' : 'text-gray-600'}`}>
                  {node.description}
                </div>
                {isRunning && (node.turns !== null || node.latest_activity) && (
                  <div className="mt-0.5 truncate text-[9px] text-indigo-300/80">
                    {node.turns !== null && node.turns !== undefined ? `Turn ${node.turns}${node.turn_budget ? ` / ${node.turn_budget}` : ''}` : 'Working'}
                    {node.iteration_count ? ` · iter ${node.iteration_count}${node.max_iterations ? ` / ${node.max_iterations}` : ''}` : ''}
                    {node.approval_wait_ms ? ` · approval wait ${Math.round(node.approval_wait_ms / 1000)}s` : ''}
                    {node.latest_activity ? ` · ${node.latest_activity}` : ''}
                  </div>
                )}
              </div>
              <span className="shrink-0 text-[9px] uppercase tracking-wide text-gray-600">{node.status} ↗</span>
            </a>
          )
        })}
      </div>
    </div>
  )
}
