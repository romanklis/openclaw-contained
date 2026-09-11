'use client'

import { useCallback, useEffect, useState } from 'react'
import { API } from '../lib/api'

const KINDS = ['basic', 'form', 'bearer', 'header', 'cookie']

interface Credential {
  name: string
  kind?: string
  username?: string
  login_url?: string
  header_name?: string
  cookie_name?: string
  allowed_origins?: string[]
  allowed_methods?: string[]
  has_password?: boolean
  has_token?: boolean
  has_header_value?: boolean
  has_cookie_value?: boolean
}

export default function CredentialsPage() {
  const [creds, setCreds] = useState<Credential[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<any>({ kind: 'basic', allowed_origins: '', allowed_methods: 'GET, POST' })

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/credentials`)
      if (res.ok) setCreds(await res.json())
    } catch { /* control plane may be unreachable */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const startEdit = (c: Credential) => {
    setEditing(c.name)
    setForm({
      name: c.name,
      kind: c.kind || 'basic',
      username: c.username || '',
      password: '',
      token: '',
      header_name: c.header_name || '',
      header_value: '',
      cookie_name: c.cookie_name || '',
      cookie_value: '',
      login_url: c.login_url || '',
      username_field: 'username',
      password_field: 'password',
      allowed_origins: (c.allowed_origins || []).join(', '),
      allowed_methods: (c.allowed_methods || []).join(', '),
    })
  }

  const submit = async () => {
    const name = (form.name || '').trim()
    if (!name) { alert('Name is required'); return }
    const payload: any = {
      kind: form.kind,
      username: form.username?.trim() || undefined,
      password: form.password || undefined,
      token: form.token || undefined,
      header_name: form.header_name?.trim() || undefined,
      header_value: form.header_value || undefined,
      cookie_name: form.cookie_name?.trim() || undefined,
      cookie_value: form.cookie_value || undefined,
      login_url: form.login_url?.trim() || undefined,
      username_field: form.username_field?.trim() || 'username',
      password_field: form.password_field?.trim() || 'password',
      allowed_origins: (form.allowed_origins || '').split(',').map((s: string) => s.trim()).filter(Boolean),
      allowed_methods: (form.allowed_methods || 'GET, POST').split(',').map((s: string) => s.trim().toUpperCase()).filter(Boolean),
    }
    try {
      const res = await fetch(`${API}/api/credentials/${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        alert(d.detail || 'Failed to save credential')
        return
      }
      setEditing(null)
      setForm({ kind: 'basic', allowed_origins: '', allowed_methods: 'GET, POST' })
      load()
    } catch {
      alert('Failed to reach control plane')
    }
  }

  const remove = async (name: string) => {
    if (!window.confirm(`Delete credential '${name}'? Agents will no longer be able to request it.`)) return
    const res = await fetch(`${API}/api/credentials/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (res.ok) load()
  }

  const importCookies = async (name: string) => {
    const raw = window.prompt(
      `Paste session cookies for '${name}' (one per line, tab-separated Netscape format:\ndomain\\tincludeSubdomains\\tpath\\tsecure\\texpiry\\tname\\tvalue).\nLines starting with '#' are ignored.`
    )
    if (!raw) return
    const cookies: any[] = []
    for (const line of raw.split('\n')) {
      const t = line.trim()
      if (!t || t.startsWith('#')) continue
      const parts = t.split('\t')
      if (parts.length < 7) continue
      const [domain, , path, secure, , name2, value] = parts
      cookies.push({ name: name2, value, domain, path, secure: secure === 'TRUE' || secure === 'true' })
    }
    if (!cookies.length) { alert('No valid cookie lines found'); return }
    try {
      const res = await fetch(`${API}/api/credentials/${encodeURIComponent(name)}/cookies/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookies, origin: `https://${cookies[0].domain}` }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        alert(d.detail || 'Import failed')
        return
      }
      alert(`Imported ${cookies.length} cookies for '${name}'.`)
    } catch {
      alert('Failed to reach control plane')
    }
  }

  const kindHint: Record<string, string> = {
    basic: 'HTTP Basic — username + password',
    form: 'Login form — username + password, posts to login_url',
    bearer: 'Authorization: Bearer <token>',
    header: 'Custom header (header_name: header_value)',
    cookie: 'Cookie injected per request',
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">Credentials</h1>
        <p className="text-sm text-gray-500">
          Stored credential profiles used by the <code className="text-cyan-300">agent_web</code> gateway.
          Secrets are masked and only ever handled by the credential gateway — agents reference them by name.
        </p>
      </div>

      <div className="card p-4 mb-6">
        <div className="text-sm font-semibold text-white mb-3">
          {editing ? `Edit credential: ${editing}` : 'Add credential'}
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <label className="block">
            <span className="text-gray-400">Name</span>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
              value={form.name || ''}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="my-account"
              disabled={!!editing}
            />
          </label>
          <label className="block">
            <span className="text-gray-400">Type</span>
            <select
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
              value={form.kind || 'basic'}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
        </div>
        <p className="text-[10px] text-gray-600 mt-1 mb-3">{kindHint[form.kind || 'basic']}</p>

        <div className="grid grid-cols-2 gap-3 text-xs">
          {(form.kind === 'basic' || form.kind === 'form') && (
            <>
              <label className="block">
                <span className="text-gray-400">Username</span>
                <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.username || ''} onChange={(e) => setForm({ ...form, username: e.target.value })} />
              </label>
              <label className="block">
                <span className="text-gray-400">Password {editing && '(leave blank to keep)'}</span>
                <input type="password" className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.password || ''} onChange={(e) => setForm({ ...form, password: e.target.value })} />
              </label>
            </>
          )}
          {form.kind === 'form' && (
            <>
              <label className="block">
                <span className="text-gray-400">Login URL</span>
                <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.login_url || ''} onChange={(e) => setForm({ ...form, login_url: e.target.value })}
                  placeholder="https://site/login" />
              </label>
              <label className="block">
                <span className="text-gray-400">Username / password fields</span>
                <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={`${form.username_field || 'username'}, ${form.password_field || 'password'}`}
                  onChange={(e) => {
                    const [u, p] = e.target.value.split(',').map((s: string) => s.trim())
                    setForm({ ...form, username_field: u || 'username', password_field: p || 'password' })
                  }} />
              </label>
            </>
          )}
          {form.kind === 'bearer' && (
            <label className="block col-span-2">
              <span className="text-gray-400">Token {editing && '(leave blank to keep)'}</span>
              <input type="password" className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                value={form.token || ''} onChange={(e) => setForm({ ...form, token: e.target.value })} />
            </label>
          )}
          {form.kind === 'header' && (
            <>
              <label className="block">
                <span className="text-gray-400">Header name</span>
                <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.header_name || ''} onChange={(e) => setForm({ ...form, header_name: e.target.value })}
                  placeholder="X-API-Key" />
              </label>
              <label className="block">
                <span className="text-gray-400">Header value {editing && '(leave blank to keep)'}</span>
                <input type="password" className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.header_value || ''} onChange={(e) => setForm({ ...form, header_value: e.target.value })} />
              </label>
            </>
          )}
          {form.kind === 'cookie' && (
            <>
              <label className="block">
                <span className="text-gray-400">Cookie name</span>
                <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.cookie_name || ''} onChange={(e) => setForm({ ...form, cookie_name: e.target.value })} />
              </label>
              <label className="block">
                <span className="text-gray-400">Cookie value {editing && '(leave blank to keep)'}</span>
                <input type="password" className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white"
                  value={form.cookie_value || ''} onChange={(e) => setForm({ ...form, cookie_value: e.target.value })} />
              </label>
            </>
          )}
          <label className="block">
            <span className="text-gray-400">Allowed origins (comma separated; empty = any)</span>
            <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono"
              value={form.allowed_origins || ''} onChange={(e) => setForm({ ...form, allowed_origins: e.target.value })}
              placeholder="https://example.com" />
          </label>
          <label className="block">
            <span className="text-gray-400">Allowed methods</span>
            <input className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-white font-mono"
              value={form.allowed_methods || ''} onChange={(e) => setForm({ ...form, allowed_methods: e.target.value })}
              placeholder="GET, POST" />
          </label>
        </div>

        <div className="flex gap-2 mt-4">
          <button onClick={submit} className="btn-primary text-xs">{editing ? 'Save changes' : 'Add credential'}</button>
          {editing && (
            <button onClick={() => { setEditing(null); setForm({ kind: 'basic', allowed_origins: '', allowed_methods: 'GET, POST' }) }}
              className="btn-secondary text-xs">Cancel</button>
          )}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold text-white mb-3">Stored credentials ({creds.length})</div>
        {loading ? (
          <p className="text-xs text-gray-500">Loading…</p>
        ) : creds.length === 0 ? (
          <p className="text-xs text-gray-600">No credentials yet. Add one above — agents can then request it by name.</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 border-b border-[#232333]">
                <th className="py-2 pr-3">Name</th>
                <th className="py-2 pr-3">Type</th>
                <th className="py-2 pr-3">Secrets</th>
                <th className="py-2 pr-3">Methods</th>
                <th className="py-2 pr-3">Origins</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {creds.map((c) => (
                <tr key={c.name} className="border-b border-[#1a1a2a] last:border-0">
                  <td className="py-2 pr-3 font-mono text-white">{c.name}</td>
                  <td className="py-2 pr-3 text-gray-300">{c.kind}</td>
                  <td className="py-2 pr-3 text-gray-400">
                    {c.has_password ? '🔑 password ' : ''}
                    {c.has_token ? '🔑 token ' : ''}
                    {(c.has_header_value ? '🔑 header ' : '')}
                    {c.has_cookie_value ? '🍪 cookie ' : ''}
                    {c.username ? `(${c.username})` : ''}
                  </td>
                  <td className="py-2 pr-3 text-gray-400">{(c.allowed_methods || []).join(', ')}</td>
                  <td className="py-2 pr-3 text-gray-400 max-w-[160px] truncate">{(c.allowed_origins || []).join(', ') || 'any'}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <button onClick={() => startEdit(c)} className="text-indigo-400 hover:text-indigo-300 mr-3">Edit</button>
                    <button onClick={() => importCookies(c.name)} title="Import session cookies (Netscape format) for browser logins"
                      className="text-cyan-400 hover:text-cyan-300 mr-3">🍪</button>
                    <button onClick={() => remove(c.name)} className="text-red-400 hover:text-red-300">Delete</button>
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
