'use client'

export function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: 'bg-blue-400',
    completed: 'bg-emerald-400',
    failed: 'bg-red-400',
    stopped: 'bg-gray-500',
    created: 'bg-gray-500',
    pending_approval: 'bg-amber-400',
    approved: 'bg-indigo-400',
    building: 'bg-blue-400',
    built: 'bg-cyan-400',
  }
  const isAnimated = status === 'running' || status === 'building'
  return (
    <span className="relative flex h-2.5 w-2.5">
      {isAnimated && (
        <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${colors[status] || 'bg-gray-500'}`} />
      )}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${colors[status] || 'bg-gray-500'}`} />
    </span>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: 'bg-blue-500/15 text-blue-400 border-blue-500/20',
    completed: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20',
    failed: 'bg-red-500/15 text-red-400 border-red-500/20',
    stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/20',
    created: 'bg-gray-500/15 text-gray-400 border-gray-500/20',
    pending_approval: 'bg-amber-500/15 text-amber-400 border-amber-500/20',
    approved: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/20',
    building: 'bg-blue-500/15 text-blue-400 border-blue-500/20',
    built: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/20',
  }
  return (
    <span className={`badge border ${styles[status] || 'bg-gray-500/15 text-gray-400 border-gray-500/20'}`}>
      {status.replace('_', ' ')}
    </span>
  )
}
