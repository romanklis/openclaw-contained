'use client'

import { useMemo, useState } from 'react'
import { API } from '../lib/api'
import { type WorkflowSummary } from '../lib/workflows'

interface MergeWorkflowDialogProps {
  target: WorkflowSummary
  groups: WorkflowSummary[]
  onClose: () => void
  onMerged: () => void
}

export default function MergeWorkflowDialog({
  target,
  groups,
  onClose,
  onMerged,
}: MergeWorkflowDialogProps) {
  const [search, setSearch] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [allowCrossProject, setAllowCrossProject] = useState(false)
  const [merging, setMerging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const candidates = useMemo(() => {
    const term = search.trim().toLowerCase()
    return groups
      .filter((group) => group.id !== target.id)
      .filter((group) => {
        if (!term) return true
        return (
          group.title.toLowerCase().includes(term) ||
          group.objective.toLowerCase().includes(term)
        )
      })
      .slice(0, 50)
  }, [groups, target.id, search])

  const source = groups.find((group) => group.id === sourceId) || null
  const crossProject = !!source && source.project_id !== target.project_id

  const applyMerge = async () => {
    if (!source || merging) return
    if (crossProject && !allowCrossProject) {
      setError('Enable cross-project merge first.')
      return
    }
    if (!window.confirm(`Move all runs from "${source.title}" into "${target.title}"? The source workflow will be removed.`)) {
      return
    }
    setMerging(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/workflows/${target.id}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_id: source.id,
          allow_cross_project: allowCrossProject,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      onMerged()
      onClose()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setMerging(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div
        className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#12121a]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-gray-800 px-4 py-3">
          <div className="text-sm font-semibold text-white">Merge into workflow</div>
          <div className="mt-0.5 text-xs text-gray-400">
            Target: <span className="text-gray-200">{target.title}</span> ({target.run_count} runs)
          </div>
        </div>

        <div className="space-y-3 overflow-y-auto p-4">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search workflows to merge in…"
            className="w-full rounded border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-xs text-gray-300 placeholder:text-gray-600"
          />

          <div className="max-h-64 space-y-1 overflow-y-auto rounded border border-gray-800 p-1.5">
            {candidates.length === 0 ? (
              <div className="p-3 text-xs text-gray-600">No matching workflows.</div>
            ) : candidates.map((group) => (
              <button
                key={group.id}
                onClick={() => setSourceId(group.id)}
                className={`w-full rounded border px-3 py-2 text-left ${
                  sourceId === group.id
                    ? 'border-indigo-500 bg-indigo-950/30'
                    : 'border-gray-800 bg-gray-900/30 hover:border-gray-600'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-200">{group.title}</span>
                  <span className="text-[9px] text-gray-600">{group.run_count} runs</span>
                  <span className="text-[9px] uppercase tracking-wide text-gray-600">{group.stage}</span>
                </div>
                <div className="mt-0.5 line-clamp-1 text-[10px] text-gray-500">{group.objective}</div>
              </button>
            ))}
          </div>

          {source && (
            <div className="rounded border border-gray-800 bg-gray-900/40 p-3 text-[10px] text-gray-400">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-gray-300">Move {source.run_count} runs from</span>
                <span className="font-mono text-gray-200">{source.title}</span>
                <span className="text-gray-500">into</span>
                <span className="font-mono text-gray-200">{target.title}</span>
              </div>
              {crossProject && (
                <label className="mt-2 flex items-center gap-2 text-amber-300">
                  <input
                    type="checkbox"
                    checked={allowCrossProject}
                    onChange={(event) => setAllowCrossProject(event.target.checked)}
                  />
                  Allow cross-project merge ({source.project_id || 'no project'} → {target.project_id || 'no project'})
                </label>
              )}
              <div className="mt-2 text-[10px] text-gray-500">
                All runs will appear under the target workflow. The source workflow entry is deleted.
              </div>
            </div>
          )}

          {error && <div className="rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-800 px-4 py-3">
          <button onClick={onClose} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200">
            Cancel
          </button>
          <button
            onClick={applyMerge}
            disabled={!source || merging || (crossProject && !allowCrossProject)}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {merging ? 'Merging…' : 'Merge workflow'}
          </button>
        </div>
      </div>
    </div>
  )
}
