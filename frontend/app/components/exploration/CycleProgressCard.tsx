'use client'

interface CycleProgressCardProps {
  sessionStatus: string
  progress: any
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

export default function CycleProgressCard({ sessionStatus, progress }: CycleProgressCardProps) {
  if (!progress || !['running', 'awaiting_input'].includes(sessionStatus)) return null
  const done = progress.completed_nodes || 0
  const total = progress.total_nodes || 0
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  const running = progress.nodes?.find((node: any) => node.status === 'running')

  return (
    <div className="my-2 rounded-lg border border-gray-800 bg-[#0f1015]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-400" />
        </span>
        <span className="text-xs font-semibold text-gray-200">
          Cycle {progress.cycle_number} · {sessionStatus === 'awaiting_input' ? 'Needs input' : 'Running'}
        </span>
        <span className="text-[10px] text-gray-500">{done}/{total} steps</span>
        <span className="text-[10px] text-gray-500">{formatElapsed(progress.elapsed_seconds)}</span>
        {running?.turns !== null && running?.turns !== undefined && (
          <span className="ml-auto text-[10px] text-indigo-300">Turn {running.turns}</span>
        )}
      </div>
      <div className="mx-3 h-1 overflow-hidden rounded bg-gray-800">
        <div className="h-full bg-indigo-500 transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-2 divide-y divide-gray-800/60 border-t border-gray-800/60">
        {(progress.nodes || []).map((node: any) => {
          const isRunning = node.status === 'running'
          return (
            <div
              key={node.node_id}
              className={`flex items-start gap-2 px-3 py-1.5 text-[10px] ${isRunning ? 'border-l-2 border-indigo-500 bg-indigo-500/5' : ''}`}
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
                    {node.turns !== null && node.turns !== undefined ? `Turn ${node.turns}` : 'Working'}
                    {node.latest_activity ? ` · ${node.latest_activity}` : ''}
                  </div>
                )}
              </div>
              <span className="shrink-0 text-[9px] uppercase tracking-wide text-gray-600">{node.status}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
