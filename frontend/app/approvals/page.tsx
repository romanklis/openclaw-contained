'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { API } from '../lib/api'

interface CapabilityRequest {
  id: number
  task_id: string
  capability_type: string
  resource_name: string
  justification: string
  status: string
  requested_at: string
  reviewed_at?: string
  reviewed_by?: string
  decision?: string
  alternative_suggestion?: string
}

export default function ApprovalsPage() {
  const [requests, setRequests] = useState<CapabilityRequest[]>([])
  const [allRequests, setAllRequests] = useState<CapabilityRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'pending' | 'history'>('pending')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [reviewComment, setReviewComment] = useState('')
  const [altSuggestion, setAltSuggestion] = useState('')
  const [actionLoading, setActionLoading] = useState<number | null>(null)

  const fetchRequests = async () => {
    try {
      const [pendingRes, allRes] = await Promise.all([
        fetch(`${API}/api/capabilities/requests?status_filter=pending`).then((r) => r.json()),
        fetch(`${API}/api/capabilities/requests`).then((r) => r.json()).catch(() => []),
      ])
      setRequests(Array.isArray(pendingRes) ? pendingRes : [])
      setAllRequests(Array.isArray(allRes) ? allRes : [])
      setLoading(false)
    } catch (error) {
      console.error('Failed to fetch requests:', error)
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRequests()
    const interval = setInterval(fetchRequests, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleReview = async (requestId: number, decision: string) => {
    setActionLoading(requestId)
    try {
      await fetch(`${API}/api/capabilities/requests/${requestId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          comment: reviewComment || decision,
          alternative_suggestion: decision === 'alternative_suggested' ? altSuggestion : undefined,
          reviewed_by: 'admin',
        }),
      })
      setExpandedId(null)
      setReviewComment('')
      setAltSuggestion('')
      fetchRequests()
    } catch (error) {
      console.error('Review failed:', error)
    } finally {
      setActionLoading(null)
    }
  }

  const capTypeIcon = (type: string) => {
    const icons: Record<string, string> = {
      pip_package: '📦',
      apt_package: '🔧',
      network: '🌐',
      file_access: '📁',
    }
    return icons[type] || '🔑'
  }

  const decisionBadge = (status: string) => {
    const map: Record<string, { bg: string; text: string }> = {
      pending: { bg: 'bg-yellow-500/10 border-yellow-500/20', text: 'text-yellow-400' },
      approved: { bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-400' },
      denied: { bg: 'bg-red-500/10 border-red-500/20', text: 'text-red-400' },
      alternative_suggested: { bg: 'bg-blue-500/10 border-blue-500/20', text: 'text-blue-400' },
      auto_approved: { bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-400' },
    }
    const s = map[status] || map['pending']
    return (
      <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium border rounded-full ${s.bg} ${s.text} capitalize`}>
        {status.replace(/_/g, ' ')}
      </span>
    )
  }

  const reviewedHistory = allRequests.filter((r) => r.status !== 'pending')

  if (loading) {
    return (
      <div className="p-8 max-w-5xl mx-auto">
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-500 text-sm">Loading approval requests...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">Capability Approvals</h1>
        <p className="text-sm text-gray-500">
          Review agent requests for packages, network access, and other capabilities
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 bg-[#12121a] rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab('pending')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'pending' ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Pending {requests.length > 0 && (
            <span className="ml-1.5 bg-yellow-500/20 text-yellow-400 px-1.5 py-0.5 rounded-full text-xs">
              {requests.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab('history')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'history' ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          History ({reviewedHistory.length})
        </button>
      </div>

      {/* Pending */}
      {tab === 'pending' && (
        <div className="space-y-3">
          {requests.length === 0 ? (
            <div className="card p-12 text-center">
              <div className="text-4xl mb-3">✅</div>
              <p className="text-gray-500 text-sm">All caught up — no pending approvals</p>
            </div>
          ) : (
            requests.map((req) => {
              const isExpanded = expandedId === req.id
              const isLoading = actionLoading === req.id
              return (
                <div key={req.id} className="card animate-fade-in">
                  <div className="p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0 flex-1">
                        <span className="text-xl mt-0.5">{capTypeIcon(req.capability_type)}</span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="font-medium text-white">{req.resource_name}</span>
                            <span className="text-xs bg-[#12121a] border border-[#232333] text-gray-400 px-2 py-0.5 rounded capitalize">
                              {req.capability_type.replace(/_/g, ' ')}
                            </span>
                          </div>
                          <div className="text-xs text-gray-600 mb-2">
                            <Link href={`/tasks/${req.task_id}`} className="text-indigo-500 hover:text-indigo-400">
                              Task: {req.task_id}
                            </Link>
                            <span className="ml-3">{new Date(req.requested_at).toLocaleString()}</span>
                          </div>
                          <div className="bg-[#12121a] border border-[#1a1a2a] rounded-lg p-3 text-sm text-gray-300">
                            <span className="text-xs text-gray-500 block mb-1">Justification:</span>
                            {req.justification}
                          </div>
                        </div>
                      </div>

                      {!isExpanded && (
                        <button
                          onClick={() => setExpandedId(req.id)}
                          className="btn-primary text-xs shrink-0"
                        >
                          Review
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Expanded review panel */}
                  {isExpanded && (
                    <div className="border-t border-[#1a1a2a] p-5 bg-[#0d0d14] rounded-b-xl animate-fade-in">
                      <div className="space-y-3 mb-4">
                        <div>
                          <label className="block text-xs font-medium text-gray-400 mb-1.5">
                            Review Comment
                          </label>
                          <textarea
                            value={reviewComment}
                            onChange={(e) => setReviewComment(e.target.value)}
                            className="input-field"
                            rows={2}
                            placeholder="Add a comment about your decision..."
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-400 mb-1.5">
                            Alternative Suggestion
                          </label>
                          <input
                            type="text"
                            value={altSuggestion}
                            onChange={(e) => setAltSuggestion(e.target.value)}
                            className="input-field"
                            placeholder="e.g., Use 'polars' instead of 'pandas'"
                          />
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleReview(req.id, 'approved')}
                          disabled={isLoading}
                          className="btn-success text-xs"
                        >
                          {isLoading ? '...' : '✓ Approve'}
                        </button>
                        <button
                          onClick={() => handleReview(req.id, 'denied')}
                          disabled={isLoading}
                          className="btn-danger text-xs"
                        >
                          {isLoading ? '...' : '✕ Deny'}
                        </button>
                        <button
                          onClick={() => handleReview(req.id, 'alternative_suggested')}
                          disabled={isLoading || !altSuggestion.trim()}
                          className="btn-warning text-xs"
                        >
                          💡 Suggest Alt
                        </button>
                        <button
                          onClick={() => {
                            setExpandedId(null)
                            setReviewComment('')
                            setAltSuggestion('')
                          }}
                          className="btn-secondary text-xs"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}

      {/* History */}
      {tab === 'history' && (
        <div className="space-y-2">
          {reviewedHistory.length === 0 ? (
            <div className="card p-12 text-center">
              <p className="text-gray-500 text-sm">No review history yet</p>
            </div>
          ) : (
            reviewedHistory.map((req) => (
              <div key={req.id} className="card p-4">
                <div className="flex items-center gap-3">
                  <span className="text-lg">{capTypeIcon(req.capability_type)}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-medium text-white text-sm">{req.resource_name}</span>
                      {decisionBadge(req.status)}
                    </div>
                    <div className="text-xs text-gray-600">
                      <Link href={`/tasks/${req.task_id}`} className="text-indigo-500 hover:text-indigo-400">
                        {req.task_id}
                      </Link>
                      {req.reviewed_at && (
                        <span className="ml-2">Reviewed {new Date(req.reviewed_at).toLocaleString()}</span>
                      )}
                      {req.alternative_suggestion && (
                        <span className="ml-2 text-blue-400">Alt: {req.alternative_suggestion}</span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
