'use client'

import { useState, useEffect, useCallback, Suspense } from 'react'
import { API } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────

interface ImageTypeSummary {
  image_type: string
  notes: string | null
  pip: number
  apt: number
  apk: number
  npm: number
  exceptions: number
}

interface Package {
  id: number
  image_type: string
  manager: string
  package_name: string
  notes: string | null
  is_exception: string
  created_at: string
  updated_at: string | null
}

interface Alias {
  id: number
  direction: string
  from_name: string
  to_name: string
  created_at: string
}

interface FullConfig {
  image_types: ImageTypeSummary[]
  aliases: Record<string, Record<string, string>>
  raw: Record<string, any>
}

// ─── Component ───────────────────────────────────────────────────

function SupplyChainContent() {
  const [config, setConfig] = useState<FullConfig | null>(null)
  const [packages, setPackages] = useState<Package[]>([])
  const [aliases, setAliases] = useState<Alias[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [activeTab, setActiveTab] = useState<'overview' | 'packages' | 'aliases' | 'exceptions'>('overview')
  const [selectedImageType, setSelectedImageType] = useState<string>('')
  const [selectedManager, setSelectedManager] = useState<string>('')

  // Add package form
  const [showAddForm, setShowAddForm] = useState(false)
  const [newPkgImageType, setNewPkgImageType] = useState('')
  const [newPkgManager, setNewPkgManager] = useState('pip')
  const [newPkgName, setNewPkgName] = useState('')
  const [newPkgNotes, setNewPkgNotes] = useState('')
  const [newPkgIsException, setNewPkgIsException] = useState(false)
  const [addingPkg, setAddingPkg] = useState(false)
  const [addMsg, setAddMsg] = useState<string | null>(null)

  // Add alias form
  const [showAddAlias, setShowAddAlias] = useState(false)
  const [newAliasDirection, setNewAliasDirection] = useState('apt_to_apk')
  const [newAliasFrom, setNewAliasFrom] = useState('')
  const [newAliasTo, setNewAliasTo] = useState('')
  const [addingAlias, setAddingAlias] = useState(false)

  // Seed state
  const [seeding, setSeeding] = useState(false)
  const [seedMsg, setSeedMsg] = useState<string | null>(null)

  // ─── Fetch functions ─────────────────────────────────────────

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/supply-chain/config`)
      if (!res.ok) throw new Error(`Failed to load config: ${res.status}`)
      const data: FullConfig = await res.json()
      setConfig(data)
      if (!selectedImageType && data.image_types.length > 0) {
        setSelectedImageType(data.image_types[0].image_type)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load config')
    }
  }, [selectedImageType])

  const fetchPackages = useCallback(async () => {
    try {
      let url = `${API}/api/supply-chain/packages`
      const params = new URLSearchParams()
      if (selectedImageType) params.set('image_type', selectedImageType)
      if (selectedManager) params.set('manager', selectedManager)
      if (activeTab === 'exceptions') params.set('exception_only', 'true')
      if (params.toString()) url += '?' + params.toString()
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Failed to load packages: ${res.status}`)
      setPackages(await res.json())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load packages')
    }
  }, [selectedImageType, selectedManager, activeTab])

  const fetchAliases = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/supply-chain/aliases`)
      if (!res.ok) throw new Error(`Failed to load aliases: ${res.status}`)
      setAliases(await res.json())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load aliases')
    }
  }, [])

  const fetchAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchConfig(), fetchPackages(), fetchAliases()])
    setLoading(false)
  }, [fetchConfig, fetchPackages, fetchAliases])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  useEffect(() => {
    if (!loading) fetchPackages()
  }, [selectedImageType, selectedManager, activeTab])

  // ─── Actions ─────────────────────────────────────────────────

  const handleSeed = async () => {
    setSeeding(true)
    setSeedMsg(null)
    try {
      const res = await fetch(`${API}/api/supply-chain/seed`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setSeedMsg(`✓ Seeded: ${data.added_image_types} image types, ${data.added_packages} packages, ${data.added_aliases} aliases`)
      await fetchAll()
    } catch (err) {
      setSeedMsg(`✗ ${err instanceof Error ? err.message : 'Failed'}`)
    } finally {
      setSeeding(false)
    }
  }

  const handleAddPackage = async () => {
    if (!newPkgName.trim() || !newPkgImageType) return
    setAddingPkg(true)
    setAddMsg(null)
    try {
      const res = await fetch(`${API}/api/supply-chain/packages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_type: newPkgImageType,
          manager: newPkgManager,
          package_name: newPkgName.trim(),
          notes: newPkgNotes || null,
          is_exception: newPkgIsException,
        }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed')
      }
      setAddMsg(`✓ Added ${newPkgName}`)
      setNewPkgName('')
      setNewPkgNotes('')
      setNewPkgIsException(false)
      await Promise.all([fetchConfig(), fetchPackages()])
    } catch (err) {
      setAddMsg(`✗ ${err instanceof Error ? err.message : 'Failed'}`)
    } finally {
      setAddingPkg(false)
    }
  }

  const handleDeletePackage = async (pkg: Package) => {
    if (!confirm(`Remove "${pkg.package_name}" from ${pkg.image_type}/${pkg.manager}?`)) return
    try {
      const res = await fetch(`${API}/api/supply-chain/packages/${pkg.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete')
      await Promise.all([fetchConfig(), fetchPackages()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete')
    }
  }

  const handleAddAlias = async () => {
    if (!newAliasFrom.trim() || !newAliasTo.trim()) return
    setAddingAlias(true)
    try {
      const res = await fetch(`${API}/api/supply-chain/aliases`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          direction: newAliasDirection,
          from_name: newAliasFrom.trim(),
          to_name: newAliasTo.trim(),
        }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed')
      }
      setNewAliasFrom('')
      setNewAliasTo('')
      await Promise.all([fetchConfig(), fetchAliases()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add alias')
    } finally {
      setAddingAlias(false)
    }
  }

  const handleDeleteAlias = async (alias: Alias) => {
    if (!confirm(`Remove alias "${alias.from_name} → ${alias.to_name}"?`)) return
    try {
      const res = await fetch(`${API}/api/supply-chain/aliases/${alias.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error('Failed to delete')
      await Promise.all([fetchConfig(), fetchAliases()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete alias')
    }
  }

  // ─── Render ──────────────────────────────────────────────────

  if (loading && !config) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400">Loading supply chain…</div>
      </div>
    )
  }

  const managerOptions = ['pip', 'apt', 'apk', 'npm']
  const imageTypes = config?.image_types || []

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Supply Chain</h1>
          <p className="text-sm text-gray-400 mt-1">
            Manage the package allowlist that governs what agents can install
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleSeed} disabled={seeding} className="btn-secondary text-sm">
            {seeding ? 'Seeding…' : '⬆ Seed from YAML'}
          </button>
        </div>
      </div>

      {seedMsg && (
        <div className={`mb-4 px-4 py-2 rounded text-sm ${seedMsg.startsWith('✓') ? 'bg-green-900/30 text-green-400' : 'bg-red-900/30 text-red-400'}`}>
          {seedMsg}
        </div>
      )}

      {error && (
        <div className="mb-4 px-4 py-2 rounded bg-red-900/30 text-red-400 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-[#0d0d14] rounded-lg p-1 w-fit">
        {(['overview', 'packages', 'exceptions', 'aliases'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'bg-[#232333] text-white'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {tab === 'overview' ? '📊 Overview' :
             tab === 'packages' ? '📦 Packages' :
             tab === 'exceptions' ? '⚠ Exceptions' :
             '🔄 Aliases'}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {imageTypes.map(it => (
            <div key={it.image_type} className="card p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold text-white">{it.image_type}</h3>
                {it.exceptions > 0 && (
                  <span className="text-xs px-2 py-0.5 bg-yellow-900/40 text-yellow-400 rounded-full">
                    {it.exceptions} exception{it.exceptions !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              {it.notes && (
                <p className="text-xs text-gray-400 mb-3">{it.notes}</p>
              )}
              <div className="grid grid-cols-4 gap-2 text-center">
                <div className="bg-[#0d0d14] rounded-lg p-2">
                  <div className="text-lg font-bold text-blue-400">{it.pip}</div>
                  <div className="text-[10px] text-gray-500 uppercase">pip</div>
                </div>
                <div className="bg-[#0d0d14] rounded-lg p-2">
                  <div className="text-lg font-bold text-green-400">{it.apt}</div>
                  <div className="text-[10px] text-gray-500 uppercase">apt</div>
                </div>
                <div className="bg-[#0d0d14] rounded-lg p-2">
                  <div className="text-lg font-bold text-purple-400">{it.apk}</div>
                  <div className="text-[10px] text-gray-500 uppercase">apk</div>
                </div>
                <div className="bg-[#0d0d14] rounded-lg p-2">
                  <div className="text-lg font-bold text-orange-400">{it.npm}</div>
                  <div className="text-[10px] text-gray-500 uppercase">npm</div>
                </div>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => { setSelectedImageType(it.image_type); setActiveTab('packages') }}
                  className="text-xs text-indigo-400 hover:text-indigo-300"
                >
                  View packages →
                </button>
              </div>
            </div>
          ))}

          {imageTypes.length === 0 && (
            <div className="col-span-2 card p-8 text-center">
              <p className="text-gray-400 mb-4">No supply chain data yet</p>
              <button onClick={handleSeed} className="btn-primary text-sm">
                Seed from YAML
              </button>
            </div>
          )}
        </div>
      )}

      {/* Packages Tab */}
      {(activeTab === 'packages' || activeTab === 'exceptions') && (
        <div>
          {/* Filters */}
          <div className="flex gap-3 mb-4 items-center flex-wrap">
            <select
              value={selectedImageType}
              onChange={e => setSelectedImageType(e.target.value)}
              className="input-field text-sm w-44"
            >
              <option value="">All image types</option>
              {imageTypes.map(it => (
                <option key={it.image_type} value={it.image_type}>{it.image_type}</option>
              ))}
            </select>
            <select
              value={selectedManager}
              onChange={e => setSelectedManager(e.target.value)}
              className="input-field text-sm w-32"
            >
              <option value="">All managers</option>
              {managerOptions.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <span className="text-xs text-gray-500">
              {packages.length} package{packages.length !== 1 ? 's' : ''}
            </span>
            <div className="ml-auto">
              <button onClick={() => setShowAddForm(!showAddForm)} className="btn-primary text-sm">
                {showAddForm ? '✕ Cancel' : '+ Add Package'}
              </button>
            </div>
          </div>

          {/* Add Package Form */}
          {showAddForm && (
            <div className="card p-4 mb-4 border border-indigo-500/30">
              <h4 className="text-sm font-semibold text-white mb-3">
                {activeTab === 'exceptions' ? 'Add Exception' : 'Add Package'}
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                <select
                  value={newPkgImageType}
                  onChange={e => setNewPkgImageType(e.target.value)}
                  className="input-field text-sm"
                >
                  <option value="">Image type…</option>
                  {imageTypes.map(it => (
                    <option key={it.image_type} value={it.image_type}>{it.image_type}</option>
                  ))}
                </select>
                <select
                  value={newPkgManager}
                  onChange={e => setNewPkgManager(e.target.value)}
                  className="input-field text-sm"
                >
                  {managerOptions.map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <input
                  type="text"
                  placeholder="Package name"
                  value={newPkgName}
                  onChange={e => setNewPkgName(e.target.value)}
                  className="input-field text-sm"
                  onKeyDown={e => e.key === 'Enter' && handleAddPackage()}
                />
                <input
                  type="text"
                  placeholder="Notes (optional)"
                  value={newPkgNotes}
                  onChange={e => setNewPkgNotes(e.target.value)}
                  className="input-field text-sm"
                />
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1.5 text-sm text-gray-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={activeTab === 'exceptions' ? true : newPkgIsException}
                      onChange={e => setNewPkgIsException(e.target.checked)}
                      disabled={activeTab === 'exceptions'}
                      className="accent-yellow-500"
                    />
                    Exception
                  </label>
                  <button
                    onClick={handleAddPackage}
                    disabled={addingPkg || !newPkgName.trim() || !newPkgImageType}
                    className="btn-success text-sm flex-1"
                  >
                    {addingPkg ? '…' : 'Add'}
                  </button>
                </div>
              </div>
              {addMsg && (
                <div className={`mt-2 text-xs ${addMsg.startsWith('✓') ? 'text-green-400' : 'text-red-400'}`}>
                  {addMsg}
                </div>
              )}
            </div>
          )}

          {/* Package Table */}
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1a1a2a]">
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">Package</th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">Image</th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">Manager</th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">Type</th>
                  <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">Notes</th>
                  <th className="text-right px-4 py-3 text-xs text-gray-500 uppercase">Actions</th>
                </tr>
              </thead>
              <tbody>
                {packages.map(pkg => (
                  <tr key={pkg.id} className="border-b border-[#1a1a2a]/50 hover:bg-[#1a1a2a]/30">
                    <td className="px-4 py-2.5 text-white font-mono text-xs">{pkg.package_name}</td>
                    <td className="px-4 py-2.5 text-gray-400 text-xs">{pkg.image_type}</td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        pkg.manager === 'pip' ? 'bg-blue-900/40 text-blue-400' :
                        pkg.manager === 'apt' ? 'bg-green-900/40 text-green-400' :
                        pkg.manager === 'apk' ? 'bg-purple-900/40 text-purple-400' :
                        'bg-orange-900/40 text-orange-400'
                      }`}>
                        {pkg.manager}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      {pkg.is_exception === 'true' ? (
                        <span className="text-xs px-1.5 py-0.5 bg-yellow-900/40 text-yellow-400 rounded">
                          exception
                        </span>
                      ) : (
                        <span className="text-xs text-gray-500">standard</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs max-w-[200px] truncate">
                      {pkg.notes || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        onClick={() => handleDeletePackage(pkg)}
                        className="text-red-400 hover:text-red-300 text-xs"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {packages.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-gray-500">
                      {activeTab === 'exceptions' ? 'No exceptions configured' : 'No packages found'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Aliases Tab */}
      {activeTab === 'aliases' && (
        <div>
          <div className="flex justify-between items-center mb-4">
            <p className="text-sm text-gray-400">
              Cross-distro package name mappings (Debian apt ↔ Alpine apk)
            </p>
            <button onClick={() => setShowAddAlias(!showAddAlias)} className="btn-primary text-sm">
              {showAddAlias ? '✕ Cancel' : '+ Add Alias'}
            </button>
          </div>

          {/* Add Alias Form */}
          {showAddAlias && (
            <div className="card p-4 mb-4 border border-indigo-500/30">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <select
                  value={newAliasDirection}
                  onChange={e => setNewAliasDirection(e.target.value)}
                  className="input-field text-sm"
                >
                  <option value="apt_to_apk">apt → apk</option>
                  <option value="apk_to_apt">apk → apt</option>
                </select>
                <input
                  type="text"
                  placeholder="From name"
                  value={newAliasFrom}
                  onChange={e => setNewAliasFrom(e.target.value)}
                  className="input-field text-sm"
                />
                <input
                  type="text"
                  placeholder="To name"
                  value={newAliasTo}
                  onChange={e => setNewAliasTo(e.target.value)}
                  className="input-field text-sm"
                  onKeyDown={e => e.key === 'Enter' && handleAddAlias()}
                />
                <button
                  onClick={handleAddAlias}
                  disabled={addingAlias || !newAliasFrom.trim() || !newAliasTo.trim()}
                  className="btn-success text-sm"
                >
                  {addingAlias ? '…' : 'Add Alias'}
                </button>
              </div>
            </div>
          )}

          {/* Aliases grouped by direction */}
          {['apt_to_apk', 'apk_to_apt'].map(dir => {
            const dirAliases = aliases.filter(a => a.direction === dir)
            if (dirAliases.length === 0) return null
            return (
              <div key={dir} className="mb-6">
                <h3 className="text-sm font-semibold text-white mb-3">
                  {dir === 'apt_to_apk' ? '🐧 apt → apk (Debian → Alpine)' : '🏔 apk → apt (Alpine → Debian)'}
                  <span className="text-gray-500 font-normal ml-2">({dirAliases.length})</span>
                </h3>
                <div className="card overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-[#1a1a2a]">
                        <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">From</th>
                        <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase"></th>
                        <th className="text-left px-4 py-3 text-xs text-gray-500 uppercase">To</th>
                        <th className="text-right px-4 py-3 text-xs text-gray-500 uppercase">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dirAliases.map(alias => (
                        <tr key={alias.id} className="border-b border-[#1a1a2a]/50 hover:bg-[#1a1a2a]/30">
                          <td className="px-4 py-2.5 text-white font-mono text-xs">{alias.from_name}</td>
                          <td className="px-4 py-2.5 text-gray-500 text-xs">→</td>
                          <td className="px-4 py-2.5 text-indigo-400 font-mono text-xs">{alias.to_name}</td>
                          <td className="px-4 py-2.5 text-right">
                            <button
                              onClick={() => handleDeleteAlias(alias)}
                              className="text-red-400 hover:text-red-300 text-xs"
                            >
                              Remove
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}

          {aliases.length === 0 && (
            <div className="card p-8 text-center text-gray-500">
              No aliases configured
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function SupplyChainPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400">Loading…</div>
      </div>
    }>
      <SupplyChainContent />
    </Suspense>
  )
}
