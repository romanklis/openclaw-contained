'use client'

interface CycleGroupCardProps {
  cycles: { message: any; meta: any }[]
  onExpand: (cycleNumber: number) => void
}

function formatDuration(seconds: number | null | undefined) {
  if (!seconds) return '—'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return minutes < 60 ? `${minutes}m ${rest}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

export default function CycleGroupCard({ cycles, onExpand }: CycleGroupCardProps) {
  const numbers = cycles.map(({ meta }) => meta.cycle_number)
  const label = numbers.length === 2
    ? `CYCLE ${numbers[0]} & ${numbers[1]}`
    : `CYCLES ${numbers[0]}–${numbers[numbers.length - 1]}`
  const statuses = new Set(cycles.map(({ meta }) => meta.status))
  const groupStatus = statuses.size === 1 ? Array.from(statuses)[0]?.toUpperCase() : 'MIXED'

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-gray-800 bg-[#0f1015]">
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-800/70 bg-[#14151c] px-3 py-2 text-[10px] font-semibold text-gray-400">
        <span className="rounded border border-cyan-700/40 bg-cyan-500/10 px-1.5 py-0.5 text-[8px] uppercase tracking-wide text-cyan-300">
          Agent · Results
        </span>
        <span>⚡ {label} • {groupStatus}</span>
      </div>
      <div className="divide-y divide-gray-800/60">
        {cycles.map(({ message, meta }) => (
          <div key={message.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-[10px] text-gray-500">
            <span className={meta.status === 'failed' ? 'text-red-300' : meta.flagged ? 'text-amber-300' : 'text-emerald-300'}>
              ▸ Cycle {meta.cycle_number} finished
            </span>
            <span>{meta.status}{meta.flagged ? ' • needs review' : ''}</span>
            {meta.score !== null && meta.score !== undefined && <span>Score {meta.score}/100</span>}
            {meta.duration_seconds && <span>({formatDuration(meta.duration_seconds)})</span>}
            <span>{Array.isArray(meta.deliverables) ? meta.deliverables.length : 0} files generated</span>
            <button onClick={() => onExpand(meta.cycle_number)} className="ml-auto rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-cyan-300 hover:border-cyan-700">
              Expand ▾
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
