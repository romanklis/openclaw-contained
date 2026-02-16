'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { StatusDot, StatusBadge } from '../components/StatusComponents'
import { API } from '../lib/api'

interface Deployment {
  id: string
  name: string
  task_id: string
  image_tag: string | null
  entrypoint: string | null
  port: number | null
  status: string
  container_id: string | null
  host_port: number | null
  url: string | null
  created_at: string
  approved_at: string | null
  built_at: string | null
  started_at: string | null
  stopped_at: string | null
  error: string | null
}

type ActionState = { id: string; action: string } | null

export default function DeploymentsPage() {
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const [actionLoading, setActionLoading] = useState<ActionState>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [approveNotes, setApproveNotes] = useState<string>('')
  const [showApproveModal, setShowApproveModal] = useState<string | null>(null)

  const fetchDeployments = async () => {
    try {
      const res = await fetch(`${API}/api/deployments`)
      const data = await res.json()
      setDeployments(Array.isArray(data) ? data : [])
    } catch (err) {
      console.error('Error fetching deployments:', err)
    }
  }

  useEffect(() => {
    fetchDeployments()
    const interval = setInterval(fetchDeployments, 4000)
    return () => clearInterval(interval)
  }, [])

  const approveDeployment = async (id: string, approved: boolean) => {
    setActionLoading({ id, action: approved ? 'approve' : 'deny' })
    try {
      await fetch(`${API}/api/deployments/${id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, notes: approveNotes || null }),
      })
      setShowApproveModal(null)
      setApproveNotes('')
      fetchDeployments()
    } catch (err) {
      console.error('Action failed:', err)
    } finally {
      setActionLoading(null)
    }
  }

  const startDeployment = async (id: string) => {
    setActionLoading({ id, action: 'start' })
    try {
      await fetch(`${API}/api/deployments/${id}/start`, { method: 'POST' })
      fetchDeployments()
    } catch (err) {
      console.error('Start failed:', err)
    } finally {
      setActionLoading(null)
    }
  }

  const stopDeployment = async (id: string) => {
    setActionLoading({ id, action: 'stop' })
    try {
      await fetch(`${API}/api/deployments/${id}/stop`, { method: 'POST' })
      fetchDeployments()
    } catch (err) {
      console.error('Stop failed:', err)
    } finally {
      setActionLoading(null)
    }
  }

  const allStatuses = ['all', 'pending_approval', 'approved', 'building', 'built', 'running', 'stopped', 'failed']

  const filtered = statusFilter === 'all'
    ? deployments
    : deployments.filter((d) => d.status === statusFilter)

  const counts: Record<string, number> = { all: deployments.length }
  for (const d of deployments) {
    counts[d.status] = (counts[d.status] || 0) + 1
  }

  const statusLabel = (s: string) => s.replace(/_/g, ' ')

  const timeSince = (iso: string) => {
    const diff = Date.now() - new Date(iso).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    return `${Math.floor(h / 24)}d ago`
  }

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">Deployments</h1>
        <p className="text-sm text-gray-500">Manage agent-created deployments — approve, build, start, and stop</p>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        {[
          { label: 'Total', value: deployments.length, icon: '📦' },
          { label: 'Running', value: counts['running'] || 0, icon: '🟢' },
          { label: 'Pending Approval', value: counts['pending_approval'] || 0, icon: '⏳' },
          { label: 'Failed', value: counts['failed'] || 0, icon: '❌' },
        ].map((s) => (
          <div key={s.label} className="stat-card">
            <div className="flex items-center gap-2">
              <span>{s.icon}</span>
              <div>
                <div className="text-xl font-bold text-white">{s.value}</div>
                <div className="text-xs text-gray-500">{s.label}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-4 bg-[#12121a] rounded-lg p-1 w-fit flex-wrap">
        {allStatuses.map((f) => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors capitalize whitespace-nowrap ${
              statusFilter === f
                ? 'bg-[#232333] text-white'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {statusLabel(f)} {counts[f] ? `(${counts[f]})` : ''}
          </button>
        ))}
      </div>

      {/* Deployment List */}
      <div className="space-y-3">
        {filtered.length === 0 ? (
          <div className="card p-12 text-center">
            <div className="text-4xl mb-3">🚀</div>
            <p className="text-gray-500 text-sm">
              {statusFilter === 'all'
                ? 'No deployments yet. Deployments are created when agents emit DEPLOYMENT_REQUEST.'
                : `No ${statusLabel(statusFilter)} deployments`}
            </p>
          </div>
        ) : (
          filtered.map((dep) => {
            const isLoading = actionLoading?.id === dep.id
            return (
              <div key={dep.id} className="card p-5 animate-fade-in">
                <div className="flex items-start justify-between gap-4">
                  {/* Left: Info */}
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <StatusDot status={dep.status} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="font-medium text-white">{dep.name}</span>
                        <StatusBadge status={dep.status} />
                        {dep.status === 'running' && dep.host_port && (
                          <a
                            href={`http://localhost:${dep.host_port}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-xs bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full hover:bg-emerald-500/20 transition-colors"
                          >
                            <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                            localhost:{dep.host_port} ↗
                          </a>
                        )}
                      </div>

                      <div className="flex items-center gap-3 text-xs text-gray-600 mb-1">
                        <span className="font-mono">{dep.id}</span>
                        <span>•</span>
                        <Link
                          href={`/tasks/${dep.task_id}`}
                          className="text-indigo-500 hover:text-indigo-400"
                        >
                          Task: {dep.task_id}
                        </Link>
                      </div>

                      <div className="flex items-center gap-3 text-xs text-gray-600 flex-wrap">
                        {dep.entrypoint && (
                          <span className="font-mono bg-[#12121a] px-1.5 py-0.5 rounded">
                            {dep.entrypoint}
                          </span>
                        )}
                        {dep.port && <span>Port: {dep.port}</span>}
                        {dep.image_tag && (
                          <span className="text-gray-700">Image: {dep.image_tag}</span>
                        )}
                        <span>{timeSince(dep.created_at)}</span>
                      </div>

                      {/* Timeline */}
                      <div className="flex items-center gap-2 mt-2 text-xs text-gray-700 flex-wrap">
                        <span>Created {new Date(dep.created_at).toLocaleTimeString()}</span>
                        {dep.approved_at && (
                          <><span>→</span><span className="text-blue-400">Approved</span></>
                        )}
                        {dep.built_at && (
                          <><span>→</span><span className="text-purple-400">Built</span></>
                        )}
                        {dep.started_at && (
                          <><span>→</span><span className="text-emerald-400">Started</span></>
                        )}
                        {dep.stopped_at && (
                          <><span>→</span><span className="text-yellow-400">Stopped</span></>
                        )}
                      </div>

                      {/* Error */}
                      {dep.error && (
                        <div className="mt-2 text-xs bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg p-2">
                          {dep.error}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Right: Actions */}
                  <div className="flex flex-col gap-2 shrink-0">
                    {dep.status === 'pending_approval' && (
                      <>
                        <button
                          onClick={() => setShowApproveModal(dep.id)}
                          disabled={isLoading}
                          className="btn-success text-xs"
                        >
                          {isLoading && actionLoading?.action === 'approve' ? 'Approving...' : '✓ Approve'}
                        </button>
                        <button
                          onClick={() => approveDeployment(dep.id, false)}
                          disabled={isLoading}
                          className="btn-danger text-xs"
                        >
                          {isLoading && actionLoading?.action === 'deny' ? 'Denying...' : '✕ Deny'}
                        </button>
                      </>
                    )}
                    {(dep.status === 'built' || dep.status === 'stopped') && (
                      <button
                        onClick={() => startDeployment(dep.id)}
                        disabled={isLoading}
                        className="btn-success text-xs"
                      >
                        {isLoading && actionLoading?.action === 'start' ? 'Starting...' : '▶ Start'}
                      </button>
                    )}
                    {dep.status === 'running' && (
                      <button
                        onClick={() => stopDeployment(dep.id)}
                        disabled={isLoading}
                        className="btn-danger text-xs"
                      >
                        {isLoading && actionLoading?.action === 'stop' ? 'Stopping...' : '■ Stop'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Approve Modal */}
      {showApproveModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="card p-6 w-full max-w-md animate-fade-in">
            <h3 className="text-lg font-semibold text-white mb-4">Approve Deployment</h3>
            <p className="text-sm text-gray-400 mb-4">
              Approving will trigger an image build for this deployment.
            </p>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">Notes (optional)</label>
            <textarea
              value={approveNotes}
              onChange={(e) => setApproveNotes(e.target.value)}
              className="input-field mb-4"
              rows={3}
              placeholder="Add any notes for this approval..."
            />
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setShowApproveModal(null); setApproveNotes('') }}
                className="btn-secondary text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => approveDeployment(showApproveModal, true)}
                disabled={actionLoading !== null}
                className="btn-success text-sm"
              >
                {actionLoading ? 'Processing...' : 'Approve & Build'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
