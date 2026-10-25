'use client'

import { useMemo, useState } from 'react'

interface FileEntry {
  path: string
  size: number
}

interface FilesDrawerProps {
  files: FileEntry[]
  onClose: () => void
  onPreview: (path: string) => void
}

function filePriority(path: string) {
  const name = (path.split('/').pop() || '').toLowerCase()
  if (name.startsWith('report') || name.startsWith('manuscript') || name.startsWith('qa_report')) return 0
  if (name.startsWith('changes_summary') || name.startsWith('review_package')) return 1
  if (name.startsWith('deliverables') || name.startsWith('manifest') || name.startsWith('verification')) return 2
  return 5
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export default function FilesDrawer({ files, onClose, onPreview }: FilesDrawerProps) {
  const [search, setSearch] = useState('')
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return files
    return files.filter((file) => file.path.toLowerCase().includes(term))
  }, [files, search])
  const groups = useMemo(() => {
    const map = new Map<string, FileEntry[]>()
    for (const file of filtered) {
      const parts = file.path.split('/')
      const group = parts.length > 1 ? parts[0] : 'Root files'
      if (!map.has(group)) map.set(group, [])
      map.get(group)!.push(file)
    }
    for (const entries of Array.from(map.values())) {
      entries.sort((a, b) => filePriority(a.path) - filePriority(b.path) || a.path.localeCompare(b.path))
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [filtered])

  return (
    <div className="fixed inset-0 z-[65] flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-md flex-col border-l border-gray-700 bg-[#0f1015]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-800 px-3 py-2">
          <span className="text-xs font-semibold text-gray-200">Files ({files.length})</span>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200">✕</button>
        </div>
        <div className="border-b border-gray-800 px-3 py-2">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search files…"
            className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[11px] text-gray-300 placeholder:text-gray-600"
          />
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {groups.length === 0 ? (
            <div className="p-4 text-xs text-gray-600">No files.</div>
          ) : groups.map(([group, entries]) => (
            <details key={group} open className="mb-1">
              <summary className="cursor-pointer px-1 py-1 text-[9px] uppercase tracking-wide text-gray-500">
                {group} ({entries.length})
              </summary>
              <div className="divide-y divide-gray-800/60">
                {entries.map((file) => (
                  <button
                    key={file.path}
                    onClick={() => onPreview(file.path)}
                    className="flex w-full items-center gap-2 px-1.5 py-1.5 text-left text-[10px] text-gray-400 hover:bg-gray-900"
                  >
                    <span className="truncate font-mono">{file.path.split('/').pop() || file.path}</span>
                    <span className="ml-auto shrink-0 text-[9px] text-gray-600">{formatBytes(file.size)}</span>
                  </button>
                ))}
              </div>
            </details>
          ))}
        </div>
      </div>
    </div>
  )
}
