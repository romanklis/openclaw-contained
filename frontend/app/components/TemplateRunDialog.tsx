'use client'

import { useEffect, useMemo, useState } from 'react'
import { API } from '../lib/api'

export interface TemplateParam {
  key: string
  label?: string
  type?: string
  default?: any
  description?: string
}

export interface TemplateDag {
  id: string
  objective: string
  template_params?: TemplateParam[] | null
  workflow_group_id?: string | null
}

interface TemplateRunDialogProps {
  template: TemplateDag
  onClose: () => void
  onCreated: (dagId: string) => void
}

function initialValue(param: TemplateParam): string {
  if (param.type === 'boolean' && (param.default === null || param.default === undefined)) return 'true'
  if (param.default === null || param.default === undefined) return ''
  if (Array.isArray(param.default)) return param.default.join(', ')
  return String(param.default)
}

export default function TemplateRunDialog({ template, onClose, onCreated }: TemplateRunDialogProps) {
  const params = useMemo(
    () => (Array.isArray(template.template_params) ? template.template_params : []),
    [template],
  )
  const [values, setValues] = useState<Record<string, string>>(() => {
    const next: Record<string, string> = {}
    for (const param of params) next[param.key] = initialValue(param)
    return next
  })
  const [objective, setObjective] = useState('')
  const [models, setModels] = useState<{ id: string }[]>([])
  const [modelOverride, setModelOverride] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API}/api/llm/models`)
      .then((res) => (res.ok ? res.json() : { models: [] }))
      .then((data) => {
        const list = Array.isArray(data?.models) ? data.models : []
        setModels(list.filter((item: any) => item?.id).map((item: any) => ({ id: item.id })))
      })
      .catch(() => {})
  }, [])

  const submit = async () => {
    setRunning(true)
    setError(null)
    try {
      const parameters: Record<string, any> = {}
      for (const param of params) {
        const raw = values[param.key] ?? ''
        if (param.type === 'number') {
          parameters[param.key] = raw === '' ? undefined : Number(raw)
        } else if (param.type === 'boolean') {
          parameters[param.key] = raw === 'true'
        } else if (param.type === 'list') {
          parameters[param.key] = raw.split(',').map((item) => item.trim()).filter(Boolean)
        } else {
          parameters[param.key] = raw
        }
      }
      const res = await fetch(`${API}/api/dags/${template.id}/instantiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parameters,
          objective: objective.trim() || undefined,
          auto_start: false,
          llm_model: modelOverride || undefined,
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any).detail || `HTTP ${res.status}`)
      onCreated((data as any).id)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div
        className="relative flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-lg border border-gray-700 bg-[#12121a]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-gray-800 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-white">Create a run from this template</div>
            <div className="mt-1 line-clamp-2 text-xs text-gray-400">{template.objective}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200">✕</button>
        </div>
        <div className="space-y-3 overflow-y-auto p-4">
          {params.length === 0 ? (
            <div className="rounded border border-gray-800 bg-gray-900/40 p-3 text-xs text-gray-500">
              No parameters required. This template can run with its frozen defaults.
            </div>
          ) : (
            params.map((param) => (
              <div key={param.key}>
                <label className="mb-1 block text-xs text-gray-400">
                  {param.label || param.key}
                  {param.type && <span className="ml-1 text-gray-600">({param.type})</span>}
                </label>
                {param.type === 'boolean' ? (
                  <select
                    value={values[param.key] ?? ''}
                    onChange={(event) => setValues((prev) => ({ ...prev, [param.key]: event.target.value }))}
                    className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : (
                  <input
                    type={param.type === 'number' ? 'number' : 'text'}
                    value={values[param.key] ?? ''}
                    onChange={(event) => setValues((prev) => ({ ...prev, [param.key]: event.target.value }))}
                    className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm"
                  />
                )}
                {param.description && <p className="mt-1 text-[10px] text-gray-600">{param.description}</p>}
              </div>
            ))
          )}
          <div>
            <label className="mb-1 block text-xs text-gray-400">Objective override (optional)</label>
            <input
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="Leave blank to use the template objective"
              className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm"
            />
          </div>
          <div className="rounded border border-gray-800 bg-gray-900/40">
            <button
              type="button"
              onClick={() => setShowAdvanced((value) => !value)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-gray-400 hover:text-gray-200"
            >
              <span>⚙ Advanced</span>
              <span>{showAdvanced ? '▴' : '▾'}</span>
            </button>
            {showAdvanced && (
              <div className="border-t border-gray-800 p-3">
                <label className="mb-1 block text-xs text-gray-400">Executing model</label>
                <select
                  value={modelOverride}
                  onChange={(event) => setModelOverride(event.target.value)}
                  className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm"
                >
                  <option value="">Use template model</option>
                  {models.map((item) => (
                    <option key={item.id} value={item.id}>{item.id}</option>
                  ))}
                </select>
                <p className="mt-1 text-[10px] text-gray-600">
                  Overrides every agent node in this run. Block and decision nodes are unchanged.
                </p>
              </div>
            )}
          </div>
          <div className="text-[10px] text-gray-600">
            A ready run will be created. Review and start it from the studio.
          </div>
          {error && <div className="rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-gray-800 px-4 py-3">
          <button onClick={onClose} className="rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200">
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={running}
            className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {running ? 'Creating run…' : '▷ Create run'}
          </button>
        </div>
      </div>
    </div>
  )
}
