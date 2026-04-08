'use client'

import { useState, useEffect } from 'react'
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
  steps: SkillStep[]
  tags: string[]
  created_at: string
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [error, setError] = useState<string | null>(null)
  const [seeding, setSeeding] = useState(false)

  const fetchSkills = async () => {
    try {
      const res = await fetch(`${API}/api/skills`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setSkills(await res.json())
    } catch (err: any) {
      setError(err.message)
    }
  }

  useEffect(() => { fetchSkills() }, [])

  const seedSkills = async () => {
    setSeeding(true)
    try {
      await fetch(`${API}/api/skills/seed`, { method: 'POST' })
      fetchSkills()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSeeding(false)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Skills</h1>
        <button onClick={seedSkills} disabled={seeding} className="btn-primary text-sm">
          {seeding ? 'Seeding...' : 'Seed Built-in Skills'}
        </button>
      </div>

      {error && <div className="bg-red-900/20 border border-red-500/30 rounded p-3 mb-4 text-red-300 text-sm">{error}</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {skills.map((skill) => (
          <div key={skill.id} className="card">
            <div className="flex items-center gap-2 mb-2">
              <h3 className="font-semibold">{skill.name}</h3>
              <span className="text-xs font-mono text-gray-500">{skill.id}</span>
            </div>
            {skill.description && <p className="text-sm text-gray-400 mb-3">{skill.description}</p>}
            <div className="space-y-1">
              {skill.steps.map((step, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <span className="text-gray-500 font-mono w-4 text-right">{i + 1}.</span>
                  <span className="text-gray-300">{step.description}</span>
                </div>
              ))}
            </div>
            {skill.tags.length > 0 && (
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
            No skills yet. Click &quot;Seed Built-in Skills&quot; to add defaults.
          </div>
        )}
      </div>
    </div>
  )
}
