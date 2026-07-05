'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { API } from '../lib/api'

interface SkillStep {
  description: string
  base_image?: string
  llm_model?: string
  timeout?: number
}

interface Skill {
  id: string
  name: string
  description: string | null
  instructions: string | null
  steps: SkillStep[]
  tags: string[]
  source_url: string | null
  created_at: string
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [error, setError] = useState<string | null>(null)
  const [seeding, setSeeding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/skills`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSkills(await res.json())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    fetchSkills()
  }, [fetchSkills])

  const seedSkills = async () => {
    setSeeding(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/skills/seed`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchSkills()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSeeding(false)
    }
  }

  const importZip = async (file: File) => {
    setImporting(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch(`${API}/api/skills/import`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || `HTTP ${res.status}`)
      }
      const imported = await res.json()
      await fetchSkills()
      window.alert(`Imported skill: ${imported.name}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const deleteSkill = async (skillId: string, skillName: string) => {
    if (!window.confirm(`Delete skill "${skillName}"?`)) return
    setError(null)
    try {
      const res = await fetch(`${API}/api/skills/${skillId}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await fetchSkills()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Skills</h1>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) importZip(file)
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="btn-primary text-sm"
          >
            {importing ? 'Importing...' : '📦 Import from ClawHub (.zip)'}
          </button>
          <button onClick={seedSkills} disabled={seeding} className="btn-primary text-sm">
            {seeding ? 'Seeding...' : 'Seed Built-in Skills'}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-900/20 border border-red-500/30 rounded p-3 mb-4 text-red-300 text-sm">{error}</div>}

      <div className="text-sm text-gray-500 mb-4">
        Download skill zips from <a href="https://clawhub.ai/skills" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">clawhub.ai</a> and import them here. Selected skills will be available to the DAG planner.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {skills.map((skill) => (
          <div key={skill.id} className="card">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold">{skill.name}</h3>
                <span className="text-xs font-mono text-gray-500">{skill.id}</span>
              </div>
              <button
                onClick={() => deleteSkill(skill.id, skill.name)}
                className="text-xs text-red-400 hover:text-red-300"
                title="Delete skill"
              >✕</button>
            </div>
            {skill.description && <p className="text-sm text-gray-400 mb-2">{skill.description}</p>}
            {skill.source_url && (
              <div className="text-xs text-gray-600 mb-2">Source: {skill.source_url}</div>
            )}
            {skill.instructions && (
              <div className="mb-2">
                <button
                  onClick={() => setExpandedSkill(expandedSkill === skill.id ? null : skill.id)}
                  className="text-xs text-blue-400 hover:text-blue-300"
                >
                  {expandedSkill === skill.id ? '▼ Hide instructions' : '▶ Show instructions'}
                </button>
                {expandedSkill === skill.id && (
                  <pre className="mt-1 text-xs text-gray-400 bg-gray-800/50 rounded p-2 max-h-64 overflow-auto whitespace-pre-wrap">
                    {skill.instructions}
                  </pre>
                )}
              </div>
            )}
            {skill.steps && skill.steps.length > 0 && (
              <div className="space-y-1">
                {skill.steps.map((step, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="text-gray-500 font-mono w-4 text-right">{i + 1}.</span>
                    <span className="text-gray-300">{step.description}</span>
                  </div>
                ))}
              </div>
            )}
            {skill.tags && skill.tags.length > 0 && (
              <div className="flex gap-1 mt-3">
                {skill.tags.map((tag) => (
                  <span key={tag} className="text-xs bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded">{tag}</span>
                ))}
              </div>
            )}
          </div>
        ))}
        {skills.length === 0 && (
          <div className="col-span-2 text-center text-gray-500 py-12">
            No skills yet. Import from ClawHub or click &quot;Seed Built-in Skills&quot; to add defaults.
          </div>
        )}
      </div>
    </div>
  )
}