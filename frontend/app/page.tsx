'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { API } from './lib/api'
import { useProject } from './lib/ProjectContext'
import {
  STAGE_META,
  formatRelativeTime,
  modeRank,
  workflowNodeCountsLabel,
  type WorkflowSummary,
} from './lib/workflows'
import TemplateRunDialog, { type TemplateDag } from './components/TemplateRunDialog'
import NewExplorationForm from './components/NewExplorationForm'

const MAX_ATTACHMENT_FILES = 20
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
const MAX_ATTACHMENT_TOTAL_BYTES = 100 * 1024 * 1024
const ATTACHMENT_ACCEPT = [
  '.pdf', '.txt', '.md', '.rst', '.doc', '.docx', '.odt', '.rtf',
  '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml', '.xlsx', '.xls', '.parquet',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.svg',
  '.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.sql', '.sh',
  '.ipynb', '.xml', '.toml', '.ini', '.cfg', '.zip', '.tar', '.gz', '.tgz',
  '.7z', '.ppt', '.pptx',
].join(',')

const QUICK_STARTERS = [
  {
    icon: '📄',
    label: 'Ingest Document to RAG',
    prompt: 'Ingest the attached PDF into the Docling RAG service, poll until indexing completes, verify the chunks, and produce a telemetry report.',
  },
  {
    icon: '📊',
    label: 'Octave / Quant Math',
    prompt: 'Run an Octave calculation on the attached data, produce the numerical results and a short methodology report.',
  },
  {
    icon: '🌐',
    label: 'Web Data Harvester',
    prompt: 'Harvest the requested web data, verify the downloads, and summarize the key facts in a report.',
  },
]

interface Skill {
  id: string
  name: string
  description: string | null
}

