'use client'

import { useCallback, useEffect, useState } from 'react'
import { API } from '../lib/api'

interface Block {
  id: string
  name: string
  description?: string
  runtime?: string
  entrypoint?: string
  status?: string
  version?: number
  inputs_schema?: any
  outputs_schema?: any
  conformance?: any
  code?: { files?: Record<string, string> }
  created_at?: string
}

export default function BlocksPage() {
  const [blocks, setBlocks] = useState<Block[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [form, setForm] = useState({ name: '', entrypoint: 'main', code: '', inputs_schema: '', conformance: '' })
  const [testing, setTesting] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/blocks`)
      if (res.ok) setBlocks(await res.json())
    } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const register = async () => {
    setBusy(true); setMsg(null)
    try {
      const res = await fetch(`${API}/api/blocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim(),
          entrypoint: form.entrypoint.trim() || 'main',
          code: { files: { 'block.py': form.code } },
          inputs_schema: form.inputs_schema.trim() ? JSON.parse(form.inputs_schema) : {},
          conformance: form.conformance.trim() ? JSON.parse(form.conformance) : {},
          status: 'draft',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) { setMsg(`❌ ${data.detail || 'register failed'}`); return }
      setMsg(`✅ Registered block ${data.id} (draft — activate to use)`)
      setForm({ name: '', entrypoint: 'main', code: '', inputs_schema: '', conformance: '' })
      load()
    } catch (e: any) { setMsg(`❌ ${e.message}`) } finally { setBusy(false) }
  }

  const setStatus = async (b: Block, status: string) => {
    await fetch(`${API}/api/blocks/${encodeURIComponent(b.id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
    load()
  }

  const runTest = async (b: Block) => {
    setTesting(b.id); setMsg(null)
    try {
      const res = await fetch(`${API}/api/blocks/${encodeURIComponent(b.id)}/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const data = await res.json().catch(() => ({}))
      setMsg(`Test ${b.id}: ${JSON.stringify(data).slice(0, 300)}`)
    } catch (e: any) { setMsg(`❌ ${e.message}`) } finally { setTesting(null) }
  }

  const remove = async (b: Block) => {
    if (!window.confirm(`Delete block '${b.id}'?`)) return
    await fetch(`${API}/api/blocks/${encodeURIComponent(b.id)}`, { method: 'DELETE' })
    load()
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">Blocks</h1>
        <p className="text-sm text-gray-500">
          Deterministic, typed units of work (no LLM loop). DAG flows compose blocks through edges;
          agents only derive new blocks when no existing one fits.
        </p>
      </div>

      <div className="card p-4 mb-6">
        <div className="text-sm font-semibold text-white mb-3">Register a block (worker-python)</div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <label className="block">
            <span className="text-gray-400">Name</span>
            <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="google-photos-album-resolve" />
          </label>
          <label className="block">
            <span className="text-gray-400">Entrypoint</span>
            <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono"
              value={form.entrypoint} onChange={(e) => setForm({ ...form, entrypoint: e.target.value })} />
          </label>
        </div>
        <label className="block mt-3 text-xs">
          <span className="text-gray-400">Python code (single file, saved as block.py)</span>
          <textarea rows={8} className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono mt-1"
            value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })}
            placeholder={'def main(inputs):\n    return {"echo": inputs}\n'} />
        </label>
        <div className="grid grid-cols-2 gap-3 text-xs mt-3">
          <label className="block">
            <span className="text-gray-400">inputs_schema (JSON, optional)</span>
            <textarea rows={3} className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono mt-1"
              value={form.inputs_schema} onChange={(e) => setForm({ ...form, inputs_schema: e.target.value })} />
          </label>
          <label className="block">
            <span className="text-gray-400">conformance {`{sample_input, expect}`} (JSON, optional)</span>
            <textarea rows={3} className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono mt-1"
              value={form.conformance} onChange={(e) => setForm({ ...form, conformance: e.target.value })} />
          </label>
        </div>
        <div className="flex items-center gap-3 mt-4">
          <button onClick={register} disabled={busy || !form.name.trim() || !form.code.trim()}
            className="btn-primary text-xs disabled:opacity-50">Register block</button>
          {msg && <span className="text-xs text-gray-300">{msg}</span>}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold text-white mb-3">Registered blocks ({blocks.length})</div>
        {loading ? <p className="text-xs text-gray-500">Loading…</p> : blocks.length === 0 ? (
          <p className="text-xs text-gray-600">No blocks yet. Register one above (or promote from a successful task via the API).</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 border-b border-[#232333]">
                <th className="py-2 pr-3">Name</th><th className="py-2 pr-3">Entry</th>
                <th className="py-2 pr-3">Runtime</th><th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Files</th><th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {blocks.map((b) => (
                <tr key={b.id} className="border-b border-[#1a1a2a] last:border-0">
                  <td className="py-2 pr-3 font-mono text-white">{b.id}
                    <div className="text-[10px] text-gray-500">{b.description}</div></td>
                  <td className="py-2 pr-3 text-gray-300 font-mono">{b.entrypoint}</td>
                  <td className="py-2 pr-3 text-gray-400">{b.runtime}</td>
                  <td className="py-2 pr-3">
                    <span className={`px-2 py-0.5 rounded text-[10px] ${b.status === 'active' ? 'bg-emerald-900 text-emerald-300' : b.status === 'archived' ? 'bg-gray-700 text-gray-400' : 'bg-amber-900 text-amber-300'}`}>{b.status}</span>
                  </td>
                  <td className="py-2 pr-3 text-gray-400">{Object.keys(b.code?.files || {}).join(', ') || '—'}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <button onClick={() => runTest(b)} disabled={testing === b.id}
                      className="text-cyan-400 hover:text-cyan-300 mr-3">Test</button>
                    {b.status === 'draft' && <button onClick={() => setStatus(b, 'active')} className="text-emerald-400 hover:text-emerald-300 mr-3">Activate</button>}
                    {b.status === 'active' && <button onClick={() => setStatus(b, 'archived')} className="text-amber-400 hover:text-amber-300 mr-3">Archive</button>}
                    <button onClick={() => remove(b)} className="text-red-400 hover:text-red-300">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
