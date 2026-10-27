'use client'

import { useEffect, useState } from 'react'
import { API } from '../lib/api'
import { useProject } from '../lib/ProjectContext'
import { QUALITY_BUDGET_OPTIONS, THINKING_BUDGET_OPTIONS } from '../lib/explorations'

const MAX_FILES = 20
const MAX_FILE_BYTES = 25 * 1024 * 1024
const MAX_TOTAL_BYTES = 100 * 1024 * 1024
const ACCEPT = [
  '.pdf', '.txt', '.md', '.rst', '.doc', '.docx', '.odt', '.rtf',
  '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml', '.xlsx', '.xls', '.parquet',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.svg',
  '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.sql', '.sh',
  '.ipynb', '.xml', '.toml', '.ini', '.cfg', '.zip', '.tar', '.gz', '.tgz',
  '.7z', '.ppt', '.pptx',
].join(',')

interface NewExplorationFormProps {
  onCreated: (sessionId: string) => void
  compact?: boolean
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export default function NewExplorationForm({ onCreated, compact }: NewExplorationFormProps) {
  const { activeProject } = useProject()
  const [objective, setObjective] = useState('')
  const [model, setModel] = useState('')
  const [thinkingBudget, setThinkingBudget] = useState('standard')
  const [qualityBudget, setQualityBudget] = useState('standard')
  const [models, setModels] = useState<{ id: string }[]>([])
  const [skills, setSkills] = useState<{ id: string; name: string; description: string | null }[]>([])
  const [skillIds, setSkillIds] = useState<string[]>([])
  const [files, setFiles] = useState<File[]>([])
  const [advanced, setAdvanced] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    fetch(`${API}/api/llm/models`)
      .then((res) => (res.ok ? res.json() : { models: [] }))
      .then((data) => {
        const list = Array.isArray(data?.models) ? data.models : []
        setModels(list.filter((item: any) => item?.id).map((item: any) => ({ id: item.id })))
        setModel((current) => current || list.find((item: any) => item.id === 'deepseek-v4-flash')?.id || list[0]?.id || '')
      })
      .catch(() => {})
    fetch(`${API}/api/skills`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [])

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    setError(null)
    const accepted = ACCEPT.split(',')
    const next = [...files]
    for (const file of Array.from(incoming)) {
      if (next.length >= MAX_FILES) {
        setError(`At most ${MAX_FILES} files can be attached.`)
        break
      }
      const dot = file.name.lastIndexOf('.')
      const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
      if (!accepted.includes(ext)) {
        setError(`Unsupported file type: ${file.name}`)
        continue
      }
      if (file.size > MAX_FILE_BYTES) {
        setError(`${file.name} is larger than ${bytes(MAX_FILE_BYTES)}.`)
        continue
      }
      if (next.some((item) => item.name === file.name && item.size === file.size)) continue
      next.push(file)
    }
    const total = next.reduce((sum, item) => sum + item.size, 0)
    if (total > MAX_TOTAL_BYTES) {
      setError(`Total attachment size exceeds ${bytes(MAX_TOTAL_BYTES)}.`)
      return
    }
    setFiles(next)
  }

  const submit = async () => {
    if (!objective.trim() || creating) return
    setCreating(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('objective', objective.trim())
      if (model) form.append('model', model)
      form.append('thinking_budget', thinkingBudget)
      form.append('quality_budget', qualityBudget)
      if (skillIds.length) form.append('skill_ids', JSON.stringify(skillIds))
      if (activeProject) form.append('project_id', activeProject)
      files.forEach((file) => form.append('files', file))
      const res = await fetch(`${API}/api/explorations`, { method: 'POST', body: form })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      onCreated((data as any)?.session?.id)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className={compact ? '' : 'rounded-xl border border-gray-700 bg-[#12121a] p-4'}>
      <textarea
        value={objective}
        onChange={(event) => setObjective(event.target.value)}
        rows={compact ? 4 : 5}
        placeholder="Describe the exploration objective. The assistant will plan cycle 1 for review."
        className="w-full resize-y bg-transparent px-1 py-1 text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none"
      />
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-800 pt-3">
        <input
          id="exploration-files"
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(event) => { addFiles(event.target.files); event.target.value = '' }}
        />
        <label htmlFor="exploration-files" className="cursor-pointer rounded border border-gray-700 px-2.5 py-1.5 text-xs text-gray-300 hover:border-gray-500">
          📎 Attach files
        </label>
        <select value={model} onChange={(event) => setModel(event.target.value)} className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300">
          {models.length === 0 && <option value="">Default model</option>}
          {models.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
        </select>
        <button type="button" onClick={() => setAdvanced((value) => !value)} className="rounded border border-gray-700 px-2.5 py-1.5 text-xs text-gray-400 hover:text-gray-200">
          ⚙ Advanced {advanced ? '▴' : '▾'}
        </button>
        <button
          onClick={submit}
          disabled={creating || !objective.trim()}
          className="ml-auto rounded bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {creating ? 'Planning cycle 1…' : '🧭 Start Exploration'}
        </button>
      </div>

      {files.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {files.map((file, index) => (
            <span key={`${file.name}-${index}`} className="inline-flex items-center gap-2 rounded border border-gray-700 bg-gray-900/60 px-2 py-1 text-[10px] text-gray-400">
              <span className="max-w-[220px] truncate font-mono">{file.name}</span>
              <span className="text-gray-600">{bytes(file.size)}</span>
              <button onClick={() => setFiles(files.filter((_, i) => i !== index))} className="text-red-400">✕</button>
            </span>
          ))}
        </div>
      )}

      {advanced && (
        <div className="mt-3 border-t border-gray-800 pt-3">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-[10px] text-gray-500">
              <span className="mb-1 block uppercase tracking-wide">Thinking budget</span>
              <select
                value={thinkingBudget}
                onChange={(event) => setThinkingBudget(event.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
              >
                {THINKING_BUDGET_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label} — {option.detail}</option>
                ))}
              </select>
            </label>
            <label className="text-[10px] text-gray-500">
              <span className="mb-1 block uppercase tracking-wide">Quality cross-check</span>
              <select
                value={qualityBudget}
                onChange={(event) => setQualityBudget(event.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
              >
                {QUALITY_BUDGET_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label} — {option.detail}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="mb-1 mt-3 text-[10px] uppercase tracking-wide text-gray-500">Skills</div>
          <div className="max-h-36 overflow-y-auto rounded border border-gray-800 p-2">
            {skills.length === 0 ? (
              <span className="text-[10px] text-gray-600">No skills available.</span>
            ) : skills.map((skill) => (
              <label key={skill.id} className="flex cursor-pointer items-start gap-2 rounded p-1 text-[10px] text-gray-400 hover:bg-gray-900">
                <input
                  type="checkbox"
                  checked={skillIds.includes(skill.id)}
                  onChange={(event) => {
                    setSkillIds((prev) => event.target.checked ? [...prev, skill.id] : prev.filter((id) => id !== skill.id))
                  }}
                />
                <span><span className="font-medium text-gray-300">{skill.name}</span>{skill.description && <span className="ml-1 text-gray-600">— {skill.description}</span>}</span>
              </label>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-gray-600">
            The first cycle is created for review. Nothing runs until you press Run cycle.
          </p>
        </div>
      )}

      {error && <div className="mt-2 rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}
    </div>
  )
}
