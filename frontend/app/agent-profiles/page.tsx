'use client'

import { useState, useEffect, useCallback } from 'react'
import { API_GATEWAY } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────

interface AgentProfile {
  id: string
  name: string
  description: string
  base_image: string
  llm_model: string
  tags: string[]
  icon: string
  metadata: {
    runtime: string
    strengths: string[]
  }
  image_info: {
    dockerfile: string
    tag: string
    runtime: string
    description: string
    size_estimate: string
  } | null
}

const EMPTY_PROFILE: Omit<AgentProfile, 'image_info'> = {
  id: '',
  name: '',
  description: '',
  base_image: 'openclaw',
  llm_model: 'gemini-flash-latest',
  tags: [],
  icon: '🤖',
  metadata: { runtime: '', strengths: [] },
}

const IMAGE_COLORS: Record<string, { bg: string; text: string; border: string; glow: string }> = {
  openclaw:  { bg: 'bg-indigo-500/10',  text: 'text-indigo-400',  border: 'border-indigo-500/20',  glow: 'hover:shadow-indigo-500/5' },
  nanobot:   { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/20', glow: 'hover:shadow-emerald-500/5' },
  picoclaw:  { bg: 'bg-amber-500/10',   text: 'text-amber-400',   border: 'border-amber-500/20',   glow: 'hover:shadow-amber-500/5' },
  zeroclaw:  { bg: 'bg-red-500/10',     text: 'text-red-400',     border: 'border-red-500/20',     glow: 'hover:shadow-red-500/5' },
}

const BASE_IMAGES = ['openclaw', 'nanobot', 'picoclaw', 'zeroclaw']

// ─── Component ───────────────────────────────────────────────────

export default function AgentProfilesPage() {
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filterImage, setFilterImage] = useState<string>('all')

  // Edit / Create modal state
  const [editingProfile, setEditingProfile] = useState<Omit<AgentProfile, 'image_info'> | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // Editable form fields (comma-separated inputs)
  const [tagsInput, setTagsInput] = useState('')
  const [strengthsInput, setStrengthsInput] = useState('')

  const fetchProfiles = useCallback(async () => {
    try {
      const res = await fetch(`${API_GATEWAY}/v1/agent-profiles`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setProfiles(data.profiles || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profiles')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchProfiles() }, [fetchProfiles])

  // ── Edit helpers ────────────────────────────────────────────────

  const openEditor = (profile: AgentProfile) => {
    setEditingProfile({
      id: profile.id,
      name: profile.name,
      description: profile.description,
      base_image: profile.base_image,
      llm_model: profile.llm_model,
      tags: profile.tags,
      icon: profile.icon,
      metadata: { ...profile.metadata },
    })
    setTagsInput(profile.tags.join(', '))
    setStrengthsInput(profile.metadata.strengths.join(', '))
    setIsCreating(false)
    setSaveError(null)
  }

  const openCreate = () => {
    setEditingProfile({ ...EMPTY_PROFILE, metadata: { runtime: '', strengths: [] } })
    setTagsInput('')
    setStrengthsInput('')
    setIsCreating(true)
    setSaveError(null)
  }

  const closeEditor = () => {
    setEditingProfile(null)
    setSaveError(null)
    setDeleteConfirm(null)
  }

  const handleSave = async () => {
    if (!editingProfile) return
    setSaving(true)
    setSaveError(null)

    const payload = {
      ...editingProfile,
      tags: tagsInput.split(',').map(s => s.trim()).filter(Boolean),
      metadata: {
        ...editingProfile.metadata,
        strengths: strengthsInput.split(',').map(s => s.trim()).filter(Boolean),
      },
    }

    try {
      const url = isCreating
        ? `${API_GATEWAY}/v1/agent-profiles`
        : `${API_GATEWAY}/v1/agent-profiles/${editingProfile.id}`
      const method = isCreating ? 'POST' : 'PUT'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      closeEditor()
      await fetchProfiles()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (profileId: string) => {
    try {
      const res = await fetch(`${API_GATEWAY}/v1/agent-profiles/${profileId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      closeEditor()
      await fetchProfiles()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  const imageTypes = Array.from(new Set(profiles.map(p => p.base_image)))
  const filtered = filterImage === 'all'
    ? profiles
    : profiles.filter(p => p.base_image === filterImage)

  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold text-white mb-1">Agent Profiles</h1>
        <p className="text-sm text-gray-500 mt-8">Loading profiles…</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">Agent Profiles</h1>
          <p className="text-sm text-gray-500">
            Pre-configured agent identities — each pairs a Base Image with a specific LLM.
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary text-sm">
          + New Profile
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg p-3 mb-6">
          {error}
        </div>
      )}

      {/* Base Image Legend */}
      <div className="card p-4 mb-6">
        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Base Images</h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {Object.entries(IMAGE_COLORS).map(([key, colors]) => {
            const info = profiles.find(p => p.base_image === key)?.image_info
            return (
              <div key={key} className={`rounded-lg border ${colors.border} ${colors.bg} p-3`}>
                <div className={`text-sm font-semibold ${colors.text} capitalize`}>{key}</div>
                <div className="text-[11px] text-gray-500 mt-1">
                  {info?.runtime || '—'}
                </div>
                <div className="text-[10px] text-gray-600 mt-0.5">
                  {info?.size_estimate || '—'}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-4 bg-[#12121a] rounded-lg p-1 w-fit">
        <button
          onClick={() => setFilterImage('all')}
          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
            filterImage === 'all'
              ? 'bg-[#232333] text-white'
              : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          All ({profiles.length})
        </button>
        {imageTypes.map(img => {
          const colors = IMAGE_COLORS[img] || IMAGE_COLORS.openclaw
          const count = profiles.filter(p => p.base_image === img).length
          return (
            <button
              key={img}
              onClick={() => setFilterImage(img)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors capitalize ${
                filterImage === img
                  ? `bg-[#232333] ${colors.text}`
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {img} ({count})
            </button>
          )
        })}
      </div>

      {/* Profile Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {filtered.map(profile => {
          const colors = IMAGE_COLORS[profile.base_image] || IMAGE_COLORS.openclaw
          return (
            <div
              key={profile.id}
              className={`card p-5 border ${colors.border} ${colors.glow} hover:shadow-lg transition-all`}
            >
              <div className="flex items-start gap-3">
                <span className="text-2xl">{profile.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-semibold text-white">{profile.name}</h3>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${colors.bg} ${colors.text} border ${colors.border}`}>
                      {profile.base_image}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mb-3">{profile.description}</p>

                  {/* Technical Details */}
                  <div className="flex flex-wrap gap-2 mb-3">
                    <span className={`inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded ${colors.bg} ${colors.text} border ${colors.border}`}>
                      Runtime: {profile.metadata.runtime}
                    </span>
                    <span className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">
                      Model: {profile.llm_model}
                    </span>
                    {profile.image_info?.size_estimate && (
                      <span className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-gray-500/10 text-gray-400 border border-gray-500/20">
                        Size: {profile.image_info.size_estimate}
                      </span>
                    )}
                  </div>

                  {/* Strengths */}
                  {profile.metadata.strengths.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {profile.metadata.strengths.map(s => (
                        <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a2a] text-gray-400 border border-[#232333]">
                          {s}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Tags */}
                  {profile.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {profile.tags.map(tag => (
                        <span key={tag} className="text-[10px] text-gray-600 font-mono">
                          #{tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Profile ID */}
              <div className="mt-3 pt-3 border-t border-[#1a1a2a] flex items-center justify-between">
                <span className="text-[11px] text-gray-600 font-mono">{profile.id}</span>
                <button
                  onClick={() => openEditor(profile)}
                  className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors font-medium"
                >
                  ✏️ Edit
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {filtered.length === 0 && (
        <div className="card p-12 text-center">
          <div className="text-4xl mb-3">🤖</div>
          <p className="text-gray-500 text-sm">No agent profiles found.</p>
        </div>
      )}

      {/* ── Edit / Create Modal ────────────────────────────────── */}
      {editingProfile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-[#16161e] border border-[#232333] rounded-2xl shadow-2xl w-full max-w-xl mx-4 max-h-[90vh] overflow-y-auto">
            {/* Modal header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#232333]">
              <h2 className="text-lg font-semibold text-white">
                {isCreating ? '✨ Create Agent Profile' : `✏️ Edit "${editingProfile.name}"`}
              </h2>
              <button onClick={closeEditor} className="text-gray-500 hover:text-gray-300 text-xl">✕</button>
            </div>

            {/* Modal body */}
            <div className="px-6 py-5 space-y-4">
              {isCreating && (
                <div>
                  <label className="block text-xs font-medium text-gray-400 mb-1">Profile ID</label>
                  <input
                    type="text"
                    value={editingProfile.id}
                    onChange={e => setEditingProfile({ ...editingProfile, id: e.target.value })}
                    className="input-field"
                    placeholder="e.g. my-custom-agent"
                  />
                  <p className="text-[10px] text-gray-600 mt-1">Must be unique, lowercase, hyphens only.</p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
                  <input
                    type="text"
                    value={editingProfile.name}
                    onChange={e => setEditingProfile({ ...editingProfile, name: e.target.value })}
                    className="input-field"
                    placeholder="My Agent"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-400 mb-1">Icon</label>
                  <input
                    type="text"
                    value={editingProfile.icon}
                    onChange={e => setEditingProfile({ ...editingProfile, icon: e.target.value })}
                    className="input-field"
                    placeholder="🤖"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1">Description</label>
                <textarea
                  value={editingProfile.description}
                  onChange={e => setEditingProfile({ ...editingProfile, description: e.target.value })}
                  className="input-field"
                  rows={2}
                  placeholder="What this agent is optimised for…"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-400 mb-1">Base Image</label>
                  <select
                    value={editingProfile.base_image}
                    onChange={e => setEditingProfile({ ...editingProfile, base_image: e.target.value })}
                    className="input-field"
                  >
                    {BASE_IMAGES.map(img => (
                      <option key={img} value={img}>{img}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-400 mb-1">LLM Model</label>
                  <input
                    type="text"
                    value={editingProfile.llm_model}
                    onChange={e => setEditingProfile({ ...editingProfile, llm_model: e.target.value })}
                    className="input-field"
                    placeholder="gemini-flash-latest"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1">Runtime Label</label>
                <input
                  type="text"
                  value={editingProfile.metadata.runtime}
                  onChange={e => setEditingProfile({
                    ...editingProfile,
                    metadata: { ...editingProfile.metadata, runtime: e.target.value },
                  })}
                  className="input-field"
                  placeholder="Python 3.11 (Debian)"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1">Tags (comma-separated)</label>
                <input
                  type="text"
                  value={tagsInput}
                  onChange={e => setTagsInput(e.target.value)}
                  className="input-field"
                  placeholder="general, coding, fast"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1">Strengths (comma-separated)</label>
                <input
                  type="text"
                  value={strengthsInput}
                  onChange={e => setStrengthsInput(e.target.value)}
                  className="input-field"
                  placeholder="Full Python ecosystem, Git & networking"
                />
              </div>

              {/* Preview badge */}
              <div className="rounded-lg bg-[#0e0e14] border border-[#232333] p-4">
                <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-2">Preview</div>
                <div className="flex items-center gap-2">
                  <span className="text-xl">{editingProfile.icon}</span>
                  <div>
                    <div className="text-sm font-semibold text-white">{editingProfile.name || 'Untitled'}</div>
                    <div className="text-[11px] text-gray-500">
                      {editingProfile.base_image} · {editingProfile.llm_model}
                    </div>
                  </div>
                </div>
              </div>

              {saveError && (
                <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg p-3">
                  {saveError}
                </div>
              )}
            </div>

            {/* Modal footer */}
            <div className="flex items-center justify-between px-6 py-4 border-t border-[#232333]">
              <div>
                {!isCreating && (
                  deleteConfirm === editingProfile.id ? (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-red-400">Are you sure?</span>
                      <button
                        onClick={() => handleDelete(editingProfile.id)}
                        className="text-xs text-red-500 hover:text-red-400 font-medium"
                      >
                        Yes, delete
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        className="text-xs text-gray-500 hover:text-gray-300"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setDeleteConfirm(editingProfile.id)}
                      className="text-xs text-red-500 hover:text-red-400 transition-colors"
                    >
                      🗑 Delete Profile
                    </button>
                  )
                )}
              </div>
              <div className="flex gap-3">
                <button onClick={closeEditor} className="btn-secondary text-sm">
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || !editingProfile.name || !editingProfile.llm_model || (isCreating && !editingProfile.id)}
                  className="btn-primary text-sm"
                >
                  {saving ? 'Saving…' : isCreating ? 'Create Profile' : 'Save Changes'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
