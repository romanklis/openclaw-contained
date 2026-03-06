'use client'

import { useState, useEffect, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
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
  details?: {
    packages?: string[]
    original_type?: string
    iteration?: string
    reason?: string
    versions?: Record<string, string>
    task_description?: string
    [key: string]: any
  }
}

interface TaskInfo {
  id: string
  name?: string
  description?: string
  status?: string
}

export default function ApprovalsPage() {
  return (
    <Suspense fallback={
      <div className="p-8 max-w-5xl mx-auto">
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-500 text-sm">Loading approval requests...</div>
        </div>
      </div>
    }>
      <ApprovalsContent />
    </Suspense>
  )
}

function ApprovalsContent() {
  const searchParams = useSearchParams()
  const filterTaskId = searchParams.get('task_id')

  const [requests, setRequests] = useState<CapabilityRequest[]>([])
  const [allRequests, setAllRequests] = useState<CapabilityRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'pending' | 'history'>('pending')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [reviewComment, setReviewComment] = useState('')
  const [altSuggestion, setAltSuggestion] = useState('')
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const [taskCache, setTaskCache] = useState<Record<string, TaskInfo>>({})

  const fetchTaskInfo = async (taskId: string): Promise<TaskInfo | null> => {
    if (taskCache[taskId]) return taskCache[taskId]
    try {
      const res = await fetch(`${API}/api/tasks/${taskId}`)
      if (res.ok) {
        const data = await res.json()
        const info: TaskInfo = { id: data.id, name: data.name, description: data.description, status: data.status }
        setTaskCache(prev => ({ ...prev, [taskId]: info }))
        return info
      }
    } catch {}
    return null
  }

  const fetchRequests = async () => {
    try {
      const [pendingRes, allRes] = await Promise.all([
        fetch(`${API}/api/capabilities/requests?status_filter=pending`).then((r) => r.json()),
        fetch(`${API}/api/capabilities/requests`).then((r) => r.json()).catch(() => []),
      ])
      const pending = Array.isArray(pendingRes) ? pendingRes : []
      const all = Array.isArray(allRes) ? allRes : []
      setRequests(pending)
      setAllRequests(all)
      // Pre-fetch task info for all unique task_ids
      const taskIds = Array.from(new Set([...pending, ...all].map(r => r.task_id)))
      taskIds.forEach(id => fetchTaskInfo(id))
      // Auto-expand the first matching request when filtered by task_id
      if (filterTaskId && expandedId === null) {
        const match = pending.find(r => r.task_id === filterTaskId)
        if (match) setExpandedId(match.id)
      }
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

  // Apply task_id filter from URL when present
  const displayedPending = filterTaskId
    ? requests.filter(r => r.task_id === filterTaskId)
    : requests
  const displayedHistory = filterTaskId
    ? reviewedHistory.filter(r => r.task_id === filterTaskId)
    : reviewedHistory

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

      {/* Task filter banner */}
      {filterTaskId && (
        <div className="mb-4 flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/20 rounded-lg px-4 py-2">
          <span className="text-indigo-400 text-sm">
            Showing requests for task <code className="font-mono text-indigo-300">{filterTaskId}</code>
          </span>
          <Link
            href="/approvals"
            className="ml-auto text-xs text-gray-400 hover:text-gray-200 underline"
          >
            Show all
          </Link>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-5 bg-[#12121a] rounded-lg p-1 w-fit">
        <button
          onClick={() => setTab('pending')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'pending' ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Pending {displayedPending.length > 0 && (
            <span className="ml-1.5 bg-yellow-500/20 text-yellow-400 px-1.5 py-0.5 rounded-full text-xs">
              {displayedPending.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab('history')}
          className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
            tab === 'history' ? 'bg-[#232333] text-white' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          History ({displayedHistory.length})
        </button>
      </div>

      {/* Pending */}
      {tab === 'pending' && (
        <div className="space-y-3">
          {displayedPending.length === 0 ? (
            <div className="card p-12 text-center">
              <div className="text-4xl mb-3">✅</div>
              <p className="text-gray-500 text-sm">{filterTaskId ? 'No pending approvals for this task' : 'All caught up — no pending approvals'}</p>
            </div>
          ) : (
            displayedPending.map((req) => {
              const isExpanded = expandedId === req.id
              const isLoading = actionLoading === req.id
              const task = taskCache[req.task_id]
              const packages = req.details?.packages || req.resource_name.split(',').map(s => s.trim())
              const versions = req.details?.versions || {}
              const iteration = req.details?.iteration || null
              const detailedReason = req.details?.reason || null

              return (
                <div key={req.id} className={`card animate-fade-in ${filterTaskId && req.task_id === filterTaskId ? 'ring-1 ring-indigo-500/50' : ''}`}>
                  <div className="p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex items-start gap-3 min-w-0 flex-1">
                        <span className="text-xl mt-0.5">{capTypeIcon(req.capability_type)}</span>
                        <div className="min-w-0 flex-1">
                          {/* Package name + type badge */}
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="font-medium text-white">{req.resource_name}</span>
                            <span className="text-xs bg-[#12121a] border border-[#232333] text-gray-400 px-2 py-0.5 rounded capitalize">
                              {req.capability_type.replace(/_/g, ' ')}
                            </span>
                            {iteration && (
                              <span className="text-xs bg-blue-500/10 border border-blue-500/20 text-blue-400 px-2 py-0.5 rounded">
                                Iteration {iteration}
                              </span>
                            )}
                          </div>

                          {/* Task link + timestamp */}
                          <div className="text-xs text-gray-600 mb-3">
                            <Link href={`/tasks/${req.task_id}`} className="text-indigo-500 hover:text-indigo-400">
                              Task: {req.task_id}
                            </Link>
                            <span className="ml-3">{new Date(req.requested_at).toLocaleString()}</span>
                          </div>

                          {/* Task context */}
                          {task && task.description && (
                            <div className="bg-indigo-500/5 border border-indigo-500/15 rounded-lg p-3 mb-3">
                              <div className="text-xs text-indigo-400 font-medium mb-1">📋 Task Description</div>
                              <div className="text-sm text-gray-300">{task.description}</div>
                            </div>
                          )}

                          {/* Package details with versions */}
                          <div className="bg-[#12121a] border border-[#1a1a2a] rounded-lg p-3 mb-3">
                            <div className="text-xs text-gray-500 font-medium mb-2">📦 Requested Packages</div>
                            <div className="space-y-1.5">
                              {packages.map((pkg: string) => {
                                const version = versions[pkg]
                                const pkgType = req.details?.original_type || req.capability_type
                                return (
                                  <div key={pkg} className="flex items-center gap-2">
                                    <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                                      pkgType.includes('python') || pkgType.includes('pip') ? 'bg-blue-900/40 text-blue-300' :
                                      pkgType.includes('npm') ? 'bg-green-900/40 text-green-300' :
                                      pkgType.includes('apt') || pkgType.includes('system') ? 'bg-orange-900/40 text-orange-300' :
                                      'bg-gray-700 text-gray-300'
                                    }`}>
                                      {pkgType.includes('python') || pkgType.includes('pip') ? 'pip' :
                                       pkgType.includes('npm') ? 'npm' :
                                       pkgType.includes('apt') || pkgType.includes('system') ? 'apt' : 'pkg'}
                                    </span>
                                    <span className="font-mono text-sm text-white font-medium">{pkg}</span>
                                    {version ? (
                                      <span className="font-mono text-xs text-emerald-400">
                                        =={version}
                                      </span>
                                    ) : (
                                      <span className="text-xs text-amber-400/70 italic">
                                        (latest)
                                      </span>
                                    )}
                                  </div>
                                )
                              })}
                            </div>
                          </div>

                          {/* Justification */}
                          <div className="bg-[#12121a] border border-[#1a1a2a] rounded-lg p-3">
                            <div className="text-xs text-gray-500 font-medium mb-1">💬 Justification</div>
                            <div className="text-sm text-gray-300 whitespace-pre-wrap">{req.justification}</div>
                            {detailedReason && detailedReason !== req.justification && (
                              <div className="mt-2 pt-2 border-t border-[#232333]">
                                <div className="text-xs text-gray-500 font-medium mb-1">🔍 Detection Detail</div>
                                <div className="text-xs text-gray-400 font-mono whitespace-pre-wrap">{detailedReason}</div>
                              </div>
                            )}
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
          {displayedHistory.length === 0 ? (
            <div className="card p-12 text-center">
              <p className="text-gray-500 text-sm">No review history yet</p>
            </div>
          ) : (
            displayedHistory.map((req) => (
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
