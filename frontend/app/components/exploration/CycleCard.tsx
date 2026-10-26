'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import { API } from '../../lib/api'
import { DEPLOYMENT_STATUS_LABEL, type DeployableApp } from '../../lib/explorations'

interface CycleCardProps {
  meta: any
  view: 'collapsed' | 'summary' | 'full'
  onViewChange: (view: 'collapsed' | 'summary' | 'full') => void
  versionMap?: Map<string, number>
  fileSizes?: Map<string, number>
  onPreview: (dagId: string, path: string) => void
  onRetry: (dagId: string, nodeId?: string, status?: string) => void
  onFinish?: () => void
  sessionStatus?: string
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

function verificationInfo(meta: any) {
  const quality = String(meta.quality_budget || 'standard')
  if (quality === 'minimal') return { label: 'Unchecked', className: 'text-gray-500' }
  if (quality === 'full') return { label: 'Full audit', className: 'text-cyan-300' }
  if (meta.flagged) return { label: '⚠ Review (Standard)', className: 'text-amber-300' }
  if (meta.status === 'completed') return { label: '✓ Passed (Standard)', className: 'text-emerald-300' }
  return { label: 'Standard tests', className: 'text-gray-500' }
}

function encodePath(path: string) {
  return path.split('/').map((part) => encodeURIComponent(part)).join('/')
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
  view,
  onViewChange,
  versionMap,
  fileSizes,
  onPreview,
  onRetry,
  onFinish,
  sessionStatus,
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
  const verification = verificationInfo(meta)
  const imagePath = all.find((path) => /\.(png|jpe?g|svg|webp)$/i.test(path)) || null
  const cancelled = meta.status === 'cancelled'
  const failed = meta.status === 'failed'
  const locked = sessionStatus === 'finished'

  const fileLine = (path: string, includeSize = false) => {
    const version = versionMap?.get(basename(path))
    const size = includeSize ? (fileSizes?.get(path) ?? fileSizes?.get(basename(path))) : undefined
    return `${basename(path)}${version && version > 1 ? ` (v${version})` : ''}${size ? ` · ${formatBytes(size)}` : ''}`
  }

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-gray-800 bg-[#0f1015]">
      <div
        className="flex cursor-pointer flex-wrap items-center gap-2 border-b border-gray-800/70 bg-[#14151c] px-3 py-2"
        onClick={() => onViewChange(view === 'collapsed' ? 'summary' : 'collapsed')}
      >
        <span className="rounded border border-cyan-700/40 bg-cyan-500/10 px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wide text-cyan-300">
          Agent · Result
        </span>
        <span className="text-xs font-semibold text-gray-300">
          ⚡ CYCLE {meta.cycle_number} • {String(meta.status || '').toUpperCase()}
          {meta.duration_seconds ? ` (${formatDuration(meta.duration_seconds)})` : ''}
        </span>
        {view === 'collapsed' && (
          <span className="text-[9px] text-gray-600">
            {meta.turn_count ?? 0} turns • {all.length} files
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2 text-[10px]" onClick={(event) => event.stopPropagation()}>
          {deployable && (() => {
            const existing = deployable.existing_deployment
            if (!existing) {
              return (
                <button onClick={() => onDeploy?.(deployable)} className="text-emerald-300 hover:text-emerald-200">
                  🚀 Deploy
                </button>
              )
            }
            const active = ['pending_approval', 'approved', 'building'].includes(existing.status)
            const statusLabel = DEPLOYMENT_STATUS_LABEL[existing.status] || existing.status
            if (active) {
              return (
                <Link href="/deployments" className="text-cyan-300 hover:text-cyan-200">
                  🚀 {statusLabel}
                </Link>
              )
            }
            return (
              <>
                {existing.status === 'running' && existing.url ? (
                  <a href={existing.url} target="_blank" rel="noreferrer" className="text-emerald-300 hover:text-emerald-200">
                    🚀 Open app
                  </a>
                ) : (
                  <Link href="/deployments" className={`${existing.status === 'failed' ? 'text-amber-300' : 'text-cyan-300'} hover:opacity-80`}>
                    {existing.status === 'failed' ? '⚠ ' : '🚀 '}{statusLabel}
                  </Link>
                )}
                <button onClick={() => onDeploy?.(deployable)} className="text-gray-300 hover:text-white">
                  ↻ Redeploy
                </button>
              </>
            )
          })()}
          {all.length > 0 && (
            <a href={`${API}/api/dags/${meta.dag_id}/deliverables.zip`} className="text-cyan-300 hover:text-cyan-200" title="Download all deliverables">
              ⭳ ZIP
            </a>
          )}
          <Link href={`/dags/${meta.dag_id}`} className="text-gray-400 hover:text-gray-200">👁 View DAG</Link>
          <button
            onClick={() => onViewChange(view === 'collapsed' ? 'summary' : view === 'summary' ? 'full' : 'summary')}
            className="text-gray-400 hover:text-gray-200"
          >
            {view === 'collapsed' ? 'Expand ▾' : view === 'summary' ? 'Expand details ▾' : 'Collapse details ▴'}
          </button>
          {view !== 'collapsed' && (
            <button onClick={() => onViewChange('collapsed')} className="text-gray-500 hover:text-gray-300" title="Minimize result">
              − Minimize
            </button>
          )}
        </div>
      </div>

      {view !== 'collapsed' && (<>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/50 px-3 py-1.5 text-[10px] text-gray-500">
        <span>{meta.turn_count ?? 0} Turns • {all.length} Files Generated</span>
        {meta.token_usage?.total ? (
          <span className="rounded border border-cyan-800/40 px-1.5 py-0.5 text-cyan-300">
            🪙 {Number(meta.token_usage.total).toLocaleString()} tokens
            <span className="ml-1 text-gray-600">
              ({Number(meta.token_usage.input || 0).toLocaleString()} in / {Number(meta.token_usage.output || 0).toLocaleString()} out)
            </span>
            {meta.token_usage.cost_usd !== null && meta.token_usage.cost_usd !== undefined && (
              <span className="ml-1 text-emerald-300">· ${Number(meta.token_usage.cost_usd).toFixed(4)}</span>
            )}
          </span>
        ) : null}
        <span className={`rounded border px-1.5 py-0.5 ${verification.className}`}>Tests: {verification.label}</span>
        {meta.score !== null && meta.score !== undefined ? (
          <span className={`rounded border px-1.5 py-0.5 ${meta.flagged ? 'border-amber-500/40 text-amber-300' : 'border-emerald-500/40 text-emerald-300'}`}>
            Audit Score: [{meta.score}/100]
          </span>
        ) : (
          <span className="rounded border border-gray-700 px-1.5 py-0.5 text-gray-500">Audit Score: —</span>
        )}
        {meta.issue_count > 0 && (
          <Link href={`/dags/${meta.dag_id}?tab=audit`} className="text-amber-300 hover:text-amber-200">
            Audit: {meta.issue_count} Issue{meta.issue_count === 1 ? '' : 's'}
          </Link>
        )}
        <span className={info.className}>Status: {info.label}</span>
      </div>

      <div className="px-3 py-2">
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
            {!locked && (
              <button
                onClick={() => onRetry(meta.dag_id, meta.node_id, meta.status)}
                className="rounded border border-gray-700 px-2 py-0.5 text-[10px] text-gray-300 hover:border-gray-500"
              >
                ▷ Retry
              </button>
            )}
            {locked ? (
              <span className="rounded border border-emerald-700/40 px-2 py-0.5 text-[10px] text-emerald-300">🔒 Locked</span>
            ) : onFinish ? (
              <button onClick={onFinish} className="rounded border border-emerald-700/50 px-2 py-0.5 text-[10px] text-emerald-300 hover:border-emerald-500">
                🔒 Lock as Done
              </button>
            ) : null}
          </div>
        ) : (
          <>
            <div className="text-[9px] uppercase tracking-wide text-gray-600">Primary deliverables:</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
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
                <button onClick={() => onViewChange(view === 'full' ? 'summary' : 'full')} className="rounded border border-gray-800 px-2 py-0.5 text-[10px] text-cyan-400 hover:border-gray-600">
                  +{supporting.length} supporting files {view === 'full' ? '▴' : '▾'}
                </button>
              )}
              {all.length === 0 && <span className="text-[10px] text-gray-600">No deliverables captured.</span>}
            </div>
            {failed && (
              <button onClick={() => onRetry(meta.dag_id, meta.node_id, meta.status)} className="mt-2 text-[10px] text-red-300 hover:text-red-200">
                ▷ Retry run
              </button>
            )}
            {imagePath && (
              <div className="mt-2">
                <div className="text-[9px] uppercase tracking-wide text-gray-600">Quick Preview:</div>
                <button
                  onClick={() => onPreview(meta.dag_id, imagePath)}
                  className="mt-1 w-full overflow-hidden rounded border border-gray-800 bg-black/20 text-left hover:border-gray-600"
                  title={imagePath}
                >
                  <div className="flex flex-wrap items-center gap-2 border-b border-gray-800/70 px-2 py-1 text-[10px] text-gray-400">
                    <span className="font-mono text-gray-300">{basename(imagePath)}</span>
                    <span className="text-gray-600">Generated preview</span>
                    <span className="ml-auto text-cyan-300">⤢ Click to enlarge</span>
                  </div>
                  <img
                    src={`${API}/api/dags/${meta.dag_id}/workspace/files/${encodePath(imagePath)}?inline=1`}
                    alt={`Preview of ${basename(imagePath)}`}
                    className="max-h-44 w-full bg-black/30 object-contain"
                  />
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {view === 'full' && !cancelled && (
        <div className="border-t border-gray-800/60 bg-[#0b0c10] px-3 py-2">
          <div className="text-[9px] uppercase tracking-wide text-gray-600">Deliverables</div>
          {all.length === 0 ? (
            <div className="mt-1 text-[10px] text-gray-600">No deliverables captured.</div>
          ) : (
            <div className="mt-1 grid max-h-52 gap-1 overflow-y-auto sm:grid-cols-2">
              {all.map((path) => (
                <button
                  key={path}
                  onClick={() => onPreview(meta.dag_id, path)}
                  className="flex min-w-0 items-center gap-2 rounded px-1.5 py-1 text-left text-[10px] text-gray-400 hover:bg-gray-900"
                  title={path}
                >
                  <span className="shrink-0">{fileIcon(path)}</span>
                  <span className="truncate font-mono">{fileLine(path)}</span>
                  <span className="ml-auto shrink-0 text-[9px] text-gray-600">{typeLabel(path)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      </>)}
    </div>
  )
}
