'use client'

import { useState, useEffect } from 'react'
import { API } from '../lib/api'

interface AgentImage {
  id: string
  name: string
  description: string
  tag: string
  enabled: boolean
  runtime: string
  capabilities: string[]
  best_for: string[]
  avoid_for: string[]
  created_at: string
}

export default function AgentImagesPage() {
  const [images, setImages] = useState<AgentImage[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const loadImages = async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/agent-images`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setImages(Array.isArray(data) ? data : [])
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadImages()
  }, [])

  const toggleEnabled = async (img: AgentImage) => {
    setBusyId(img.id)
    setError(null)
    try {
      const r = await fetch(`${API}/api/agent-images/${encodeURIComponent(img.id)}/${img.enabled ? 'disable' : 'enable'}`, { method: 'POST' })
      if (!r.ok) throw new Error(await r.text())
      await loadImages()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  const syncFromYaml = async () => {
    setMessage(null)
    setError(null)
    try {
      const r = await fetch(`${API}/api/agent-images/sync-from-yaml`, { method: 'POST' })
      if (!r.ok) throw new Error(await r.text())
      const res = await r.json()
      setMessage(`Synced from YAML — created ${res.created}, updated ${res.updated}, skipped ${res.skipped}`)
      await loadImages()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const chip = (t: string) => (
    <span key={t} className="bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded text-xs">{t}</span>
  )

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Agent Images</h1>
            <p className="text-gray-400 text-sm mt-1">
              Image catalog (DB) the planner can assign to DAG nodes and tasks.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={syncFromYaml} className="btn-secondary text-sm" title="Re-import base_images: from agent_profiles.yaml">
              ↻ Sync from YAML
            </button>
            <button onClick={loadImages} className="btn-primary text-sm">⟳ Refresh</button>
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded text-red-300 text-sm">{error}</div>
        )}
        {message && (
          <div className="mb-4 p-3 bg-green-900/40 border border-green-700 rounded text-green-300 text-sm">{message}</div>
        )}

        {loading && <p className="text-gray-400 text-sm">Loading…</p>}

        {!loading && images.length === 0 && (
          <p className="text-gray-500 text-sm py-12 text-center">No images in the catalog yet.</p>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {images.map((img) => (
            <div key={img.id} className={`bg-gray-800 rounded-lg p-4 border ${img.enabled ? 'border-gray-700' : 'border-red-900/50 opacity-75'}`}>
              <div className="flex items-start justify-between mb-1">
                <div>
                  <span className="font-medium text-white">{img.name}</span>
                  <span className="ml-2 text-xs text-gray-500 font-mono">{img.id}</span>
                </div>
                <button
                  onClick={() => toggleEnabled(img)}
                  disabled={busyId === img.id}
                  className={`px-2 py-0.5 rounded text-xs font-medium ${
                    img.enabled ? 'bg-green-800 hover:bg-green-700' : 'bg-gray-700 hover:bg-gray-600'
                  }`}
                  title={img.enabled ? 'Disable (planner will skip it)' : 'Enable'}
                >
                  {img.enabled ? '✅ Enabled' : '⏸ Disabled'}
                </button>
              </div>
              <div className="text-xs text-gray-500 font-mono mb-1">{img.tag}</div>
              {img.description && <p className="text-gray-400 text-sm mb-2">{img.description}</p>}
              {img.runtime && <div className="text-xs text-gray-500 mb-1">runtime: {img.runtime}</div>}
              {img.capabilities?.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-1">{img.capabilities.map(chip)}</div>
              )}
              {img.best_for?.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-1">
                  <span className="text-xs text-gray-600">best for:</span>
                  {img.best_for.map(chip)}
                </div>
              )}
              {img.avoid_for?.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  <span className="text-xs text-gray-600">avoid for:</span>
                  {img.avoid_for.map(chip)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