interface DAGRecord extends TemplateDag {
  locked: boolean
  archived: boolean
  status: string
  llm_model?: string | null
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export default function LaunchpadPage() {
  const router = useRouter()
  const { activeProject, projects } = useProject()
  const promptRef = useRef<HTMLTextAreaElement | null>(null)

  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [models, setModels] = useState<{ id: string; provider?: string }[]>([])
  const [dags, setDags] = useState<DAGRecord[]>([])
  const [loadingGroups, setLoadingGroups] = useState(true)

  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([])
  const [attachments, setAttachments] = useState<File[]>([])
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [composerMode, setComposerMode] = useState<'experiment' | 'template' | 'exploration'>('experiment')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState('')
  const [activeTemplate, setActiveTemplate] = useState<DAGRecord | null>(null)
  const [modeGuideDismissed, setModeGuideDismissed] = useState(false)

  const fetchGroups = async () => {
    setLoadingGroups(true)
    try {
      const qs = new URLSearchParams({ limit: '200' })
      if (activeProject) qs.set('project_id', activeProject)
      const [workflowRes, dagRes] = await Promise.all([
        fetch(`${API}/api/workflows?${qs}`),
        fetch(`${API}/api/dags?${new URLSearchParams({ archived: 'false', limit: '200', ...(activeProject ? { project_id: activeProject } : {}) })}`),
      ])
      if (workflowRes.ok) setWorkflows(await workflowRes.json())
      if (dagRes.ok) setDags(await dagRes.json())
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoadingGroups(false)
    }
  }

  useEffect(() => {
    fetchGroups()
    fetch(`${API}/api/skills`).then((res) => res.ok ? res.json() : []).then(setSkills).catch(() => {})
    fetch(`${API}/api/llm/models`).then((res) => res.ok ? res.json() : { models: [] }).then((data) => {
      const list = Array.isArray(data?.models) ? data.models : []
      setModels(list)
      setModel((current) => current || list.find((item: any) => item.id === 'deepseek-v4-flash')?.id || list[0]?.id || '')
    }).catch(() => {})
    const params = new URLSearchParams(window.location.search)
    if (params.get('new') === '1') {
      setComposerMode('experiment')
      setTimeout(() => promptRef.current?.focus(), 50)
    }
  }, [activeProject])

  useEffect(() => {
    try {
      setModeGuideDismissed(window.localStorage.getItem('mode-guide-dismissed') === '1')
    } catch {
      setModeGuideDismissed(false)
    }
  }, [])

  const dismissModeGuide = () => {
    setModeGuideDismissed(true)
    try {
      window.localStorage.setItem('mode-guide-dismissed', '1')
    } catch {}
  }

  const reopenModeGuide = () => {
    setModeGuideDismissed(false)
    try {
      window.localStorage.removeItem('mode-guide-dismissed')
    } catch {}
  }

  const templates = useMemo(
    () => dags.filter((dag) => dag.locked && !dag.archived),
    [dags],
  )
  const workflowById = useMemo(
    () => new Map(workflows.map((group) => [group.id, group])),
    [workflows],
  )
  const projectNameById = useMemo(
    () => new Map(projects.map((project) => [project.id, project.name])),
    [projects],
  )
  const selectedTemplate = templates.find((template) => template.id === templateId) || null

  const jumpBackIn = [...workflows]
    .sort((a, b) => {
      const byMode = modeRank(a.mode) - modeRank(b.mode)
      if (byMode !== 0) return byMode
      return new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
    })
    .slice(0, 4)
  const explorations = workflows.filter((group) => group.mode === 'exploration').length
  const nonExploration = workflows.filter((group) => group.mode !== 'exploration')
  const experiments = nonExploration.filter((group) => group.stage === 'experimenting').length
  const locked = nonExploration.filter((group) => group.stage === 'locked').length
  const hardened = nonExploration.filter((group) => group.stage === 'hardened').length
  const determinism = hardened > 0
    ? Math.round(
        nonExploration
          .filter((group) => group.stage === 'hardened')
          .reduce((sum, group) => sum + group.determinism_pct, 0) / hardened,
      )
    : 0

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    setAttachmentError(null)
    const accepted = ATTACHMENT_ACCEPT.split(',')
    const next = [...attachments]
    for (const file of Array.from(incoming)) {
      if (next.length >= MAX_ATTACHMENT_FILES) {
        setAttachmentError(`At most ${MAX_ATTACHMENT_FILES} files can be attached.`)
        break
      }
      const dot = file.name.lastIndexOf('.')
      const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
      if (!accepted.includes(ext)) {
        setAttachmentError(`Unsupported file type: ${file.name}`)
        continue
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setAttachmentError(`${file.name} is larger than ${formatBytes(MAX_ATTACHMENT_BYTES)}.`)
        continue
      }
      if (next.some((f) => f.name === file.name && f.size === file.size)) continue
      next.push(file)
    }
    const total = next.reduce((sum, f) => sum + f.size, 0)
    if (total > MAX_ATTACHMENT_TOTAL_BYTES) {
      setAttachmentError(`Total attachment size exceeds ${formatBytes(MAX_ATTACHMENT_TOTAL_BYTES)}.`)
      return
    }
    setAttachments(next)
  }

