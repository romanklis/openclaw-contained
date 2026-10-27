'use client'

import { useEffect, useRef, useState } from 'react'
import { API } from '../../lib/api'
import {
  DEPLOYMENT_STATUS_LABEL,
  type DeployableApp,
} from '../../lib/explorations'

interface DeployDialogProps {
  candidate: DeployableApp
  sessionTitle: string
  onClose: () => void
  onDeployed: (deployment: any) => void
}

export default function DeployDialog({ candidate, sessionTitle, onClose, onDeployed }: DeployDialogProps) {
  const [name, setName] = useState(candidate.name || `${sessionTitle} — Cycle ${candidate.cycle_number}`)
  const [entrypoint, setEntrypoint] = useState(candidate.entrypoint)
  const [port, setPort] = useState(String(candidate.port))
  const [deploying, setDeploying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deployment, setDeployment] = useState<any>(null)
  const mounted = useRef(true)
  const timer = useRef<number | null>(null)
  const startRequested = useRef(false)
  const existing = candidate.existing_deployment || null
  const existingActive = Boolean(existing && ['pending_approval', 'approved', 'building'].includes(existing.status))

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [])

  const poll = async (id: string) => {
    try {
      const res = await fetch(`${API}/api/deployments/${id}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!mounted.current) return
      setDeployment(data)
      if (data.status === 'failed') {
        setError(data.error || 'Deployment failed')
        setDeploying(false)
        return
      }
      if (data.status === 'built' && !startRequested.current) {
        startRequested.current = true
        const start = await fetch(`${API}/api/deployments/${id}/start`, { method: 'POST' })
        if (!start.ok) {
          startRequested.current = false
          const err = await start.json().catch(() => ({}))
          throw new Error((err as any)?.detail || `Start failed (HTTP ${start.status})`)
        }
      }
      if (data.status === 'running') {
        setDeploying(false)
        onDeployed(data)
        return
      }
      timer.current = window.setTimeout(() => poll(id), 2500)
    } catch (err: any) {
      if (!mounted.current) return
      setError(err.message)
      setDeploying(false)
    }
  }

  const deploy = async () => {
    if (deploying) return
    if (existingActive) {
      setError('This deployment is already being prepared. Wait for it to finish first.')
      return
    }
    setDeploying(true)
    setError(null)
    try {
      if (existing && existing.status === 'running') {
        const stop = await fetch(`${API}/api/deployments/${existing.id}/stop`, { method: 'POST' })
        if (!stop.ok) {
          const current = await fetch(`${API}/api/deployments/${existing.id}`).then((res) => res.json().catch(() => ({})))
          if (!['stopped', 'built', 'failed'].includes((current as any)?.status)) {
            const err = await stop.json().catch(() => ({}))
            throw new Error((err as any)?.detail || `Stop failed (HTTP ${stop.status})`)
          }
        }
      }
      const create = await fetch(`${API}/api/deployments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: candidate.task_id,
          name: name.trim() || candidate.name,
          entrypoint: entrypoint.trim(),
          port: Number(port) || candidate.port,
          agent_image: candidate.agent_image || undefined,
        }),
      })
      const created = await create.json().catch(() => ({}))
      if (!create.ok) throw new Error((created as any)?.detail || `HTTP ${create.status}`)
      setDeployment(created)
      const approve = await fetch(`${API}/api/deployments/${created.id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved: true, notes: 'Deployed from exploration chat' }),
      })
      const approved = await approve.json().catch(() => ({}))
      if (!approve.ok) throw new Error((approved as any)?.detail || `HTTP ${approve.status}`)
      setDeployment(approved)
      window.setTimeout(() => poll(created.id), 1500)
    } catch (err: any) {
      setError(err.message)
      setDeploying(false)
    }
  }

  const status = deployment?.status || null
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={() => { if (!deploying) onClose() }}>
      <div className="absolute inset-0 bg-black/60" />
      <div
        className="relative flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#12121a]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-800 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-white">{existing ? 'Redeploy application' : 'Deploy application'}</div>
            <div className="mt-0.5 text-[10px] text-gray-500">Cycle {candidate.cycle_number} · {candidate.framework}</div>
          </div>
          <button onClick={onClose} disabled={deploying} className="text-gray-500 hover:text-gray-200 disabled:opacity-40">✕</button>
        </div>
        <div className="space-y-3 overflow-y-auto p-4">
          <div className="rounded border border-gray-800 bg-gray-900/40 p-2 text-[11px] text-gray-400">
            <div>App: <span className="font-mono text-gray-300">{candidate.app_path}</span></div>
            <div className="mt-0.5">
              Source: <span className="font-mono text-gray-500">{candidate.source_node || '—'}</span>
              {' · '}
              <span className="font-mono text-gray-500">{candidate.task_id}</span>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-400">Name</label>
            <input value={name} onChange={(event) => setName(event.target.value)} className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-200" />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-400">Entrypoint</label>
            <input value={entrypoint} onChange={(event) => setEntrypoint(event.target.value)} className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5 font-mono text-sm text-gray-200" />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-400">Container port</label>
            <input value={port} onChange={(event) => setPort(event.target.value)} type="number" className="w-32 rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-200" />
            <div className="mt-1 text-[10px] text-gray-500">Host port is assigned automatically when the deployment starts.</div>
          </div>
          {existing && (
            <div className="rounded border border-amber-700/40 bg-amber-950/20 p-2 text-[10px] text-amber-200">
              An existing deployment will be stopped and replaced. The new build is trial-checked before it starts.
            </div>
          )}
          {deployment && (
            <div className="rounded border border-gray-800 bg-gray-900/40 p-2 text-[11px]">
              <div className="flex items-center gap-2">
                <span className="text-gray-400">Status:</span>
                <span className="text-gray-200">{DEPLOYMENT_STATUS_LABEL[status] || status}</span>
                {deployment.url && (
                  <a href={deployment.url} target="_blank" rel="noreferrer" className="ml-auto text-cyan-300 hover:text-cyan-200">
                    Open app ↗
                  </a>
                )}
              </div>
              <div className="mt-1 text-gray-500">
                Container port: {deployment.port ?? port}
                {deployment.host_port ? ` · Published: http://localhost:${deployment.host_port}` : ''}
              </div>
              {deployment.error && <div className="mt-1 text-red-300">{deployment.error}</div>}
            </div>
          )}
          {error && <div className="rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}
          <p className="text-[10px] text-gray-500">
            This creates the deployment, trial-checks it, and starts it automatically. Trials and builds can take a few minutes.
          </p>
        </div>
        <div className="flex items-center justify-between border-t border-gray-800 px-4 py-3">
          <a href="/deployments" className="text-[10px] text-gray-500 hover:text-gray-300">Open Deployments →</a>
          <div className="flex gap-2">
            <button onClick={onClose} disabled={deploying} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 disabled:opacity-40">Close</button>
            <button
              onClick={deploy}
              disabled={deploying || existingActive || !entrypoint.trim() || !port}
              className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {deploying ? 'Deploying…' : existingActive ? 'In progress' : existing ? '↻ Redeploy & Start' : '🚀 Deploy & Start'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
