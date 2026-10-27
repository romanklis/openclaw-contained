'use client'

import { useState } from 'react'
import Link from 'next/link'

interface CycleProposalCardProps {
  meta: Record<string, any>
  canRun: boolean
  onRun: (dagId: string) => void
}

export default function CycleProposalCard({ meta, canRun, onRun }: CycleProposalCardProps) {
  const [open, setOpen] = useState(false)
  const nodes = Array.isArray(meta.nodes) ? meta.nodes : []
  const stepCount = Number(meta.node_count || nodes.length || 0)

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-cyan-800/40 bg-cyan-950/10">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left hover:bg-cyan-950/20"
      >
        <span className="rounded border border-cyan-600/40 bg-cyan-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-cyan-300">
          Agent · Plan
        </span>
        <span className="text-xs font-medium text-gray-200">Planned cycle {meta.cycle_number}</span>
        <span className="text-[10px] text-gray-500">{stepCount} step{stepCount === 1 ? '' : 's'}</span>
        <span className="ml-auto text-[10px] text-gray-500">{open ? 'Hide details' : 'Click to review'}</span>
        <span className="text-cyan-300">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="border-t border-cyan-900/30 px-3 py-2">
          {nodes.length > 0 ? (
            <div>
              <div className="text-[9px] uppercase tracking-wide text-gray-500">Planned steps</div>
              <div className="mt-1 space-y-1">
                {nodes.slice(0, 3).map((node: any) => (
                  <div
                    key={node.node_id}
                    className="line-clamp-2 text-[10px] leading-relaxed text-gray-400"
                    title={node.description || node.node_id}
                  >
                    <span className="text-gray-600">{node.node_type || 'agent'}:</span>{' '}
                    {node.description || node.node_id}
                  </div>
                ))}
                {nodes.length > 3 && (
                  <div className="text-[9px] text-gray-600">+{nodes.length - 3} more steps</div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-[10px] text-gray-500">{stepCount} planned step{stepCount === 1 ? '' : 's'}</div>
          )}

          <div className="mt-2 flex flex-wrap gap-2">
            <Link
              href={`/dags/${meta.dag_id}`}
              className="rounded border border-gray-700 px-2.5 py-1 text-[10px] text-gray-300 hover:border-gray-500"
            >
              Review DAG →
            </Link>
            {canRun && (
              <button
                onClick={() => onRun(meta.dag_id)}
                className="rounded bg-emerald-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-emerald-500"
              >
                ▷ Run cycle
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
