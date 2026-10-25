'use client'

import Link from 'next/link'

export interface CapabilityApproval {
  id: number
  task_id: string
  capability_type: string
  resource_name: string
  justification: string
  status: string
  requested_at: string | null
  details?: Record<string, any>
  alternative_suggestion?: string | null
  decision_notes?: string | null
}

interface ApprovalCardProps {
  request: CapabilityApproval
  reviewing: boolean
  onReview: (decision: 'approved' | 'denied') => void
}

function title(request: CapabilityApproval) {
  const type = String(request.capability_type || '').replace(/_/g, ' ')
  return `${request.resource_name} (${type})`
}

export default function ApprovalCard({ request, reviewing, onReview }: ApprovalCardProps) {
  const packages = Array.isArray(request.details?.packages) ? request.details.packages : []
  return (
    <div className="my-3 rounded-md border border-amber-700/50 bg-[#1a150d] p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-300">⚠ Approval required</span>
        <span className="font-mono text-[10px] text-gray-400">{title(request)}</span>
        {request.requested_at && (
          <span className="ml-auto text-[9px] text-gray-600">{new Date(request.requested_at).toLocaleTimeString()}</span>
        )}
      </div>
      <div className="mt-1 text-xs text-gray-300">
        Install <span className="font-mono text-amber-200">{request.resource_name}</span>
        {packages.length > 0 && (
          <span className="text-gray-500"> · {packages.map((pkg: any) => pkg.name || pkg).filter(Boolean).join(', ')}</span>
        )}
      </div>
      {request.justification && (
        <p className="mt-1 line-clamp-3 text-[11px] text-gray-400" title={request.justification}>
          {request.justification}
        </p>
      )}
      {request.details?.task_description && (
        <details className="mt-1 text-[10px] text-gray-500">
          <summary className="cursor-pointer text-gray-500">Task context</summary>
          <p className="mt-1 whitespace-pre-wrap text-gray-400">{request.details.task_description}</p>
        </details>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px]">
        <button
          onClick={() => onReview('approved')}
          disabled={reviewing}
          className="rounded bg-emerald-600 px-2.5 py-1 font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {reviewing ? 'Approving…' : 'Approve'}
        </button>
        <button
          onClick={() => onReview('denied')}
          disabled={reviewing}
          className="rounded border border-red-700/50 px-2.5 py-1 text-red-300 hover:border-red-500 disabled:opacity-50"
        >
          Deny
        </button>
        <Link href="/approvals" className="text-gray-400 hover:text-gray-200">
          Open in Approvals →
        </Link>
      </div>
    </div>
  )
}
