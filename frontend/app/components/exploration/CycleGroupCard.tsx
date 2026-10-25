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
    <div className="my-2 border-t border-gray-800/80 pt-2">
      <div className="mb-1 text-[10px] font-semibold text-gray-400">⚡ {label} • {groupStatus}</div>
      <div className="divide-y divide-gray-800/60">
        {cycles.map(({ message, meta }) => (
          <div key={message.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-[10px] text-gray-500">
            <span>▸ Cycle {meta.cycle_number} finished</span>
            <span className={meta.status === 'failed' ? 'text-red-300' : meta.flagged ? 'text-amber-300' : 'text-emerald-300'}>
              {meta.status}{meta.flagged ? ' • needs review' : ''}
            </span>
            {meta.score !== null && meta.score !== undefined && <span>Score {meta.score}/100</span>}
            {meta.duration_seconds && <span>({formatDuration(meta.duration_seconds)})</span>}
            <span>{Array.isArray(meta.deliverables) ? meta.deliverables.length : 0} files generated</span>
            <button onClick={() => onExpand(meta.cycle_number)} className="ml-auto text-cyan-400 hover:text-cyan-300">
              Expand
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
