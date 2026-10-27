'use client'

import { useCallback, useEffect, useState } from 'react'
import { API } from '../lib/api'

interface ImageBuildRecord {
  id: number
  version_id: string | null
  definition_id: string
  build_id: string
  trigger_type: string
  capability_request_id: number | null
  task_id: string | null
  node_id: string | null
  requested_by: string
  approved_by: string
  status: string
  image_tag: string
  image_digest: string
  error: string
  created_at: string | null
  finished_at: string | null
}

interface ImageVersion {
  id: string
  definition_id: string
  version: string
  commit_message?: string
  latest_build?: ImageBuildRecord | null
  git_tag: string | null
  git_commit: string | null
  bundle_hash: string | null
  dockerfile_hash: string | null
  base_image: string | null
  runtime_family: string | null
  adapter_type: string | null
  adapter_sha: string | null
  runner_sha: string | null
  entrypoint: string | null
  capability_fingerprint: string | null
  capabilities: { manager: string; name: string; version?: string }[]
  image_tag: string | null
  image_digest: string | null
  parent_version_id?: string | null
  status: string
  source_available: boolean
  created_at: string | null
  aliases: Record<string, string>
}

interface ImageDefinition {
  id: string
  name: string
  description: string
  runtime_family: string
  adapter_type: string
  entrypoint: string
  enabled: boolean
  tags: string[]
  version_count: number
  latest: ImageVersion | null
  aliases: Record<string, string>
}

interface CapabilityHit {
  manager: string
  name: string
  version: string
  definition_id: string
  definition_name: string
  version_id: string
  image_version: string
  image_tag: string | null
  status: string
}

interface HistoryRow {
  version: ImageVersion
  definition: { id: string; name: string; runtime_family: string; enabled: boolean }
  build: ImageBuildRecord | null
  capability_delta: {
    added: { manager: string; name: string; version: string }[]
    removed: { manager: string; name: string; version: string }[]
  }
}

const EDITOR_TEXT_EXTENSIONS = new Set([
  '.sh', '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.yaml', '.yml',
  '.toml', '.ini', '.cfg', '.txt', '.md', '.rst', '.html', '.css', '.sql',
  '.xml', '.env', '.service', '.conf', '.lock',
])

function isTextPath(path: string) {
  const name = path.split('/').pop() || ''
  if (name === 'Dockerfile' || name.startsWith('Dockerfile.')) return true
  const dot = name.lastIndexOf('.')
  return dot >= 0 && EDITOR_TEXT_EXTENSIONS.has(name.slice(dot).toLowerCase())
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    const slice = bytes.subarray(i, i + chunk)
    for (let j = 0; j < slice.length; j++) {
      binary += String.fromCharCode(slice[j])
    }
  }
  return btoa(binary)
}

