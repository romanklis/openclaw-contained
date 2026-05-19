'use client'

import { useState, useEffect } from 'react'
import { API } from '../lib/api'

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentImage {
  id: string
  name: string
  description: string
  capabilities: string[]
}

interface SkillV2 {
  id: string
  image_id: string
  name: string
  description: string
  instructions: string
  status: 'draft' | 'active' | 'archived'
  source_type: string
  parent_id: string | null
  confidence_score: number
  usage_count: number
  success_count: number
  reviewer_score: number | null
  tags: string[]
  evidence_task_ids: string[]
  created_at: string
}

interface Demo {
  id: string
  image_id: string
  skill_id: string | null
  prompt: string
  extracted_procedure: Record<string, unknown> | null
  source_task_id: string | null
  status: string
  created_at: string
}

type Tab = 'tree' | 'review-queue' | 'demos' | 'capture'

// ── Helpers ──────────────────────────────────────────────────────────────────

function statusBadge(status: string) {
  const colours: Record<string, string> = {
    active: 'bg-green-900 text-green-300',
    draft: 'bg-yellow-900 text-yellow-300',
    archived: 'bg-gray-700 text-gray-400',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-mono ${colours[status] ?? 'bg-gray-800 text-gray-300'}`}>
      {status}
    </span>
  )
}

function confidenceBar(score: number) {
  const pct = Math.min(100, Math.max(0, score))
  const colour = pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-2 bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${colour}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400">{pct}</span>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function SkillStudioPage() {
  const [tab, setTab] = useState<Tab>('tree')
  const [images, setImages] = useState<AgentImage[]>([])
  const [selectedImage, setSelectedImage] = useState<string>('')
  const [skills, setSkills] = useState<SkillV2[]>([])
  const [reviewQueue, setReviewQueue] = useState<SkillV2[]>([])
  const [demos, setDemos] = useState<Demo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Demo capture form
  const [demoPrompt, setDemoPrompt] = useState('')
  const [demoTaskId, setDemoTaskId] = useState('')
  const [demoSubmitting, setDemoSubmitting] = useState(false)
  const [demoSuccess, setDemoSuccess] = useState<string | null>(null)

  // Review state
  const [reviewingSkill, setReviewingSkill] = useState<SkillV2 | null>(null)
  const [reviewNotes, setReviewNotes] = useState('')
  const [reviewRating, setReviewRating] = useState(3)
  const [editedInstructions, setEditedInstructions] = useState('')
  const [reviewSubmitting, setReviewSubmitting] = useState(false)

  // Manual skill creation
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [newSkillName, setNewSkillName] = useState('')
  const [newSkillDesc, setNewSkillDesc] = useState('')
  const [newSkillInstructions, setNewSkillInstructions] = useState('')
  const [newSkillTags, setNewSkillTags] = useState('')
  const [createSubmitting, setCreateSubmitting] = useState(false)

  // Load images on mount
  useEffect(() => {
    fetch(`${API}/api/agent-images`)
      .then(r => r.json())
      .then((imgs: AgentImage[]) => {
        setImages(imgs.filter(i => (i as any).enabled !== false))
        if (imgs.length > 0) setSelectedImage(imgs[0].id)
      })
      .catch(e => setError(e.message))
  }, [])

  // Load content when tab or image changes
  useEffect(() => {
    if (!selectedImage) return
    if (tab === 'tree') loadTree()
    if (tab === 'review-queue') loadReviewQueue()
    if (tab === 'demos') loadDemos()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, selectedImage])

  async function loadTree() {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/skill-learning/tree/${selectedImage}?active_only=false`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setSkills(await r.json())
    } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }

  async function loadReviewQueue() {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/skill-learning/review-queue?image_id=${selectedImage}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setReviewQueue(await r.json())
    } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }

  async function loadDemos() {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API}/api/skill-learning/demos?image_id=${selectedImage}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setDemos(await r.json())
    } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }

  async function submitDemo() {
    if (!demoPrompt.trim()) return
    setDemoSubmitting(true)
    setDemoSuccess(null)
    setError(null)
    try {
      const r = await fetch(`${API}/api/skill-learning/demos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_id: selectedImage,
          prompt: demoPrompt.trim(),
          source_task_id: demoTaskId.trim() || null,
        }),
      })
      if (!r.ok) throw new Error(await r.text())
      const demo: Demo = await r.json()
      setDemoSuccess(`Demo ${demo.id} submitted — extraction in progress`)
      setDemoPrompt('')
      setDemoTaskId('')
    } catch (e: any) { setError(e.message) } finally { setDemoSubmitting(false) }
  }

  async function submitReview(decision: 'approve' | 'reject' | 'request_changes') {
    if (!reviewingSkill) return
    setReviewSubmitting(true)
    try {
      const r = await fetch(`${API}/api/skill-learning/skills/${reviewingSkill.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          rating: reviewRating,
          notes: reviewNotes,
          edited_instructions: editedInstructions.trim() || null,
        }),
      })
      if (!r.ok) throw new Error(await r.text())
      setReviewingSkill(null)
      setReviewNotes('')
      setEditedInstructions('')
      loadReviewQueue()
      if (tab === 'tree') loadTree()
    } catch (e: any) { setError(e.message) } finally { setReviewSubmitting(false) }
  }

  async function createSkill() {
    if (!newSkillName.trim()) return
    setCreateSubmitting(true)
    try {
      const r = await fetch(`${API}/api/skill-learning/skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_id: selectedImage,
          name: newSkillName.trim(),
          description: newSkillDesc.trim(),
          instructions: newSkillInstructions.trim(),
          tags: newSkillTags.split(',').map(t => t.trim()).filter(Boolean),
          source_type: 'manual',
        }),
      })
      if (!r.ok) throw new Error(await r.text())
      setShowCreateForm(false)
      setNewSkillName(''); setNewSkillDesc(''); setNewSkillInstructions(''); setNewSkillTags('')
      loadReviewQueue()
    } catch (e: any) { setError(e.message) } finally { setCreateSubmitting(false) }
  }

  async function mineFromTask(taskId: string) {
    try {
      const r = await fetch(`${API}/api/skill-learning/mine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId }),
      })
      if (!r.ok) throw new Error(await r.text())
      const demo: Demo = await r.json()
      alert(`Mining started — demo ${demo.id} created`)
      loadDemos()
    } catch (e: any) { setError(e.message) }
  }

  async function promoteDemo(demoId: string) {
    try {
      const r = await fetch(`${API}/api/skill-learning/demos/${demoId}/promote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!r.ok) throw new Error(await r.text())
      loadDemos()
      loadReviewQueue()
    } catch (e: any) { setError(e.message) }
  }

  const tabClasses = (t: Tab) =>
    `px-4 py-2 text-sm font-medium rounded-t-md cursor-pointer transition-colors ${
      tab === t
        ? 'bg-gray-800 text-white border-b-2 border-blue-500'
        : 'text-gray-400 hover:text-white'
    }`

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Skill Studio</h1>
            <p className="text-gray-400 text-sm mt-1">
              Image-scoped skill tree — capture, review, and govern agent procedures
            </p>
          </div>
          <button
            onClick={() => setShowCreateForm(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-md text-sm font-medium"
          >
            + New Skill
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded text-red-300 text-sm">
            {error}
          </div>
        )}

        {/* Image selector */}
        <div className="mb-6 flex items-center gap-3">
          <label className="text-sm text-gray-400">Image:</label>
          <select
            value={selectedImage}
            onChange={e => setSelectedImage(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white"
          >
            {images.map(img => (
              <option key={img.id} value={img.id}>{img.id} — {img.name}</option>
            ))}
          </select>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-0 border-b border-gray-700">
          {(['tree', 'review-queue', 'demos', 'capture'] as Tab[]).map(t => (
            <button key={t} className={tabClasses(t)} onClick={() => setTab(t)}>
              {t === 'tree' ? 'Skill Tree' :
               t === 'review-queue' ? `Review Queue${reviewQueue.length > 0 ? ` (${reviewQueue.length})` : ''}` :
               t === 'demos' ? 'Demos' : 'Capture Demo'}
            </button>
          ))}
        </div>

        <div className="bg-gray-900 rounded-b-md rounded-tr-md border border-gray-700 border-t-0 p-4 min-h-[400px]">
          {loading && <p className="text-gray-400 text-sm">Loading…</p>}

          {/* ── Skill Tree ─────────────────────────────── */}
          {tab === 'tree' && !loading && (
            <div className="space-y-3">
              {skills.length === 0 && (
                <p className="text-gray-500 text-sm">No skills for this image yet. Capture a demo or create one manually.</p>
              )}
              {skills.map(skill => (
                <SkillCard key={skill.id} skill={skill} onReview={() => {
                  setReviewingSkill(skill)
                  setEditedInstructions(skill.instructions)
                  setTab('review-queue')
                }} />
              ))}
            </div>
          )}

          {/* ── Review Queue ────────────────────────────── */}
          {tab === 'review-queue' && !loading && (
            <div className="space-y-4">
              {reviewQueue.length === 0 && (
                <p className="text-gray-500 text-sm">No skills pending review.</p>
              )}
              {reviewQueue.map(skill => (
                <div key={skill.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <span className="font-medium text-white">{skill.name}</span>
                      <span className="ml-2 text-xs text-gray-500 font-mono">{skill.id}</span>
                    </div>
                    {statusBadge(skill.status)}
                  </div>
                  <p className="text-gray-400 text-sm mb-3">{skill.description}</p>
                  {skill.instructions && (
                    <pre className="bg-gray-900 rounded p-3 text-xs text-gray-300 overflow-x-auto mb-3 max-h-40 overflow-y-auto">
                      {skill.instructions}
                    </pre>
                  )}

                  {reviewingSkill?.id === skill.id ? (
                    <div className="space-y-3">
                      <textarea
                        className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white placeholder-gray-500 resize-none h-28"
                        placeholder="Edit instructions (optional)"
                        value={editedInstructions}
                        onChange={e => setEditedInstructions(e.target.value)}
                      />
                      <div className="flex items-center gap-3">
                        <label className="text-sm text-gray-400">Rating:</label>
                        {[1,2,3,4,5].map(n => (
                          <button
                            key={n}
                            onClick={() => setReviewRating(n)}
                            className={`w-7 h-7 rounded text-sm font-bold ${reviewRating === n ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'}`}
                          >{n}</button>
                        ))}
                      </div>
                      <textarea
                        className="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white placeholder-gray-500 resize-none h-16"
                        placeholder="Review notes (optional)"
                        value={reviewNotes}
                        onChange={e => setReviewNotes(e.target.value)}
                      />
                      <div className="flex gap-2">
                        <button
                          disabled={reviewSubmitting}
                          onClick={() => submitReview('approve')}
                          className="px-4 py-1.5 bg-green-700 hover:bg-green-600 rounded text-sm font-medium disabled:opacity-50"
                        >Approve</button>
                        <button
                          disabled={reviewSubmitting}
                          onClick={() => submitReview('request_changes')}
                          className="px-4 py-1.5 bg-yellow-700 hover:bg-yellow-600 rounded text-sm font-medium disabled:opacity-50"
                        >Request Changes</button>
                        <button
                          disabled={reviewSubmitting}
                          onClick={() => submitReview('reject')}
                          className="px-4 py-1.5 bg-red-800 hover:bg-red-700 rounded text-sm font-medium disabled:opacity-50"
                        >Reject</button>
                        <button
                          onClick={() => setReviewingSkill(null)}
                          className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm"
                        >Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={() => { setReviewingSkill(skill); setEditedInstructions(skill.instructions) }}
                      className="px-4 py-1.5 bg-blue-700 hover:bg-blue-600 rounded text-sm font-medium"
                    >Review</button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ── Demos ───────────────────────────────────── */}
          {tab === 'demos' && !loading && (
            <div className="space-y-3">
              <div className="flex items-center gap-3 mb-4">
                <input
                  type="text"
                  placeholder="Mine from task ID…"
                  className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white placeholder-gray-500 flex-1"
                  onKeyDown={e => {
                    if (e.key === 'Enter') mineFromTask((e.target as HTMLInputElement).value.trim())
                  }}
                />
                <span className="text-xs text-gray-500">Press Enter to mine</span>
              </div>
              {demos.length === 0 && (
                <p className="text-gray-500 text-sm">No demos for this image yet.</p>
              )}
              {demos.map(demo => (
                <div key={demo.id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                  <div className="flex items-start justify-between mb-2">
                    <span className="font-mono text-xs text-gray-500">{demo.id}</span>
                    <span className={`px-2 py-0.5 rounded text-xs font-mono ${
                      demo.status === 'linked' ? 'bg-green-900 text-green-300' :
                      demo.status === 'extracted' ? 'bg-blue-900 text-blue-300' :
                      'bg-gray-700 text-gray-400'
                    }`}>{demo.status}</span>
                  </div>
                  <p className="text-gray-300 text-sm mb-2 line-clamp-3">{demo.prompt}</p>
                  {demo.extracted_procedure && (
                    <details className="mb-2">
                      <summary className="text-xs text-blue-400 cursor-pointer">Extracted procedure</summary>
                      <pre className="mt-1 bg-gray-900 rounded p-2 text-xs text-gray-300 overflow-x-auto max-h-32 overflow-y-auto">
                        {JSON.stringify(demo.extracted_procedure, null, 2)}
                      </pre>
                    </details>
                  )}
                  {demo.status === 'extracted' && !demo.skill_id && (
                    <button
                      onClick={() => promoteDemo(demo.id)}
                      className="px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded text-xs font-medium"
                    >Promote to Skill Draft</button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ── Capture Demo ────────────────────────────── */}
          {tab === 'capture' && (
            <div className="max-w-2xl space-y-4">
              <p className="text-gray-400 text-sm">
                Describe a task you want the agent to learn. Be specific about the tools used,
                the steps taken, and the expected outcome. The system will extract a reusable procedure.
              </p>
              {demoSuccess && (
                <div className="p-3 bg-green-900/40 border border-green-700 rounded text-green-300 text-sm">
                  {demoSuccess}
                </div>
              )}
              <div>
                <label className="block text-sm text-gray-400 mb-1">Demonstration description *</label>
                <textarea
                  className="w-full bg-gray-800 border border-gray-700 rounded p-3 text-sm text-white placeholder-gray-500 resize-none h-40"
                  placeholder={`Example: "Use the Playwright browser tool to navigate to example.com/ir, find the latest quarterly report PDF link, download it with the browser download tool, and save it as report.pdf. Use agent-browser.goto() then find the href with agent-browser.find_element() then agent-browser.download()."`}
                  value={demoPrompt}
                  onChange={e => setDemoPrompt(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Source task ID (optional)</label>
                <input
                  type="text"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-500"
                  placeholder="task-xxxxxxxx"
                  value={demoTaskId}
                  onChange={e => setDemoTaskId(e.target.value)}
                />
              </div>
              <button
                disabled={demoSubmitting || !demoPrompt.trim()}
                onClick={submitDemo}
                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-50"
              >
                {demoSubmitting ? 'Submitting…' : 'Submit Demo'}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Create Skill Modal ─────────────────────────────────────────────── */}
      {showCreateForm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-lg p-6 space-y-4">
            <h2 className="text-lg font-bold">Create Skill (Manual)</h2>
            <div>
              <label className="text-sm text-gray-400 block mb-1">Name *</label>
              <input
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                value={newSkillName} onChange={e => setNewSkillName(e.target.value)}
                placeholder="e.g. Download PDF with Playwright"
              />
            </div>
            <div>
              <label className="text-sm text-gray-400 block mb-1">Description</label>
              <input
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                value={newSkillDesc} onChange={e => setNewSkillDesc(e.target.value)}
              />
            </div>
            <div>
              <label className="text-sm text-gray-400 block mb-1">Instructions (injected at agent start)</label>
              <textarea
                className="w-full bg-gray-800 border border-gray-700 rounded p-3 text-sm text-white resize-none h-32"
                value={newSkillInstructions} onChange={e => setNewSkillInstructions(e.target.value)}
                placeholder="Step-by-step instructions for the agent…"
              />
            </div>
            <div>
              <label className="text-sm text-gray-400 block mb-1">Tags (comma separated)</label>
              <input
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                value={newSkillTags} onChange={e => setNewSkillTags(e.target.value)}
                placeholder="browser, pdf, download"
              />
            </div>
            <div className="flex gap-3 pt-2">
              <button
                disabled={createSubmitting || !newSkillName.trim()}
                onClick={createSkill}
                className="px-5 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-50"
              >{createSubmitting ? 'Creating…' : 'Create Draft'}</button>
              <button
                onClick={() => setShowCreateForm(false)}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm"
              >Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── SkillCard component ───────────────────────────────────────────────────────

function SkillCard({ skill, onReview }: { skill: SkillV2; onReview: () => void }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-start justify-between mb-1">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-left font-medium text-white hover:text-blue-300 transition-colors"
        >
          {skill.name}
        </button>
        <div className="flex items-center gap-2 flex-shrink-0 ml-4">
          {statusBadge(skill.status)}
          {skill.status === 'draft' && (
            <button
              onClick={onReview}
              className="px-2 py-0.5 bg-yellow-800 hover:bg-yellow-700 rounded text-xs font-medium"
            >Review</button>
          )}
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs text-gray-500 mb-2">
        <span className="font-mono">{skill.id}</span>
        <span>src: {skill.source_type}</span>
        {confidenceBar(skill.confidence_score)}
        <span>used {skill.usage_count}×</span>
        {skill.reviewer_score && <span>⭐ {skill.reviewer_score}/5</span>}
      </div>
      {skill.description && (
        <p className="text-gray-400 text-sm mb-1">{skill.description}</p>
      )}
      {skill.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {skill.tags.map(t => (
            <span key={t} className="bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded text-xs">{t}</span>
          ))}
        </div>
      )}
      {expanded && skill.instructions && (
        <pre className="mt-2 bg-gray-900 rounded p-3 text-xs text-gray-300 overflow-x-auto max-h-48 overflow-y-auto">
          {skill.instructions}
        </pre>
      )}
    </div>
  )
}
