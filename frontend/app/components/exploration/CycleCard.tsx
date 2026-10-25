'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import { DEPLOYMENT_STATUS_LABEL, type DeployableApp } from '../../lib/explorations'

interface CycleCardProps {
  meta: any
  expanded: boolean
  onToggle: () => void
  versionMap?: Map<string, number>
  fileSizes?: Map<string, number>
  onPreview: (dagId: string, path: string) => void
  onRetry: (dagId: string, nodeId?: string) => void
  deployable?: DeployableApp | null
  onDeploy?: (candidate: DeployableApp) => void
}

function basename(path: string) {
  return path.split('/').pop() || path
}

function fileIcon(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  if (ext === 'py') return '🐍'
  if (ext === 'json') return '{}'
  if (ext === 'pdf') return '📕'
  if (['png', 'jpg', 'jpeg', 'svg', 'webp'].includes(ext)) return '🖼'
  if (ext === 'md') return '📄'
  return '📄'
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

function formatDuration(seconds: number | null | undefined) {
  if (!seconds) return '—'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (minutes < 60) return `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function formatBytes(value: number | undefined) {
  if (value === undefined) return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function statusInfo(meta: any) {
  const status = String(meta.status || 'unknown')
  if (status === 'failed') return { label: 'Failed', className: 'text-red-300' }
  if (status === 'cancelled') return { label: 'Cancelled', className: 'text-gray-400' }
  if (status === 'completed') return meta.flagged ? { label: 'Needs review', className: 'text-amber-300' } : { label: 'Completed', className: 'text-emerald-300' }
  return { label: status, className: 'text-gray-400' }
}

export default function CycleCard({
  meta,
  expanded,
  onToggle,
  versionMap,
  fileSizes,
  onPreview,
  onRetry,
  deployable,
  onDeploy,
}: CycleCardProps) {
  const all: string[] = Array.isArray(meta.deliverables) ? meta.deliverables : []
  const info = statusInfo(meta)
  const keyFiles = useMemo(() => {
    const sorted = [...all].sort((a, b) => priority(a) - priority(b))
    return sorted.filter((path) => priority(path) < 20).slice(0, 3)
  }, [all])
  const displayKeys = keyFiles.length > 0 ? keyFiles : all.slice(0, 3)
  const supporting = all.filter((path) => !displayKeys.includes(path))
  const cancelled = meta.status === 'cancelled'
  const failed = meta.status === 'failed'

  const fileLine = (path: string, includeSize = false) => {
    const version = versionMap?.get(basename(path))
    const size = includeSize ? (fileSizes?.get(path) ?? fileSizes?.get(basename(path))) : undefined
    return `${basename(path)}${version && version > 1 ? ` (v${version})` : ''}${size ? ` · ${formatBytes(size)}` : ''}`
  }

  return (
    <div className="my-2 border-t border-gray-800/80 pt-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px]">
        <span className="font-semibold text-gray-300">
          ⚡ CYCLE {meta.cycle_number} • {String(meta.status || '').toUpperCase()}
          {meta.duration_seconds ? ` (${formatDuration(meta.duration_seconds)})` : ''}
        </span>
        <span className={info.className}>{info.label}</span>
        {meta.score !== null && meta.score !== undefined && (
          <span className={`rounded border px-1.5 py-0.5 ${meta.flagged ? 'border-amber-500/40 text-amber-300' : 'border-emerald-500/40 text-emerald-300'}`}>
            {meta.score}/100
          </span>
        )}
        {meta.issue_count > 0 && (
          <Link href={`/dags/${meta.dag_id}?tab=audit`} className="text-amber-300 hover:text-amber-200">
            ⚠ Audit ({meta.issue_count})
          </Link>
        )}
        <span className="text-gray-600">{all.length} files</span>
        {deployable && (() => {
          const existing = deployable.existing_deployment
          if (!existing) {
            return (
              <button onClick={() => onDeploy?.(deployable)} className="ml-auto text-emerald-300 hover:text-emerald-200">
                🚀 Deploy
              </button>
            )
          }
          const active = ['pending_approval', 'approved', 'building'].includes(existing.status)
          const statusLabel = DEPLOYMENT_STATUS_LABEL[existing.status] || existing.status
          if (active) {
            return (
              <Link href="/deployments" className="ml-auto text-cyan-300 hover:text-cyan-200">
                🚀 {statusLabel}
              </Link>
            )
          }
          return (
            <>
              {existing.status === 'running' && existing.url ? (
                <a href={existing.url} target="_blank" rel="noreferrer" className="ml-auto text-emerald-300 hover:text-emerald-200">
                  🚀 Open app
                </a>
              ) : (
                <Link href="/deployments" className={`ml-auto ${existing.status === 'failed' ? 'text-amber-300' : 'text-cyan-300'} hover:opacity-80`}>
                  {existing.status === 'failed' ? '⚠ ' : '🚀 '}{statusLabel}
                </Link>
              )}
              <button onClick={() => onDeploy?.(deployable)} className="text-gray-300 hover:text-white">
                ↻ Redeploy
              </button>
            </>
          )
        })()}
        <Link href={`/dags/${meta.dag_id}`} className={`${deployable ? '' : 'ml-auto'} text-gray-400 hover:text-gray-200`}>View DAG</Link>
        <button onClick={onToggle} className="text-gray-500 hover:text-gray-300">
          {expanded ? 'Collapse' : 'Expand'}
        </button>
      </div>

      {expanded && (
        <div className="mt-2 border-t border-gray-800/60 pt-2">
          {cancelled ? (
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
              <span>User stopped exploration.</span>
              {meta.key_output && (
                <button
                  onClick={() => onPreview(meta.dag_id, meta.key_output)}
                  className="rounded border border-gray-700 px-2 py-0.5 font-mono text-[10px] text-gray-300 hover:border-gray-500"
                >
                  {fileIcon(meta.key_output)} {fileLine(meta.key_output, true)}
                </button>
              )}
              <button
                onClick={() => onRetry(meta.dag_id, meta.node_id)}
                className="rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-300 hover:border-gray-500"
              >
                ▷ Retry
              </button>
            </div>
          ) : (
            <>
              <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-600">Outputs</div>
              <div className="flex flex-wrap gap-1.5">
                {displayKeys.map((path) => (
                  <button
                    key={path}
                    onClick={() => onPreview(meta.dag_id, path)}
                    className="rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-300 hover:border-gray-500"
                    title={path}
                  >
                    {fileIcon(path)} {fileLine(path)}
                  </button>
                ))}
                {supporting.length > 0 && (
                  <details className="text-[10px]">
                    <summary className="cursor-pointer rounded border border-gray-800 px-2 py-0.5 text-cyan-400">
                      +{supporting.length} supporting files ▾
                    </summary>
                    <div className="mt-1 max-h-48 min-w-[260px] overflow-y-auto rounded border border-gray-800 bg-[#0f1015] p-1">
                      {supporting.map((path) => (
                        <button
                          key={path}
                          onClick={() => onPreview(meta.dag_id, path)}
                          className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[10px] text-gray-400 hover:bg-gray-900"
                        >
                          <span className="truncate font-mono">{fileLine(path)}</span>
                          <span className="ml-auto shrink-0 text-[9px] text-gray-600">{typeLabel(path)}</span>
                        </button>
                      ))}
                    </div>
                  </details>
                )}
              </div>
              {failed && (
                <button
                  onClick={() => onRetry(meta.dag_id, meta.node_id)}
                  className="mt-2 text-[10px] text-red-300 hover:text-red-200"
                >
                  ▷ Retry run
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