function shortHash(value?: string | null) {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 12)}…` : value
}

function statusColor(status: string) {
  if (status === 'ready' || status === 'succeeded') return 'text-emerald-400 border-emerald-500/30'
  if (status === 'failed' || status === 'denied') return 'text-red-400 border-red-500/30'
  if (status === 'building') return 'text-amber-400 border-amber-500/30'
  return 'text-gray-400 border-gray-600/40'
}

export default function ImageCatalogPage() {
  const [definitions, setDefinitions] = useState<ImageDefinition[]>([])
  const [detectedDefs, setDetectedDefs] = useState<ImageDefinition[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [versions, setVersions] = useState<ImageVersion[]>([])
  const [activeTab, setActiveTab] = useState<'all' | 'history' | 'builds'>('all')
  const [imageQuery, setImageQuery] = useState('')
  const [imageFilter, setImageFilter] = useState<'all' | 'detected'>('all')
  const [contentTab, setContentTab] = useState<'files' | 'compare' | 'logs'>('files')
  const [compareTo, setCompareTo] = useState('')
  const [capOpen, setCapOpen] = useState(false)
  const [capQuery, setCapQuery] = useState('')
  const [capResults, setCapResults] = useState<CapabilityHit[]>([])
  const [capLoading, setCapLoading] = useState(false)
  const [fileVersion, setFileVersion] = useState<ImageVersion | null>(null)
  const [tree, setTree] = useState<{ mode: string; type: string; sha: string; size: string; path: string }[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [busy, setBusy] = useState(false)

  // History / builds
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [builds, setBuilds] = useState<ImageBuildRecord[]>([])
  const [buildLogs, setBuildLogs] = useState<{ id: number; build_id: string; status: string; logs: string; error: string } | null>(null)
  const [diffText, setDiffText] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [buildsLoading, setBuildsLoading] = useState(false)

  // Browser source editor
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorBase, setEditorBase] = useState<ImageVersion | null>(null)
  const [editorTree, setEditorTree] = useState<{ path: string; size: string }[]>([])
  const [editorFiles, setEditorFiles] = useState<Record<string, string>>({})
  const [editorEncoding, setEditorEncoding] = useState<Record<string, 'utf-8' | 'base64'>>({})
  const [editorActive, setEditorActive] = useState<string | null>(null)
  const [editorMessage, setEditorMessage] = useState('')
  const [editorVersion, setEditorVersion] = useState('')
  const [editorDeleted, setEditorDeleted] = useState<string[]>([])
  const [editorSaving, setEditorSaving] = useState(false)

  const loadDefinitions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [mainResp, detectedResp] = await Promise.all([
        fetch(`${API}/api/image-catalog/definitions?exclude_detected=true&limit=300`),
        fetch(`${API}/api/image-catalog/definitions?detected_only=true&limit=100`),
      ])
      if (!mainResp.ok) throw new Error(await mainResp.text())
      setDefinitions(await mainResp.json())
      setDetectedDefs(detectedResp.ok ? await detectedResp.json() : [])
    } catch (err: any) {
      setError(err.message || 'Failed to load catalog')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadDefinitions() }, [loadDefinitions])

  const loadVersions = async (definitionId: string, preferredVersionId?: string) => {
    setSelectedId(definitionId)
    setFileVersion(null)
    setTree([])
    setSelectedFile(null)
    setFileContent('')
    setDiffText(null)
    setBuildLogs(null)
    setEditorOpen(false)
    setContentTab('files')
    try {
      const resp = await fetch(`${API}/api/image-catalog/definitions/${encodeURIComponent(definitionId)}/versions?limit=200`)
      if (!resp.ok) throw new Error(await resp.text())
      const list: ImageVersion[] = await resp.json()
      setVersions(list)
      const target =
        (preferredVersionId ? list.find((version) => version.id === preferredVersionId) : undefined) ||
        list.find((version) => version.aliases?.stable === version.id) ||
        list.find((version) => version.aliases?.latest === version.id) ||
        list[0]
      if (target) await showFiles(target)
    } catch (err: any) {
      setError(err.message || 'Failed to load versions')
    }
  }

  useEffect(() => {
    if (selectedId) return
    const first = definitions[0] || detectedDefs[0]
    if (first) loadVersions(first.id)
  }, [definitions, detectedDefs, selectedId])

  const refreshSelectedVersion = async () => {
    if (!selectedId) return
    try {
      const resp = await fetch(`${API}/api/image-catalog/definitions/${encodeURIComponent(selectedId)}/versions?limit=200`)
      if (!resp.ok) return
      const list: ImageVersion[] = await resp.json()
      setVersions(list)
      setFileVersion((current) =>
        current ? list.find((version) => version.id === current.id) || current : current
      )
    } catch {
      // Keep the last known version on transient errors.
    }
  }

  useEffect(() => {
    if (!fileVersion || fileVersion.status !== 'building') return
    const timer = setInterval(() => { refreshSelectedVersion() }, 5000)
    return () => clearInterval(timer)
  }, [fileVersion?.id, fileVersion?.status, selectedId])

  useEffect(() => {
    if (!buildLogs || (buildLogs.status !== 'pending' && buildLogs.status !== 'building')) return
    const timer = setInterval(() => { openBuildLogs(buildLogs.id) }, 4000)
    return () => clearInterval(timer)
  }, [buildLogs?.id, buildLogs?.status])

  const seedImages = async () => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/seed`, { method: 'POST' })
      if (!resp.ok) throw new Error(await resp.text())
      const result = await resp.json()
      const catalog = result.catalog || {}
      const agent = result.agent_images || {}
      const metadata = agent.error
        ? `; planner metadata: ${agent.error}`
        : `; planner images +${agent.created ?? 0} updated ${agent.updated ?? 0}`
      setMessage(
        `Seed complete: ${catalog.definitions ?? 0} definitions, ${catalog.versions ?? 0} versions (skipped ${catalog.skipped ?? 0})${metadata}.`
      )
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const reconcile = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/reconcile`, { method: 'POST' })
      if (!resp.ok) throw new Error(await resp.text())
      const result = await resp.json()
      setMessage(`Registry scan: ${result.scanned} tags scanned, ${result.created} new images detected.`)
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    } finally { setBusy(false) }
  }

  const promote = async (versionId: string, alias: string) => {
    setError(null); setMessage(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${versionId}/promote`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      setMessage(`Promoted ${alias} → ${versionId}. New DAGs will use it.`)
      if (selectedId) await loadVersions(selectedId, fileVersion?.id)
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const scanCapabilities = async (versionId: string) => {
    setError(null)
    setMessage(null)
    setBusy(true)
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${versionId}/scan-capabilities`, {
        method: 'POST',
      })
      if (!resp.ok) throw new Error(await resp.text())
      const result = await resp.json()
      setMessage(`Capability scan complete: ${result.added?.length ?? 0} new capability row(s).`)
      if (selectedId) await loadVersions(selectedId, fileVersion?.id)
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (definition: ImageDefinition) => {
    setError(null)
    setMessage(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/definitions/${encodeURIComponent(definition.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !definition.enabled }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      setMessage(`${definition.id} ${definition.enabled ? 'disabled' : 'enabled'} for planner reuse.`)
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const loadHistory = async () => {
    setHistoryLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/history?exclude_detected=true&limit=200`)
      if (!resp.ok) throw new Error(await resp.text())
      setHistory(await resp.json())
    } catch (err: any) {
      setError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const loadBuilds = async () => {
    setBuildsLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/builds?limit=200`)
      if (!resp.ok) throw new Error(await resp.text())
      setBuilds(await resp.json())
    } catch (err: any) {
      setError(err.message)
    } finally {
      setBuildsLoading(false)
    }
  }

  const openBuildLogs = async (ref: string | number) => {
    setError(null)
    setBuildLogs(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/builds/${ref}/logs`)
      if (!resp.ok) throw new Error(await resp.text())
      setBuildLogs(await resp.json())
    } catch (err: any) {
      setError(err.message)
    }
  }

  const buildVersion = async (versionId: string) => {
    setError(null)
    setMessage(null)
    setBusy(true)
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${versionId}/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requested_by: 'user' }),
      })
      if (!resp.ok) throw new Error(await resp.text())
      const build = await resp.json()
      setMessage(`Build started (${build.build_id || build.id}).`)
      if (selectedId) await loadVersions(selectedId, versionId)
      setContentTab('logs')
      await loadBuilds()
      await openBuildLogs(build.id)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const openDiff = async (version: ImageVersion, otherId?: string) => {
    setContentTab('compare')
    const other = otherId || version.parent_version_id
    if (!other) {
      setDiffText('This version has no parent to compare with.')
      return
    }
    setCompareTo(other)
    setDiffText('Loading…')
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${version.id}/diff/${other}`)
      if (!resp.ok) throw new Error(await resp.text())
      const data = await resp.json()
      setDiffText(data.diff || '(no changes)')
    } catch (err: any) {
      setDiffText(`Diff failed: ${err.message}`)
    }
  }

  const openEditor = async (version: ImageVersion) => {
    setEditorOpen(true)
    setEditorBase(version)
    setEditorActive(null)
    setEditorFiles({})
    setEditorEncoding({})
    setEditorDeleted([])
    setEditorMessage(`Update ${selected?.id || version.definition_id} image source`)
    setEditorVersion('')
    setEditorTree([])
    setError(null)
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${version.id}/tree`)
      if (!resp.ok) throw new Error(await resp.text())
      const data = await resp.json()
      const files = (data.files || []).filter((f: any) => f.type === 'blob')
      setEditorTree(files.map((f: any) => ({ path: f.path, size: f.size })))
      const textFiles: Record<string, string> = {}
      const enc: Record<string, 'utf-8' | 'base64'> = {}
      for (const entry of files) {
        const size = Number(entry.size)
        if (!isTextPath(entry.path) || (Number.isFinite(size) && size > 200_000)) continue
        const fileResp = await fetch(`${API}/api/image-catalog/versions/${version.id}/file?path=${encodeURIComponent(entry.path)}`)
        if (fileResp.ok) {
          textFiles[entry.path] = await fileResp.text()
          enc[entry.path] = 'utf-8'
        }
      }
      setEditorFiles(textFiles)
      setEditorEncoding(enc)
      setEditorActive(textFiles['Dockerfile'] ? 'Dockerfile' : Object.keys(textFiles)[0] || null)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const addEditorUpload = async (file: File) => {
    const bytes = new Uint8Array(await file.arrayBuffer())
    const text = isTextPath(file.name)
    setEditorFiles((prev) => ({
      ...prev,
      [file.name]: text ? new TextDecoder().decode(bytes) : toBase64(bytes),
    }))
    setEditorEncoding((prev) => ({ ...prev, [file.name]: text ? 'utf-8' : 'base64' }))
    setEditorDeleted((prev) => prev.filter((path) => path !== file.name))
    setEditorTree((prev) =>
      prev.some((entry) => entry.path === file.name)
        ? prev
        : [...prev, { path: file.name, size: String(bytes.length) }]
    )
    setEditorActive(file.name)
  }

  const saveEditor = async () => {
    if (!editorOpen || !editorBase) return
    setEditorSaving(true)
    setError(null)
    setMessage(null)
    try {
      const files = Object.fromEntries(
        Object.entries(editorFiles).map(([path, content]) => [
          path,
          { content, encoding: editorEncoding[path] || 'utf-8' },
        ])
      )
      const resp = await fetch(
        `${API}/api/image-catalog/definitions/${encodeURIComponent(editorBase.definition_id)}/versions/editor`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            base_version_id: editorBase.id,
            version: editorVersion,
            message: editorMessage,
            files,
            deleted: editorDeleted,
          }),
        }
      )
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}))
        throw new Error(
          typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail.detail || detail)
        )
      }
      const data = await resp.json()
      setMessage(`Draft v${data.version.version} committed at ${String(data.version.git_commit || '').slice(0, 8)}. Build it when ready.`)
      setEditorOpen(false)
      await loadVersions(editorBase.definition_id, data.version.id)
      await loadHistory()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setEditorSaving(false)
    }
  }

  const deleteDefinition = async (definition: ImageDefinition) => {
    if (!window.confirm(`Delete ${definition.name} and its git history?`)) return
    setError(null)
    setMessage(null)
    try {
      const resp = await fetch(
        `${API}/api/image-catalog/definitions/${encodeURIComponent(definition.id)}`,
        { method: 'DELETE' }
      )
      if (!resp.ok) throw new Error(await resp.text())
      setSelectedId(null)
      setFileVersion(null)
      setVersions([])
      setTree([])
      setFileContent('')
      setMessage(`Deleted ${definition.id}.`)
      await loadDefinitions()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const loadFile = async (versionId: string, path: string) => {
    setSelectedFile(path)
    setFileContent('Loading…')
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${versionId}/file?path=${encodeURIComponent(path)}`)
      if (!resp.ok) throw new Error(await resp.text())
      setFileContent(await resp.text())
    } catch (err: any) {
      setFileContent(`Failed to load ${path}: ${err.message}`)
    }
  }

  const showFiles = async (version: ImageVersion) => {
    setFileVersion(version)
    setTree([])
    setSelectedFile(null)
    setFileContent('')
    setBuildLogs(null)
    setDiffText(null)
    setEditorOpen(false)
    setCompareTo(version.parent_version_id || '')
    setContentTab('files')
    if (!version.source_available) {
      setFileContent('Source bundle is not available for this version.')
      return
    }
    try {
      const resp = await fetch(`${API}/api/image-catalog/versions/${version.id}/tree`)
      if (resp.status === 409) {
        setFileContent('Source bundle is not available for this version.')
        return
      }
      if (!resp.ok) throw new Error(await resp.text())
      const data = await resp.json()
      const blobs = (data.files || []).filter((entry: any) => entry.type === 'blob')
      setTree(blobs)
      const defaultFile =
        blobs.find((entry: any) => entry.path === 'Dockerfile') ||
        blobs.find((entry: any) => isTextPath(entry.path)) ||
        blobs[0]
      if (defaultFile) await loadFile(version.id, defaultFile.path)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const openFile = async (path: string) => {
    if (!fileVersion) return
    await loadFile(fileVersion.id, path)
  }

  const searchCapabilities = async () => {
    setCapLoading(true)
    try {
      const resp = await fetch(`${API}/api/image-catalog/capabilities?query=${encodeURIComponent(capQuery)}`)
      if (!resp.ok) throw new Error(await resp.text())
      setCapResults(await resp.json())
    } catch (err: any) {
      setError(err.message)
    } finally { setCapLoading(false) }
  }

  const selected = definitions.find((d) => d.id === selectedId) || detectedDefs.find((d) => d.id === selectedId) || null

  const readyCount = definitions.filter((definition) => definition.latest?.status === 'ready').length
  const filteredImages = (imageFilter === 'detected' ? detectedDefs : definitions).filter((definition) => {
    if (!imageQuery.trim()) return true
    const query = imageQuery.trim().toLowerCase()
    return (
      definition.id.toLowerCase().includes(query) ||
      definition.name.toLowerCase().includes(query) ||
      definition.runtime_family.toLowerCase().includes(query)
    )
  })
  const isStable = !!fileVersion && fileVersion.aliases?.stable === fileVersion.id
  const hasCapabilities = (fileVersion?.capabilities?.length || 0) > 0
  const canBuild = !!fileVersion?.source_available && fileVersion?.status !== 'building'

  return (
    <div className="p-8 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div>
          <h1 className="text-2xl font-bold text-white">Image Catalog</h1>
          <p className="text-sm text-gray-500 mt-1">Pick an image, choose a version, inspect or edit its source.</p>
          <p className="text-xs text-gray-600 mt-1">
            {definitions.length} images · {readyCount} ready · {detectedDefs.length} detected
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={loadDefinitions} disabled={loading} className="btn-secondary text-xs">
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <details className="relative">
            <summary className="btn-secondary text-xs cursor-pointer list-none">⋯</summary>
            <div className="absolute right-0 z-30 mt-1 w-56 rounded-lg border border-gray-700 bg-[#12121a] p-1 shadow-xl">
              <button
                onClick={seedImages}
                disabled={busy}
                className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-50"
                title="Import full source bundles from agent-images/ and sync planner metadata; does not build images"
              >
                Seed predefined images
              </button>
              <div className="px-2 py-1 text-[10px] leading-snug text-gray-600">
                Full bundles from <code>agent-images/</code>; no image builds.
              </div>
              <button onClick={reconcile} className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-800">Scan registry</button>
              <button onClick={() => setCapOpen(true)} className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-800">Search capabilities</button>
            </div>
          </details>
        </div>
      </div>

      {error && <div className="mb-4 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-red-400 text-sm">{error}</div>}
      {message && <div className="mb-4 bg-emerald-500/10 border border-emerald-500/20 rounded-lg p-3 text-emerald-300 text-sm">{message}</div>}

      <div className="flex gap-1 mb-4 border-b border-gray-800">
        {(['all', 'history', 'builds'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => {
              setActiveTab(tab)
              if (tab === 'history') loadHistory()
              if (tab === 'builds') loadBuilds()
            }}
            className={`px-3 py-2 text-xs border-b-2 ${
              activeTab === tab ? 'border-indigo-500 text-white' : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab === 'all' ? 'Images' : tab === 'history' ? 'History' : 'Builds'}
          </button>
        ))}
      </div>

      {activeTab === 'all' ? (
        <div className="grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] gap-4">
          <aside className="space-y-2 min-w-0">
            <input
              value={imageQuery}
              onChange={(event) => setImageQuery(event.target.value)}
              placeholder="Search images"
              className="input-field w-full text-sm"
            />
            <div className="flex gap-1">
              <button
                onClick={() => setImageFilter('all')}
                className={`flex-1 px-2 py-1 rounded border text-[11px] ${imageFilter === 'all' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-gray-900 border-gray-700 text-gray-400'}`}
              >
                Images ({definitions.length})
              </button>
              <button
                onClick={() => setImageFilter('detected')}
                className={`flex-1 px-2 py-1 rounded border text-[11px] ${imageFilter === 'detected' ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-gray-900 border-gray-700 text-gray-400'}`}
              >
                Detected ({detectedDefs.length})
              </button>
            </div>
            <div className="card divide-y divide-gray-800 overflow-y-auto lg:max-h-[calc(100vh-260px)]">
              {filteredImages.map((definition) => (
                <button
                  key={definition.id}
                  onClick={() => loadVersions(definition.id)}
                  className={`w-full text-left px-3 py-2.5 hover:bg-gray-800/60 ${selectedId === definition.id ? 'bg-indigo-500/10 border-l-2 border-indigo-500' : ''}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm text-white truncate">{definition.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${definition.latest ? statusColor(definition.latest.status) : 'text-gray-500 border-gray-700'}`}>
                      {definition.latest?.status || '—'}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-x-2 text-[10px] text-gray-500 mt-0.5">
                    <span className="font-mono">{definition.id}</span>
                    <span>{definition.runtime_family}</span>
                    {definition.latest && <span>v{definition.latest.version}</span>}
                    {definition.latest && <span>{definition.latest.capabilities?.length || 0} caps</span>}
                  </div>
                </button>
              ))}
              {filteredImages.length === 0 && (
                <div className="p-4 text-xs text-gray-500">
                  {definitions.length === 0 && detectedDefs.length === 0 ? (
                    <>
                      <div>No images yet.</div>
                      <button onClick={seedImages} disabled={busy} className="btn-secondary mt-2 text-[10px]">
                        Seed predefined images
                      </button>
                      <div className="mt-1 text-[10px] leading-snug text-gray-600">
                        Imports full bundles from <code>agent-images/</code>; no image builds.
                      </div>
                    </>
                  ) : (
                    'No images match this filter.'
                  )}
                </div>
              )}
            </div>
          </aside>

          <main className="space-y-4 min-w-0">
            {!selected && <div className="card p-6 text-sm text-gray-500">Select an image to inspect its versions and files.</div>}
            {selected && (
              <>
                <div className="card p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-lg font-semibold text-white">
                        {selected.name}
                        {fileVersion && <span className="ml-2 text-sm text-gray-400 font-mono">v{fileVersion.version}</span>}
                      </div>
                      <div className="text-xs text-gray-500 font-mono mt-0.5">
                        {selected.id} · {selected.runtime_family}{selected.adapter_type ? ` · ${selected.adapter_type}` : ''}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        entrypoint: <span className="font-mono">{selected.entrypoint || '—'}</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button onClick={() => fileVersion && openEditor(fileVersion)} disabled={!fileVersion?.source_available || busy} className="btn-primary text-xs">
                        New version
                      </button>
                      <button onClick={() => fileVersion && buildVersion(fileVersion.id)} disabled={!canBuild || busy} className="btn-secondary text-xs">
                        {fileVersion?.status === 'ready' ? 'Rebuild' : 'Build'}
                      </button>
                      <button onClick={() => fileVersion && promote(fileVersion.id, 'stable')} disabled={!fileVersion || fileVersion.status !== 'ready' || isStable} className="btn-secondary text-xs">
                        Promote stable
                      </button>
                      <details className="relative">
                        <summary className="btn-secondary text-xs cursor-pointer list-none">⋯</summary>
                        <div className="absolute right-0 z-30 mt-1 w-52 rounded-lg border border-gray-700 bg-[#12121a] p-1 shadow-xl">
                          {fileVersion?.source_available && (
                            <a href={`${API}/api/image-catalog/versions/${fileVersion.id}/source`} className="block rounded px-2 py-1.5 text-xs text-gray-300 hover:bg-gray-800">
                              Download source
                            </a>
                          )}
                          {fileVersion?.image_tag && !hasCapabilities && (
                            <button onClick={() => scanCapabilities(fileVersion.id)} className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-800">
                              Scan capabilities
                            </button>
                          )}
                          <button onClick={() => toggleEnabled(selected)} className="block w-full rounded px-2 py-1.5 text-left text-xs text-gray-300 hover:bg-gray-800">
                            {selected.enabled ? 'Disable' : 'Enable'}
                          </button>
                          <button onClick={() => deleteDefinition(selected)} className="block w-full rounded px-2 py-1.5 text-left text-xs text-red-400 hover:bg-gray-800">
                            Delete image
                          </button>
                        </div>
                      </details>
                    </div>
                  </div>

                  <div className="mt-3">
                    <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Version</div>
                    {versions.length <= 8 ? (
                      <div className="flex flex-wrap gap-1">
                        {versions.map((version) => (
                          <button
                            key={version.id}
                            onClick={() => showFiles(version)}
                            className={`px-2 py-1 rounded border text-[11px] font-mono ${fileVersion?.id === version.id ? 'bg-indigo-600 border-indigo-500 text-white' : 'bg-gray-900 border-gray-700 text-gray-400 hover:text-gray-200'}`}
                          >
                            v{version.version}{version.aliases?.stable === version.id ? ' · stable' : ''}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <select
                        value={fileVersion?.id || ''}
                        onChange={(event) => {
                          const version = versions.find((candidate) => candidate.id === event.target.value)
                          if (version) showFiles(version)
                        }}
                        className="input-field text-xs font-mono"
                      >
                        {versions.map((version) => (
                          <option key={version.id} value={version.id}>
                            v{version.version} · {version.status}{version.aliases?.stable === version.id ? ' · stable' : ''}
                          </option>
                        ))}
                      </select>
                    )}
                    {selected.version_count > versions.length && (
                      <div className="text-[10px] text-gray-600 mt-1">Showing latest {versions.length} of {selected.version_count} versions.</div>
                    )}
                  </div>

                  {fileVersion && (
                    <div className="mt-3">
                      <div className="text-sm text-gray-200">{fileVersion.commit_message || '(no commit message)'}</div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-gray-500">
                        <span className={statusColor(fileVersion.status).split(' ')[0]}>{fileVersion.status}</span>
                        {fileVersion.latest_build && (
                          <span>
                            {fileVersion.latest_build.trigger_type} · {fileVersion.latest_build.status}
                            {fileVersion.latest_build.approved_by ? ` · approved by ${fileVersion.latest_build.approved_by}` : ''}
                            {fileVersion.latest_build.capability_request_id ? ` · request #${fileVersion.latest_build.capability_request_id}` : ''}
                            {fileVersion.latest_build.task_id ? ` · ${fileVersion.latest_build.task_id}` : ''}
                          </span>
                        )}
                        <span>git {shortHash(fileVersion.git_commit)}</span>
                        <span>{fileVersion.capabilities?.length || 0} capabilities</span>
                        <span>parent {shortHash(fileVersion.parent_version_id)}</span>
                        {isStable && <span className="text-emerald-400">stable</span>}
                      </div>
                    </div>
                  )}
                </div>

                <div className="card">
                  <div className="flex flex-wrap items-center gap-1 border-b border-gray-800 px-2 pt-1">
                    <button onClick={() => setContentTab('files')} className={`px-3 py-1.5 text-xs border-b-2 ${contentTab === 'files' ? 'border-indigo-500 text-white' : 'border-transparent text-gray-500 hover:text-gray-300'}`}>Files</button>
                    <button onClick={() => { setContentTab('compare'); if (fileVersion) openDiff(fileVersion) }} className={`px-3 py-1.5 text-xs border-b-2 ${contentTab === 'compare' ? 'border-indigo-500 text-white' : 'border-transparent text-gray-500 hover:text-gray-300'}`}>Compare</button>
                    <button onClick={() => { setContentTab('logs'); if (fileVersion?.latest_build) openBuildLogs(fileVersion.latest_build.id) }} className={`px-3 py-1.5 text-xs border-b-2 ${contentTab === 'logs' ? 'border-indigo-500 text-white' : 'border-transparent text-gray-500 hover:text-gray-300'}`}>Build logs</button>
                    <button onClick={refreshSelectedVersion} className="ml-auto px-2 py-1 text-[10px] text-gray-500 hover:text-gray-300">Refresh</button>
                  </div>
                  <div className="p-4">
                    {editorOpen ? (
                      <div>
                        <div className="flex items-center justify-between mb-2">
                          <div className="text-xs text-gray-400">
                            New version from <span className="font-mono">v{editorBase?.version}</span>
                          </div>
                          <button onClick={() => setEditorOpen(false)} className="text-xs text-gray-500 hover:text-gray-300">Cancel</button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-[220px_minmax(0,1fr)] gap-2">
                          <div className="max-h-[45vh] overflow-y-auto border border-gray-800 rounded p-1">
                            {editorTree.map((entry) => (
                              <button
                                key={entry.path}
                                onClick={() => setEditorActive(entry.path)}
                                className={`block w-full text-left text-[10px] font-mono px-1 py-0.5 rounded truncate ${
                                  editorActive === entry.path ? 'bg-indigo-900/40 text-indigo-200' : editorDeleted.includes(entry.path) ? 'text-red-400 line-through' : 'text-gray-400 hover:bg-gray-800'
                                }`}
                                title={entry.path}
                              >
                                {entry.path}
                              </button>
                            ))}
                          </div>
                          <div>
                            {editorActive && editorFiles[editorActive] !== undefined ? (
                              <textarea
                                value={editorFiles[editorActive]}
                                onChange={(event) => setEditorFiles((prev) => ({ ...prev, [editorActive]: event.target.value }))}
                                rows={16}
                                className="input-field w-full text-xs font-mono"
                              />
                            ) : (
                              <div className="text-xs text-gray-500 p-3 border border-gray-800 rounded">
                                Select a text file to edit. Binary/large files are preserved from the base version.
                              </div>
                            )}
                            <div className="flex flex-wrap items-center gap-2 mt-2">
                              <input
                                type="file"
                                onChange={(event) => {
                                  const file = event.target.files?.[0]
                                  if (file) addEditorUpload(file)
                                  event.target.value = ''
                                }}
                                className="text-xs text-gray-400"
                              />
                              {editorActive && (
                                <button
                                  onClick={() => {
                                    setEditorDeleted((prev) => (prev.includes(editorActive) ? prev : [...prev, editorActive]))
                                    setEditorFiles((prev) => {
                                      const copy = { ...prev }
                                      delete copy[editorActive]
                                      return copy
                                    })
                                    setEditorActive(null)
                                  }}
                                  className="btn-secondary text-[10px] px-2 py-0.5"
                                >
                                  Delete file
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-3">
                          <input value={editorVersion} onChange={(event) => setEditorVersion(event.target.value)} placeholder="version (auto if empty)" className="input-field text-xs" />
                          <input value={editorMessage} onChange={(event) => setEditorMessage(event.target.value)} placeholder="commit message" className="input-field text-xs md:col-span-2" />
                        </div>
                        <div className="flex flex-wrap items-center gap-2 mt-2">
                          <button onClick={saveEditor} disabled={editorSaving} className="btn-primary text-xs">
                            {editorSaving ? 'Committing…' : 'Create draft'}
                          </button>
                          <span className="text-[10px] text-gray-500">Creates a draft; use Build in the header to build it.</span>
                        </div>
                      </div>
                    ) : contentTab === 'compare' ? (
                      <div>
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                          <select value={compareTo} onChange={(event) => setCompareTo(event.target.value)} className="input-field text-xs max-w-xs">
                            <option value="">Select version</option>
                            {versions.filter((version) => version.id !== fileVersion?.id).map((version) => (
                              <option key={version.id} value={version.id}>v{version.version} · {version.status}</option>
                            ))}
                          </select>
                          <button onClick={() => fileVersion && openDiff(fileVersion, compareTo)} className="btn-primary text-xs">Compare</button>
                        </div>
                        <pre className="max-h-[55vh] overflow-auto text-[10px] text-gray-300 bg-[#0d0d14] border border-[#1a1a2a] rounded p-2">{diffText || 'Choose a version to compare.'}</pre>
                      </div>
                    ) : contentTab === 'logs' ? (
                      <div>
                        {!fileVersion?.latest_build && <div className="text-sm text-gray-500">No build recorded for this version. Use Build to create one.</div>}
                        {fileVersion?.latest_build && (
                          <>
                            <div className="flex flex-wrap items-center gap-2 mb-2 text-xs text-gray-400">
                              <span>#{fileVersion.latest_build.id} · {fileVersion.latest_build.status} · {fileVersion.latest_build.trigger_type}</span>
                              <button onClick={() => openBuildLogs(fileVersion.latest_build!.id)} className="text-cyan-400 hover:text-cyan-300">Load logs</button>
                            </div>
                            {buildLogs?.error && <div className="text-xs text-red-400 mb-2">{buildLogs.error}</div>}
                            <pre className="max-h-[55vh] overflow-auto text-[10px] text-gray-300 bg-[#0d0d14] border border-[#1a1a2a] rounded p-2">{buildLogs?.logs || 'No logs loaded.'}</pre>
                          </>
                        )}
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-[240px_minmax(0,1fr)] gap-3">
                        <div className="max-h-[55vh] overflow-y-auto border border-gray-800 rounded p-1">
                          {tree.map((entry) => (
                            <button
                              key={entry.path}
                              onClick={() => openFile(entry.path)}
                              className={`block w-full text-left text-[10px] font-mono px-1 py-0.5 rounded truncate ${selectedFile === entry.path ? 'bg-indigo-900/40 text-indigo-200' : 'text-gray-400 hover:bg-gray-800'}`}
                              title={entry.path}
                            >
                              {entry.path}
                            </button>
                          ))}
                          {tree.length === 0 && <div className="p-2 text-[10px] text-gray-500">No files.</div>}
                        </div>
                        <pre className="max-h-[55vh] overflow-auto text-[10px] text-gray-300 bg-[#0d0d14] border border-[#1a1a2a] rounded p-2">{fileContent || 'Select a file to view its content.'}</pre>
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </main>
        </div>
      ) : activeTab === 'history' ? (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white">History</h2>
            <button onClick={loadHistory} className="btn-secondary text-xs">Refresh</button>
          </div>
          {historyLoading && <div className="text-sm text-gray-500">Loading…</div>}
          <div className="divide-y divide-gray-800">
            {history.map((row) => (
              <button
                key={row.version.id}
                onClick={() => { setActiveTab('all'); loadVersions(row.definition.id, row.version.id) }}
                className="w-full text-left py-2.5 hover:bg-gray-800/40"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-white">{row.definition.name}</span>
                  <span className="font-mono text-gray-500">{row.definition.id}</span>
                  <span className="font-mono text-cyan-300">v{row.version.version}</span>
                  <span className={`px-1.5 py-0.5 rounded border ${statusColor(row.version.status)}`}>{row.version.status}</span>
                  {row.build && <span className="text-gray-400">{row.build.trigger_type} · {row.build.status}</span>}
                  {row.build?.approved_by && <span className="text-gray-500">by {row.build.approved_by}</span>}
                  {row.version.created_at && <span className="text-gray-600">{new Date(row.version.created_at).toLocaleString()}</span>}
                </div>
                <div className="text-xs text-gray-400 mt-0.5">{row.version.commit_message || '(no commit message)'}</div>
                <div className="flex flex-wrap gap-2 text-[10px] text-gray-500 mt-0.5">
                  {row.capability_delta?.added?.length > 0 && <span className="text-emerald-400">+{row.capability_delta.added.length} caps</span>}
                  {row.capability_delta?.removed?.length > 0 && <span className="text-red-400">-{row.capability_delta.removed.length} caps</span>}
                  {row.build?.task_id && <span>{row.build.task_id}</span>}
                </div>
              </button>
            ))}
            {!historyLoading && history.length === 0 && <div className="text-sm text-gray-500 py-3">No history yet.</div>}
          </div>
        </div>
      ) : (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white">Builds</h2>
            <button onClick={loadBuilds} className="btn-secondary text-xs">Refresh</button>
          </div>
          {buildsLoading && <div className="text-sm text-gray-500">Loading…</div>}
          <div className="divide-y divide-gray-800">
            {builds.map((build) => (
              <div key={build.id} className="py-2.5">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono text-white">#{build.id}</span>
                  <span className={`px-1.5 py-0.5 rounded border ${statusColor(build.status)}`}>{build.status}</span>
                  <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">{build.trigger_type}</span>
                  <span className="text-cyan-400 font-mono">{build.definition_id}</span>
                  {build.version_id && <span className="font-mono text-gray-500">{build.version_id}</span>}
                  {build.capability_request_id && <span className="text-gray-500">request #{build.capability_request_id}</span>}
                  {build.task_id && <span className="text-gray-500">{build.task_id}</span>}
                  {build.approved_by && <span className="text-gray-500">by {build.approved_by}</span>}
                  {build.created_at && <span className="text-gray-600">{new Date(build.created_at).toLocaleString()}</span>}
                  <button onClick={() => { setActiveTab('all'); loadVersions(build.definition_id, build.version_id || undefined) }} className="btn-secondary text-[10px] px-2 py-0.5">Open</button>
                  <button onClick={() => openBuildLogs(build.id)} className="btn-secondary text-[10px] px-2 py-0.5">Logs</button>
                </div>
                {build.image_tag && <div className="text-[10px] font-mono text-gray-600 mt-0.5">{build.image_tag}</div>}
                {build.error && <div className="text-[10px] text-red-400 mt-0.5">{build.error}</div>}
              </div>
            ))}
            {!buildsLoading && builds.length === 0 && <div className="text-sm text-gray-500 py-3">No builds yet.</div>}
          </div>
          {buildLogs && (
            <div className="mt-3 border-t border-gray-800 pt-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-gray-400">Build #{buildLogs.id} · {buildLogs.status}</span>
                <button onClick={() => setBuildLogs(null)} className="text-xs text-gray-500 hover:text-gray-300">Close</button>
              </div>
              {buildLogs.error && <div className="text-xs text-red-400">{buildLogs.error}</div>}
              <pre className="max-h-64 overflow-auto text-[10px] text-gray-300 bg-[#0d0d14] border border-[#1a1a2a] rounded p-2">{buildLogs.logs || 'No logs.'}</pre>
            </div>
          )}
        </div>
      )}

      {capOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-8" onClick={() => setCapOpen(false)}>
          <div className="w-full max-w-2xl rounded-lg border border-gray-700 bg-[#12121a] p-5" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold text-white">Search capabilities</div>
              <button onClick={() => setCapOpen(false)} className="text-xs text-gray-500 hover:text-gray-300">Close</button>
            </div>
            <div className="flex gap-2 mb-3">
              <input
                value={capQuery}
                onChange={(event) => setCapQuery(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && searchCapabilities()}
                placeholder="e.g. pandas, chromium, pdf"
                className="input-field flex-1 text-sm"
              />
              <button onClick={searchCapabilities} disabled={capLoading} className="btn-primary text-sm">
                {capLoading ? 'Searching…' : 'Search'}
              </button>
            </div>
            <div className="max-h-[50vh] overflow-y-auto space-y-1">
              {capResults.map((hit) => (
                <button
                  key={`${hit.version_id}-${hit.manager}-${hit.name}`}
                  onClick={() => { setCapOpen(false); setActiveTab('all'); loadVersions(hit.definition_id, hit.version_id) }}
                  className="flex w-full flex-wrap items-center gap-2 rounded border border-gray-800 px-2 py-1.5 text-left text-xs hover:bg-gray-800/50"
                >
                  <span className="font-mono text-indigo-300">{hit.manager}:{hit.name}{hit.version ? `=${hit.version}` : ''}</span>
                  <span className="text-gray-500">in</span>
                  <span className="text-cyan-400">{hit.definition_id}</span>
                  <span className="text-gray-500">v{hit.image_version}</span>
                  <span className={`px-1.5 py-0.5 rounded border ${statusColor(hit.status)}`}>{hit.status}</span>
                </button>
              ))}
              {!capLoading && capResults.length === 0 && <div className="text-sm text-gray-500">No results.</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
