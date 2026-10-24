'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'

interface CycleResultCardProps {
  meta: any
  onPreview: (dagId: string, path: string) => void
  onRetry: (dagId: string, nodeId?: string) => void
}

function basename(path: string) {
  return path.split('/').pop() || path
}

function typeLabel(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  if (ext === 'md') return 'Markdown'
  if (ext === 'pdf') return 'PDF'
  if (ext === 'docx') return 'DOCX'
  if (['png', 'jpg', 'jpeg', 'svg', 'webp'].includes(ext)) return 'Image'
  if (ext === 'json') return 'JSON'
  if (ext === 'csv' || ext === 'tsv') return 'Data'
  if (['py', 'js', 'ts', 'sh'].includes(ext)) return 'Code'
  return ext ? ext.toUpperCase() : 'File'
}

function priority(path: string): number {
  const name = basename(path).toLowerCase()
  if (/^manuscript_final\.(md|pdf|docx)$/.test(name)) return 0
  if (/^(report|final_report)\.(md|pdf)$/.test(name)) return 1
  if (/^qa_report\.(md|pdf|docx)$/.test(name)) return 2
  if (/^changes_summary\.(md|pdf|docx)$/.test(name)) return 3
  if (/^review_package\.pdf$/.test(name)) return 4
  if (/^(deliverables|manifest)\.(md|json)$/.test(name)) return 5
  if (/^(verification_report|references_final)\.(json|md|bib)$/.test(name)) return 6
  if (/^(figures?|images?)\//.test(path) || /\.(png|jpg|jpeg|svg|webp)$/.test(name)) return 7
  return 20
}

function isKey(path: string) {
  return priority(path) < 20
}

export default function CycleResultCard({ meta, onPreview, onRetry }: CycleResultCardProps) {
  const [showAll, setShowAll] = useState(false)
  const all: string[] = Array.isArray(meta.deliverables) ? meta.deliverables : []
  const keyFiles = useMemo(() => {
    const sorted = [...all].sort((a, b) => priority(a) - priority(b))
    return sorted.filter(isKey).slice(0, 6)
  }, [all])
  const displayKeys = keyFiles.length > 0 ? keyFiles : all.slice(0, 6)
  const hidden = all.length - displayKeys.length

  const groups = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const path of all) {
      const parts = path.split('/')
      const group = parts.length > 1 ? parts[0] : 'Root files'
      if (!map.has(group)) map.set(group, [])
      map.get(group)!.push(path)
    }
    return Array.from(map.entries())
  }, [all])

  const failed = meta.status === 'failed' || meta.status === 'cancelled'

  return (
    <div className="my-2 rounded-lg border border-gray-800 bg-[#0f1015]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        <span className="text-xs font-semibold text-gray-200">Cycle {meta.cycle_number} finished</span>
        <span className={`rounded border px-1.5 py-0.5 text-[9px] uppercase ${
          failed ? 'border-red-500/40 bg-red-500/10 text-red-300' :
          meta.flagged ? 'border-amber-500/40 bg-amber-500/10 text-amber-300' :
          'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
        }`}>
          {meta.status}{meta.flagged ? ' • needs review' : ''}
        </span>
        {meta.score !== null && meta.score !== undefined && <span className="text-[10px] text-gray-400">{meta.score}/100</span>}
        {meta.duration_seconds && <span className="text-[10px] text-gray-600">⏱ {meta.duration_seconds}s</span>}
      </div>

      {meta.blocker && (
        <details className="border-t border-gray-800/70 px-3 py-2">
          <summary className="cursor-pointer list-none text-[10px] text-amber-300/90">
            <span className="line-clamp-1 inline">Blocker: {meta.blocker}</span>
            <span className="ml-2 text-gray-600">details</span>
          </summary>
          <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-amber-100/75">{meta.blocker}</p>
        </details>
      )}

      {all.length > 0 && (
        <div className="border-t border-gray-800/70 px-3 py-2">
          <div className="mb-1 flex items-center justify-between text-[9px] uppercase tracking-wide text-gray-600">
            <span>Key deliverables</span>
            <span>{all.length} files</span>
          </div>
          <div className="divide-y divide-gray-800/60">
            {displayKeys.map((path) => (
              <button
                key={path}
                onClick={() => onPreview(meta.dag_id, path)}
                className="flex w-full items-center gap-2 py-1.5 text-left text-[11px] text-gray-300 hover:bg-gray-900/40"
              >
                <span className="shrink-0">📄</span>
                <span className="truncate font-mono">{basename(path)}</span>
                <span className="ml-auto shrink-0 text-[9px] text-gray-600">{typeLabel(path)}</span>
                <span className="shrink-0 text-[10px] text-cyan-400">↗</span>
              </button>
            ))}
          </div>
          {hidden > 0 && (
            <button
              onClick={() => setShowAll((value) => !value)}
              className="mt-1 text-[10px] text-cyan-400 hover:text-cyan-300"
            >
              {showAll ? 'Hide files' : `Show all ${all.length} files`}
            </button>
          )}
          {showAll && (
            <div className="mt-2 max-h-64 overflow-y-auto border-t border-gray-800/60 pt-2">
              {groups.map(([group, files]) => (
                <details key={group} className="mb-1">
                  <summary className="cursor-pointer py-1 text-[10px] uppercase tracking-wide text-gray-500">
                    {group} ({files.length})
                  </summary>
                  <div className="divide-y divide-gray-800/50">
                    {files.map((path) => (
                      <button
                        key={path}
                        onClick={() => onPreview(meta.dag_id, path)}
                        className="flex w-full items-center gap-2 py-1.5 text-left text-[10px] text-gray-400 hover:bg-gray-900/40"
                      >
                        <span className="truncate font-mono">{path}</span>
                        <span className="ml-auto shrink-0 text-[9px] text-gray-600">{typeLabel(path)}</span>
                      </button>
                    ))}
                  </div>
                </details>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-gray-800/70 px-3 py-2 text-[10px]">
        <Link href={`/dags/${meta.dag_id}`} className="text-gray-300 hover:text-white">Open DAG</Link>
        <Link href={`/dags/${meta.dag_id}?tab=audit`} className="text-amber-300/90 hover:text-amber-200">
          Audit ({meta.issue_count ?? 0})
        </Link>
        {failed && (
          <button onClick={() => onRetry(meta.dag_id, meta.node_id)} className="text-red-300 hover:text-red-200">
            Retry run
          </button>
        )}
      </div>
    </div>
  )
}
