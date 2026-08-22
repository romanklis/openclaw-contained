'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { StatusBadge } from '../components/StatusComponents'
import { API } from '../lib/api'

interface TemplateSkill {
  id: string
  dag_id: string
  node_id: string
  source_skill_id: string | null
  name: string
  description: string
  instructions: string
  params: string[]
  status: string
  reviewer_score: number | null
  review_notes: string
  tags: string[]
  created_at: string
  updated_at: string | null
}

function tskillBadge(status: string) {
  const colours: Record<string, string> = {
    draft: 'bg-amber-800/50 text-amber-200',
    active: 'bg-emerald-800/50 text-emerald-200',
    archived: 'bg-gray-700 text-gray-400',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-mono ${colours[status] ?? 'bg-gray-800 text-gray-300'}`}>{status}</span>
}

export default function TemplateSkillsPage() {
  const [skills, setSkills] = useState<TemplateSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<TemplateSkill | null>(null)
  const [editedInstructions, setEditedInstructions] = useState('')
  const [rating, setRating] = useState(3)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/skill-learning/template-skills`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSkills(await res.json())
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const open = (s: TemplateSkill) => {
    setSelected(s)
    setEditedInstructions(s.instructions)
    setRating(s.reviewer_score || 3)
    setNotes(s.review_notes || '')
  }

  const save = async (status: 'active' | 'archived' | 'draft') => {
    if (!selected) return
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/skill-learning/template-skills/${selected.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          status,
          reviewer_score: rating,
          review_notes: notes,
          edited_instructions: editedInstructions.trim() ? editedInstructions : null,
        }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      const updated = await res.json()
      setSkills(prev => prev.map(s => (s.id === updated.id ? updated : s)))
      setSelected(updated)
      setEditedInstructions(updated.instructions)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">Template Skills</h1>
        <Link href="/dags" className="text-sm text-gray-500 hover:text-gray-300">&larr; DAGs</Link>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Generalized, parameterized skills produced when a DAG is locked as a template. Review and approve them before use.
      </p>

      {error && <div className="bg-red-900/20 border border-red-500/30 rounded p-3 mb-4 text-red-300 text-sm">{error}</div>}

      {loading && <p className="text-gray-500 text-sm">Loading…</p>}

      {!loading && skills.length === 0 && (
        <div className="text-center text-gray-500 py-12">No template skills yet. Lock a DAG as a template to generate them.</div>
      )}

      <div className="space-y-3">
        {skills.map(s => (
          <button key={s.id} onClick={() => open(s)} className="card block w-full text-left hover:border-purple-500/50 transition-colors">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <span className="font-mono text-xs text-gray-500">{s.id}</span>
                  {tskillBadge(s.status)}
                  {s.reviewer_score != null && <span className="text-xs text-amber-300">★ {s.reviewer_score}/5</span>}
                </div>
                <div className="text-sm text-gray-200 font-medium">{s.name}</div>
                {s.description && <div className="text-xs text-gray-500">{s.description}</div>}
                <div className="text-xs text-gray-600 mt-1">
                  template: <span className="font-mono text-purple-300">{s.dag_id}</span> · node: <span className="font-mono">{s.node_id}</span>
                  {s.source_skill_id && <> · source: <span className="font-mono">{s.source_skill_id}</span></>}
                  {s.params.length > 0 && <> · params: <span className="font-mono">{s.params.join(', ')}</span></>}
                </div>
              </div>
            </div>
          </button>
        ))}
      </div>

      {selected && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center overflow-y-auto p-6" onClick={() => setSelected(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-lg max-w-3xl w-full p-5 my-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                <span className="font-mono text-xs text-gray-500">{selected.id}</span>
                {tskillBadge(selected.status)}
              </div>
              <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-gray-300">&times;</button>
            </div>
            <div className="text-lg font-semibold text-gray-100 mb-1">{selected.name}</div>
            {selected.description && <div className="text-sm text-gray-500 mb-2">{selected.description}</div>}
            <div className="text-xs text-gray-600 mb-3">
              template: <Link href={`/dags/${selected.dag_id}`} className="text-purple-300 hover:text-purple-200 font-mono">{selected.dag_id}</Link>
              {' '}· node: <span className="font-mono">{selected.node_id}</span>
              {selected.source_skill_id && <> · source: <span className="font-mono">{selected.source_skill_id}</span></>}
              {selected.params.length > 0 && <> · params: <span className="font-mono">{selected.params.join(', ')}</span></>}
            </div>

            <label className="block text-xs text-gray-400 mb-1">Instructions (generalized pseudo-code)</label>
            <textarea
              value={editedInstructions}
              onChange={(e) => setEditedInstructions(e.target.value)}
              rows={16}
              className="w-full bg-gray-800 border border-gray-700 rounded p-2 text-xs text-gray-300 font-mono whitespace-pre-wrap"
            />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Rating (1-5)</label>
                <select value={rating} onChange={(e) => setRating(Number(e.target.value))} className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm">
                  {[1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Review notes</label>
                <input value={notes} onChange={(e) => setNotes(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm" />
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setSelected(null)} className="px-3 py-1.5 text-xs rounded border border-gray-700 text-gray-300 hover:text-white">Close</button>
              <button onClick={() => save('draft')} disabled={saving} className="px-3 py-1.5 text-xs rounded border border-gray-700 text-gray-300 hover:text-white disabled:opacity-50">Save draft</button>
              <button onClick={() => save('archived')} disabled={saving} className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-white disabled:opacity-50">Archive</button>
              <button onClick={() => save('active')} disabled={saving} className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50">
                {saving ? 'Saving…' : 'Approve → Active'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