  const buildWorkflow = async () => {
    if (!prompt.trim() || creating) return
    setCreating(true)
    setError(null)
    try {
      const formData = new FormData()
      formData.append('objective', prompt.trim())
      formData.append('auto_start', 'false')
      if (model) formData.append('llm_model', model)
      if (selectedSkillIds.length > 0) formData.append('skill_ids', JSON.stringify(selectedSkillIds))
      if (activeProject) formData.append('project_id', activeProject)
      attachments.forEach((file) => formData.append('files', file))
      const res = await fetch(`${API}/api/dags/with-files`, { method: 'POST', body: formData })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const detail = (data as any)?.detail
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || data))
      }
      router.push(`/dags/${(data as any).id}`)
    } catch (err: any) {
      setError(err.message)
      setCreating(false)
    }
  }

  const runWorkflow = (group: WorkflowSummary) => {
    const latestId = group.latest_run?.id
    if (!latestId) return
    if (group.locked) {
      const template = templates.find((dag) => dag.workflow_group_id === group.id)
        || templates.find((dag) => dag.id === latestId)
      if (template) {
        setActiveTemplate(template)
        return
      }
    }
    router.push(`/dags/${latestId}`)
  }

  const openLatest = (group: WorkflowSummary) => {
    if (group.latest_run?.id) router.push(`/dags/${group.latest_run.id}`)
  }

  const resume = (group: WorkflowSummary) => {
    const latest = group.latest_run
    if (!latest) return
    router.push(`/dags/${latest.id}${group.latest_active_node_id ? `?node=${encodeURIComponent(group.latest_active_node_id)}` : ''}`)
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-10">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.25em] text-indigo-400">TaskForge Studio</div>
          <h1 className="mt-2 text-3xl font-bold text-white">What are you working on today?</h1>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            Explore continuously with chat-driven cycles, run a single experiment, or start from a locked template. Iterate, lock the winner, then harden into deterministic blocks.
          </p>
        </div>
        <Link href="/dags" className="btn-secondary text-sm">
          View all {workflows.length} workflows in Studio →
        </Link>
      </div>

      {!modeGuideDismissed ? (
        <section className="mb-4 rounded-xl border border-indigo-800/40 bg-indigo-950/10 p-4">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-indigo-200">How modes work</h2>
              <p className="mt-0.5 text-[11px] text-gray-500">
                Each mode has a different job. Use the lightest one that fits what you already know.
              </p>
            </div>
            <button
              onClick={dismissModeGuide}
              className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 hover:border-gray-500 hover:text-gray-200"
            >
              Dismiss ✕
            </button>
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            <div className="rounded-lg border border-cyan-800/40 bg-cyan-950/10 p-3">
              <div className="text-xs font-semibold text-cyan-200">🧭 Exploration — Discover</div>
              <p className="mt-1 text-[10px] text-gray-400">Clarify unknowns through chat and cycles.</p>
              <p className="mt-1 text-[10px] text-gray-500">Outputs: a draft workflow or a one-off deliverable.</p>
              <p className="mt-1 text-[10px] text-amber-300/80">Expected to be iterative; token spend can be higher.</p>
            </div>
            <div className="rounded-lg border border-amber-800/40 bg-amber-950/10 p-3">
              <div className="text-xs font-semibold text-amber-200">🧪 Experiment — Build &amp; refine</div>
              <p className="mt-1 text-[10px] text-gray-400">
                Run the workflow, inspect results, fix steps, and harden into deterministic blocks.
              </p>
            </div>
            <div className="rounded-lg border border-purple-800/40 bg-purple-950/10 p-3">
              <div className="text-xs font-semibold text-purple-200">🔒 Template — Run proven work</div>
              <p className="mt-1 text-[10px] text-gray-400">
                Reuse a locked workflow with known inputs and a pinned image/version.
              </p>
            </div>
          </div>
          <div className="mt-3 rounded-lg border border-gray-800 bg-gray-950/40 px-2.5 py-2 text-[10px]">
            <div className="font-mono text-indigo-300">Explore → Experiment / Harden → Lock → Run as Template</div>
            <p className="mt-1 text-gray-500">
              Explore when the shape is unknown. Once stable, lock the workflow so repeated runs reuse the pinned
              workflow instead of paying for planning and iteration.
            </p>
          </div>
        </section>
      ) : (
        <button onClick={reopenModeGuide} className="mb-4 text-[11px] text-indigo-400 hover:text-indigo-300">
          How modes work
        </button>
      )}

      {error && (
        <div className="mb-4 rounded border border-red-500/30 bg-red-900/20 p-3 text-sm text-red-300">{error}</div>
      )}

      <div className="rounded-xl border border-gray-700/70 bg-[#12121a] p-4 shadow-xl">
        <div className="mb-4 grid gap-2 md:grid-cols-3">
          <button
            onClick={() => { setComposerMode('exploration'); setError(null) }}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              composerMode === 'exploration'
                ? 'border-cyan-500 bg-cyan-950/30'
                : 'border-gray-700 bg-gray-900/30 hover:border-gray-600'
            }`}
          >
            <div className={`text-sm font-semibold ${composerMode === 'exploration' ? 'text-white' : 'text-gray-300'}`}>
              🧭 Exploration
            </div>
            <div className="mt-0.5 text-[10px] text-gray-500">
              Discover what is needed; output a draft workflow or a one-off deliverable.
            </div>
          </button>
          <button
            onClick={() => { setComposerMode('experiment'); setError(null) }}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              composerMode === 'experiment'
                ? 'border-indigo-500 bg-indigo-950/40'
                : 'border-gray-700 bg-gray-900/30 hover:border-gray-600'
            }`}
          >
            <div className={`text-sm font-semibold ${composerMode === 'experiment' ? 'text-white' : 'text-gray-300'}`}>
              🧪 Experiment
            </div>
            <div className="mt-0.5 text-[10px] text-gray-500">
              Build, execute, and refine the actual workflow.
            </div>
          </button>
          <button
            onClick={() => { setComposerMode('template'); setError(null) }}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              composerMode === 'template'
                ? 'border-purple-500 bg-purple-950/30'
                : 'border-gray-700 bg-gray-900/30 hover:border-gray-600'
            }`}
          >
            <div className={`flex items-center gap-2 text-sm font-semibold ${composerMode === 'template' ? 'text-white' : 'text-gray-300'}`}>
              <span>🔒 Template</span>
              {templates.length > 0 && (
                <span className="rounded border border-purple-700/40 px-1.5 py-0.5 text-[9px] font-normal text-purple-300">
                  {templates.length}
                </span>
              )}
            </div>
            <div className="mt-0.5 text-[10px] text-gray-500">
              Run a proven, locked workflow with known inputs.
            </div>
          </button>
        </div>

        {composerMode === 'experiment' ? (
          <>
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={5}
              placeholder="Describe the objective you want to accomplish..."
              className="w-full resize-y bg-transparent px-1 py-1 text-sm text-gray-200 placeholder:text-gray-600 focus:outline-none"
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') buildWorkflow()
              }}
            />

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-800 pt-3">
              <input
                id="launchpad-files"
                type="file"
                multiple
                accept={ATTACHMENT_ACCEPT}
                className="hidden"
                onChange={(event) => { addFiles(event.target.files); event.target.value = '' }}
              />
              <label
                htmlFor="launchpad-files"
                className="cursor-pointer rounded border border-gray-700 px-2.5 py-1.5 text-xs text-gray-300 hover:border-gray-500"
              >
                📎 Attach PDF / file
              </label>
              <select
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300"
              >
                {models.length === 0 && <option value="">Default model</option>}
                {models.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
              </select>
              <button
                onClick={() => setShowAdvanced((value) => !value)}
                className="rounded border border-gray-700 px-2.5 py-1.5 text-xs text-gray-400 hover:border-gray-500 hover:text-gray-200"
              >
                ⚙ Advanced {showAdvanced ? '▴' : '▾'}
              </button>
              <button
                onClick={buildWorkflow}
                disabled={creating || !prompt.trim()}
                className="ml-auto rounded bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {creating ? 'Planning workflow…' : '⚡ Build with AI →'}
              </button>
            </div>

            <div className="mt-1 text-[10px] text-gray-600">
              Planning creates a ready run. Review and start it from the studio.
            </div>

            {attachments.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {attachments.map((file, index) => (
                  <span key={`${file.name}-${index}`} className="inline-flex items-center gap-2 rounded border border-gray-700 bg-gray-900/60 px-2 py-1 text-[10px] text-gray-400">
                    <span className="max-w-[220px] truncate font-mono">{file.name}</span>
                    <span className="text-gray-600">{formatBytes(file.size)}</span>
                    <button onClick={() => setAttachments(attachments.filter((_, i) => i !== index))} className="text-red-400">✕</button>
                  </span>
                ))}
              </div>
            )}
            {attachmentError && <div className="mt-1 text-[10px] text-red-400">{attachmentError}</div>}

            {showAdvanced && (
              <div className="mt-3 grid gap-3 border-t border-gray-800 pt-3 md:grid-cols-2">
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Skills</div>
                  <div className="max-h-36 overflow-y-auto rounded border border-gray-800 p-2">
                    {skills.length === 0 ? (
                      <span className="text-[10px] text-gray-600">No skills available.</span>
                    ) : skills.map((skill) => (
                      <label key={skill.id} className="flex cursor-pointer items-start gap-2 rounded p-1 text-[10px] text-gray-400 hover:bg-gray-900">
                        <input
                          type="checkbox"
                          checked={selectedSkillIds.includes(skill.id)}
                          onChange={(event) => {
                            setSelectedSkillIds((prev) => event.target.checked
                              ? [...prev, skill.id]
                              : prev.filter((id) => id !== skill.id))
                          }}
                        />
                        <span>
                          <span className="font-medium text-gray-300">{skill.name}</span>
                          {skill.description && <span className="ml-1 text-gray-600">— {skill.description}</span>}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <div className="text-[10px] text-gray-600">
                    Up to {MAX_ATTACHMENT_FILES} files, {formatBytes(MAX_ATTACHMENT_BYTES)} each, {formatBytes(MAX_ATTACHMENT_TOTAL_BYTES)} total.
                  </div>
                </div>
              </div>
            )}
          </>
        ) : composerMode === 'template' ? (
          <div className="space-y-3">
            {templates.length === 0 ? (
              <div className="rounded-lg border border-dashed border-gray-700 p-6 text-center">
                <div className="text-sm text-gray-400">No locked templates yet.</div>
                <p className="mt-1 text-xs text-gray-600">Lock a successful workflow first to reuse it with new inputs.</p>
                <Link href="/dags?stage=locked" className="mt-3 inline-block rounded border border-purple-700/50 px-3 py-1.5 text-xs text-purple-300 hover:border-purple-500">
                  Open locked workflows in Studio →
                </Link>
              </div>
            ) : (
              <>
                <div className="max-h-64 space-y-1.5 overflow-y-auto rounded border border-gray-800 p-1.5">
                  {templates.map((template) => {
                    const title = workflowById.get(template.workflow_group_id || '')?.title || template.objective
                    const paramCount = Array.isArray(template.template_params) ? template.template_params.length : 0
                    const selected = template.id === templateId
                    return (
                      <button
                        key={template.id}
                        onClick={() => setTemplateId(template.id)}
                        className={`w-full rounded border px-3 py-2 text-left transition-colors ${
                          selected
                            ? 'border-purple-500 bg-purple-950/30'
                            : 'border-gray-800 bg-gray-900/30 hover:border-gray-600'
                        }`}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-200">{title}</span>
                          <span className="shrink-0 rounded border border-purple-700/40 px-1.5 py-0.5 text-[9px] text-purple-300">🔒 locked</span>
                          <span className="shrink-0 text-[9px] text-gray-600">
                            {paramCount > 0 ? `${paramCount} input${paramCount === 1 ? '' : 's'}` : 'No inputs'}
                          </span>
                        </div>
                        <div className="mt-0.5 line-clamp-1 text-[10px] text-gray-500">{template.objective}</div>
                      </button>
                    )
                  })}
                </div>

                <div className="flex flex-wrap items-center gap-3 border-t border-gray-800 pt-3">
                  <p className="max-w-md text-[10px] text-gray-500">
                    Templates run with their frozen attachments. Upload files in Experiment when you need new inputs.
                  </p>
                  <button
                    onClick={() => {
                      if (selectedTemplate) setActiveTemplate(selectedTemplate)
                    }}
                    disabled={!selectedTemplate}
                    className="ml-auto rounded bg-purple-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-purple-500 disabled:opacity-40"
                  >
                    ▷ Configure &amp; Create Run
                  </button>
                </div>
              </>
            )}
          </div>
        ) : (
          <NewExplorationForm
            compact
            onCreated={(sessionId) => router.push(`/explorations/${sessionId}`)}
          />
        )}
      </div>

      {composerMode === 'experiment' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="py-1 text-[10px] uppercase tracking-wide text-gray-600">Quick starters:</span>
          {QUICK_STARTERS.map((starter) => (
            <button
              key={starter.label}
              onClick={() => { setPrompt(starter.prompt); promptRef.current?.focus() }}
              className="rounded-full border border-gray-700 px-2.5 py-1 text-[10px] text-gray-400 hover:border-indigo-500/50 hover:text-indigo-300"
            >
              {starter.icon} {starter.label}
            </button>
          ))}
        </div>
      )}

      <div className="mt-10">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500">Jump back in</div>
            <p className="text-xs text-gray-600">Pick up where you left off across your active workflow groups.</p>
          </div>
          <Link href="/dags" className="text-xs text-indigo-400 hover:text-indigo-300">View all workflows →</Link>
        </div>

        {loadingGroups ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-52 animate-pulse rounded-lg border border-gray-800 bg-gray-900/40" />
            ))}
          </div>
        ) : jumpBackIn.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-700 p-8 text-center text-sm text-gray-500">
            No workflows yet. Describe an objective above to start your first experiment.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            {jumpBackIn.map((group) => {
              const stage = STAGE_META[group.stage]
              const isExploration = group.mode === 'exploration'
              return (
                <div key={group.id} className={`flex flex-col rounded-lg border bg-[#12121a] p-4 ${isExploration ? 'border-cyan-800/40' : stage.border}`}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${isExploration ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300' : stage.badge}`}>
                      {isExploration ? '🧭 Exploration' : `${stage.icon} ${stage.short}`}
                    </span>
                    {group.locked && <span className="text-[9px] text-purple-300">🔒</span>}
                  </div>
                  {isExploration && (
                    <div className="mb-1 text-[9px] text-gray-600">
                      Maturity: {stage.short} · {group.determinism_pct}%
                    </div>
                  )}
                  {group.project_id && projectNameById.get(group.project_id) && (
                    <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-600">
                      Group: {projectNameById.get(group.project_id)}
                    </div>
                  )}
                  <div className="line-clamp-2 text-sm font-semibold text-white">{group.title}</div>
                  <p className="mt-1 line-clamp-2 text-[11px] text-gray-500">{group.objective}</p>
                  <div className="mt-2 space-y-1 text-[10px] text-gray-500">
                    <div>{workflowNodeCountsLabel(group.latest_node_counts)}</div>
                    <div>
                      {group.latest_run
                        ? `Run #${group.latest_run.run_number} • ${group.latest_run.status}${group.latest_run.score !== null ? ` • ${group.latest_run.score}/100` : ''}`
                        : 'No runs yet'}
                    </div>
                    {group.blocker && <div className="line-clamp-2 text-amber-300/80">Blocker: {group.blocker}</div>}
                    {group.key_output && <div className="truncate text-cyan-300/80">Output: <span className="font-mono">{group.key_output}</span></div>}
                    <div>Last touched {formatRelativeTime(group.updated_at)}</div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {group.mode === 'exploration' && group.exploration_session_id ? (
                      <Link href={`/explorations/${group.exploration_session_id}`} className="rounded bg-cyan-600 px-2 py-1 text-[10px] font-semibold text-white hover:bg-cyan-500">
                        🧭 Open Exploration
                      </Link>
                    ) : (
                      <button onClick={() => resume(group)} className="rounded bg-indigo-600 px-2 py-1 text-[10px] font-semibold text-white hover:bg-indigo-500">
                        → Resume Iteration
                      </button>
                    )}
                    <button onClick={() => runWorkflow(group)} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:border-gray-500">
                      {group.locked ? '▷ Run Template' : '▷ Run'}
                    </button>
                    <Link href={`/dags?workflow=${group.id}`} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 hover:border-gray-500">
                      Open Group
                    </Link>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="mt-10 grid gap-3 md:grid-cols-4">
        <Link href="/dags?stage=exploration" className="rounded-lg border border-cyan-800/40 bg-cyan-950/10 p-4 hover:border-cyan-600/60">
          <div className="text-2xl font-bold text-cyan-300">{explorations}</div>
          <div className="text-xs text-cyan-200/80">🧭 Explorations in flight</div>
        </Link>
        <Link href="/dags?stage=experimenting" className="rounded-lg border border-amber-800/40 bg-amber-950/10 p-4 hover:border-amber-600/60">
          <div className="text-2xl font-bold text-amber-300">{experiments}</div>
          <div className="text-xs text-amber-200/80">🧪 Experiments in flight</div>
        </Link>
        <Link href="/dags?stage=locked" className="rounded-lg border border-purple-800/40 bg-purple-950/10 p-4 hover:border-purple-600/60">
          <div className="text-2xl font-bold text-purple-300">{locked}</div>
          <div className="text-xs text-purple-200/80">🔒 Battle-tested templates</div>
        </Link>
        <Link href="/dags?stage=hardened" className="rounded-lg border border-emerald-800/40 bg-emerald-950/10 p-4 hover:border-emerald-600/60">
          <div className="text-2xl font-bold text-emerald-300">{hardened}</div>
          <div className="text-xs text-emerald-200/80">⚡ Hardened workflows ({determinism}% deterministic)</div>
        </Link>
      </div>

      {activeTemplate && (
        <TemplateRunDialog
          template={activeTemplate}
          onClose={() => setActiveTemplate(null)}
          onCreated={(dagId) => router.push(`/dags/${dagId}`)}
        />
      )}
    </div>
  )
}
