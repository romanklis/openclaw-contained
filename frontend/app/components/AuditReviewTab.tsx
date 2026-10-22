'use client'

import { useEffect, useState } from 'react'

type AuditFilter = 'all' | 'high' | 'medium' | 'low' | 'passed'

interface AuditIssue {
  severity?: string
  category?: string
  finding?: string
  evidence?: string
  recommendation?: string
}

interface AuditReview {
  id?: number | string
  verdict?: string
  score?: number
  summary?: string
  model?: string
  created_at?: string
  image_id?: string
  image_tag?: string
  skill_used_name?: string
  issues?: AuditIssue[]
  positives?: string[]
}

interface WorkspaceFile {
  path: string
  size: number
}

interface AuditReviewTabProps {
  review: AuditReview | null
  nodeId: string
  taskId: string | null
  dagId: string
  deepReviewLoading: boolean
  correctSkillLoading: boolean
  correctSkillResult: any
  workspaceFiles: WorkspaceFile[]
  onRunReview: () => void
  onApplyFixes: () => void
  onOpenFile: (path: string) => void
}

const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 }

function normalizeSeverity(value: unknown): 'high' | 'medium' | 'low' | 'other' {
  const severity = String(value || '').toLowerCase()
  if (severity === 'high' || severity === 'medium' || severity === 'low') return severity
  return 'other'
}

function severityBadge(severity: 'high' | 'medium' | 'low' | 'other') {
  if (severity === 'high') return 'border-red-500/40 bg-red-500/10 text-red-300'
  if (severity === 'medium') return 'border-amber-500/40 bg-amber-500/10 text-amber-300'
  if (severity === 'low') return 'border-gray-600 bg-gray-800/70 text-gray-300'
  return 'border-gray-700 bg-gray-800/70 text-gray-400'
}

function severityIcon(severity: 'high' | 'medium' | 'low' | 'other') {
  if (severity === 'high') return '[!]'
  if (severity === 'medium') return '[~]'
  return '[•]'
}

function firstSentence(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return ''
  const match = trimmed.match(/^.*?[.!?](?:\s|$)/)
  const sentence = (match?.[0] || trimmed).trim()
  return sentence.length > 110 ? `${sentence.slice(0, 107)}…` : sentence
}

function issueTitle(issue: AuditIssue): string {
  return firstSentence(issue.finding || '') || issue.category || 'Untitled issue'
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function parseEvidence(evidence: string, files: WorkspaceFile[], nodeId: string) {
  const text = String(evidence || '').trim()
  if (!text) return null
  const candidateMatch = text.match(
    /([A-Za-z0-9_./-]+\.(?:md|markdown|py|json|txt|log|csv|tsv|yaml|yml|sh|bash|js|ts|tsx|jsx|pdf|png|jpg|jpeg|gif|svg|tex|m|ipynb|html|htm))/i,
  )
  const candidate = candidateMatch?.[1] || ''
  let resolved: WorkspaceFile | null = null

  if (candidate) {
    const exact = files.find((file) => file.path === candidate)
    if (exact) {
      resolved = exact
    } else {
      const base = candidate.split('/').pop()
      const matches = files.filter((file) => (file.path.split('/').pop() || '') === base)
      resolved =
        matches.find((file) => file.path.startsWith(`${nodeId}/`)) ||
        matches[0] ||
        null
    }
  }

  let line: string | null = null
  const explicitLine = text.match(/\bline\s+(\d+)\b/i)
  if (explicitLine) {
    line = explicitLine[1]
  } else if (candidate) {
    const suffixLine = text.match(new RegExp(`${escapeRegExp(candidate)}\\s*[:#]\\s*(\\d+)`, 'i'))
    if (suffixLine) line = suffixLine[1]
  }

  let quote = text
  if (candidate) {
    quote = quote.replace(new RegExp(`^\\s*${escapeRegExp(candidate)}\\s*:\\s*`, 'i'), '')
  }

  return { candidate, path: resolved?.path || null, line, quote }
}

export default function AuditReviewTab({
  review,
  nodeId,
  taskId,
  dagId,
  deepReviewLoading,
  correctSkillLoading,
  correctSkillResult,
  workspaceFiles,
  onRunReview,
  onApplyFixes,
  onOpenFile,
}: AuditReviewTabProps) {
  const [filter, setFilter] = useState<AuditFilter>('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [passedOpen, setPassedOpen] = useState(true)

  const reviewKey = review?.id ?? 'review'
  const reviewIssues = Array.isArray(review?.issues) ? review.issues : []
  useEffect(() => {
    setFilter('all')
    setExpanded(new Set())
    setPassedOpen(true)
  }, [reviewKey, nodeId])

  if (!review) {
    return (
      <div className="rounded border border-gray-700 bg-gray-900/30 p-4 text-center text-xs text-gray-500">
        No deep review yet.
        {taskId && (
          <button
            onClick={onRunReview}
            disabled={deepReviewLoading}
            className="ml-2 text-amber-300 hover:text-amber-200 disabled:opacity-50"
          >
            {deepReviewLoading ? 'Reviewing…' : 'Run Deep Review →'}
          </button>
        )}
      </div>
    )
  }

  const issues = reviewIssues
  const positives = Array.isArray(review.positives)
    ? review.positives.filter((item) => String(item || '').trim())
    : []
  const orderedIssues = issues
    .map((issue, index) => ({ issue, key: `${reviewKey}-${index}` }))
    .sort((a, b) => {
      const rankA = SEVERITY_ORDER[normalizeSeverity(a.issue.severity)] ?? 3
      const rankB = SEVERITY_ORDER[normalizeSeverity(b.issue.severity)] ?? 3
      return rankA - rankB
    })

  const counts = { high: 0, medium: 0, low: 0, other: 0 }
  for (const issue of issues) {
    counts[normalizeSeverity(issue.severity)] += 1
  }
  const highCount = counts.high
  const visibleIssues =
    filter === 'all'
      ? orderedIssues
      : filter === 'passed'
        ? []
        : orderedIssues.filter(({ issue }) => normalizeSeverity(issue.severity) === filter)

  const score = typeof review.score === 'number' ? review.score : null
  const clean = review.verdict === 'clean'
  const verdictText = String(review.verdict || 'unknown').replace(/_/g, ' ').toUpperCase()
  const scoreClass =
    clean || (score !== null && score >= 80)
      ? 'text-emerald-300'
      : score !== null && score < 50
        ? 'text-red-300'
        : 'text-amber-300'
  const verdictClass =
    clean || (score !== null && score >= 80)
      ? 'text-emerald-300'
      : score !== null && score < 50
        ? 'text-red-300'
        : 'text-amber-300'
  const badge =
    clean
      ? { label: 'PASSED', className: 'border border-emerald-500/40 bg-emerald-500/10 text-emerald-300' }
      : score !== null && score < 50
        ? { label: 'CRITICAL', className: 'border border-red-500/40 bg-red-500/10 text-red-300' }
        : { label: 'NEEDS ATTENTION', className: 'border border-amber-500/40 bg-amber-500/10 text-amber-300' }
  const auditedBy = [review.model, review.image_id ? `(${review.image_id})` : null]
    .filter(Boolean)
    .join(' ')
  const auditedAt = review.created_at ? new Date(review.created_at).toLocaleString() : ''

  const filterOptions: { id: AuditFilter; label: string; count: number }[] = [
    { id: 'all', label: 'All Issues', count: issues.length },
    { id: 'high', label: 'High', count: counts.high },
    { id: 'medium', label: 'Medium', count: counts.medium },
    { id: 'low', label: 'Low', count: counts.low },
    { id: 'passed', label: 'Passed Checks', count: positives.length },
  ]

  const toggleIssue = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const exportReport = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      dag_id: dagId,
      node_id: nodeId,
      task_id: taskId,
      review,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `audit-report-${dagId}-${nodeId}.json`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-2 text-[10px] uppercase tracking-wide text-gray-500">
          Audit Summary &amp; Scorecard
        </div>
        <div className={`rounded border p-3 ${clean ? 'border-emerald-700/40 bg-emerald-950/10' : score !== null && score < 50 ? 'border-red-700/40 bg-red-950/20' : 'border-amber-700/40 bg-amber-950/20'}`}>
          <div className="flex flex-col gap-3 md:flex-row">
            <div className={`flex w-full shrink-0 flex-col items-center justify-center rounded border px-3 py-3 md:w-40 ${badge.className}`}>
              <div className={`text-3xl font-bold ${scoreClass}`}>
                {score ?? '—'}
                <span className="text-xs font-normal text-gray-500"> / 100</span>
              </div>
              <div className={`mt-1 rounded px-2 py-0.5 text-[9px] font-semibold uppercase ${badge.className}`}>
                {badge.label}
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs text-gray-400">
                Overall Verdict:{' '}
                <span className={`font-semibold ${verdictClass}`}>{verdictText}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-gray-300">
                {review.summary || 'No summary provided.'}
              </p>
              <div className="mt-2 text-[10px] text-gray-500">
                Audited by: <span className="text-gray-400">{auditedBy || 'unknown'}</span>
                {auditedAt ? ` • ⏱ ${auditedAt}` : ''}
                {review.skill_used_name ? ` • skill: ${review.skill_used_name}` : ''}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[10px]">
        <span className="uppercase tracking-wide text-gray-500">Filters:</span>
        {filterOptions.map((option) => (
          <button
            key={option.id}
            onClick={() => {
              setFilter(option.id)
              if (option.id === 'passed') setPassedOpen(true)
            }}
            className={`rounded border px-2 py-1 transition-colors ${
              filter === option.id
                ? 'border-blue-500 bg-gray-700 text-white'
                : 'border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-200'
            }`}
          >
            {option.label} ({option.count})
          </button>
        ))}
      </div>

      {filter !== 'passed' && (
        <div className="space-y-3">
          {visibleIssues.length === 0 ? (
            <div className="rounded border border-gray-700 bg-gray-900/30 p-3 text-xs text-gray-500">
              No issues in this filter.
            </div>
          ) : (
            visibleIssues.map(({ issue, key }) => {
              const severity = normalizeSeverity(issue.severity)
              const title = issueTitle(issue)
              const isExpanded = expanded.has(key)
              const evidence = isExpanded && issue.evidence
                ? parseEvidence(issue.evidence, workspaceFiles, nodeId)
                : null
              return (
                <div key={key} className={`rounded border ${severity === 'high' ? 'border-red-800/40' : severity === 'medium' ? 'border-amber-800/40' : 'border-gray-700'} bg-gray-900/30`}>
                  <button
                    onClick={() => toggleIssue(key)}
                    className="flex w-full flex-wrap items-start gap-2 px-3 py-2 text-left"
                  >
                    <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${severityBadge(severity)}`}>
                      {severityIcon(severity)} {severity.toUpperCase()} SEVERITY
                    </span>
                    <span className="text-[9px] uppercase tracking-wide text-gray-500">
                      {String(issue.category || 'uncategorized').replace(/_/g, ' ')}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-gray-200">{title}</span>
                    <span className="shrink-0 text-[10px] text-gray-500">
                      {isExpanded ? 'Collapse ▴' : 'Expand ▾'}
                    </span>
                  </button>

                  {isExpanded && (
                    <div className="space-y-2 border-t border-gray-800 px-3 py-3 text-xs">
                      <div>
                        <span className="text-gray-500">Title: </span>
                        <span className="font-semibold text-gray-100">{title}</span>
                      </div>
                      <div>
                        <span className="text-gray-500">Finding:</span>
                        <p className="mt-1 whitespace-pre-wrap text-gray-300">{issue.finding || '—'}</p>
                      </div>
                      {evidence && (
                        <div className="rounded border border-gray-700 bg-gray-950/60 p-2">
                          <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-500">Evidence</div>
                          <div className="flex flex-wrap items-center gap-2 text-[10px] text-gray-400">
                            <span>
                              File:{' '}
                              <span className="font-mono text-gray-300">
                                {evidence.candidate || '—'}
                              </span>
                            </span>
                            {evidence.line && <span className="text-gray-500">(Line {evidence.line})</span>}
                            {evidence.path && (
                              <button
                                onClick={() => onOpenFile(evidence.path!)}
                                className="ml-auto text-indigo-300 hover:text-indigo-200"
                              >
                                ↗ Open in Deliverables
                              </button>
                            )}
                          </div>
                          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] text-gray-300">
                            {evidence.quote}
                          </pre>
                        </div>
                      )}
                      {issue.recommendation && (
                        <div className="text-amber-200/90">
                          ↳ <span className="font-semibold">Recommended Fix:</span> {issue.recommendation}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}

      {(positives.length > 0 || filter === 'passed') && (
        <div className="rounded border border-emerald-800/40 bg-emerald-950/10">
          <button
            onClick={() => setPassedOpen((value) => !value)}
            className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left text-[10px] uppercase tracking-wide text-emerald-300"
          >
            <span>✓ Passed Validations ({positives.length} checks passed without issues)</span>
            <span className="text-emerald-400/70">{passedOpen ? 'Collapse ▴' : 'Expand ▾'}</span>
          </button>
          {passedOpen && (
            <div className="border-t border-emerald-900/40 px-3 py-2">
              {positives.length === 0 ? (
                <div className="text-xs text-gray-500">No passed checks recorded.</div>
              ) : (
                <ul className="space-y-1 text-xs text-emerald-200/90">
                  {positives.map((item, index) => (
                    <li key={index} className="flex gap-2">
                      <span className="shrink-0 text-emerald-400">✓</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      <div className="border-t border-gray-800 pt-3">
        <div className="mb-2 text-[9px] uppercase tracking-wide text-gray-500">Footer Actions</div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button
            onClick={exportReport}
            className="rounded border border-gray-700 px-3 py-1.5 text-gray-300 hover:border-gray-600 hover:text-white"
          >
            ⭳ Export Audit Report (.json)
          </button>
          {highCount > 0 && (
            <button
              onClick={onApplyFixes}
              disabled={correctSkillLoading || !taskId}
              className="rounded bg-amber-600 px-3 py-1.5 font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
            >
              {correctSkillLoading
                ? '⚡ Generating corrected skill…'
                : `⚡ Apply Suggested Skill Fixes (${highCount})`}
            </button>
          )}
        </div>
      </div>

      {correctSkillResult && (
        <div className="rounded border border-emerald-700/40 bg-emerald-950/20 p-3">
          {correctSkillResult.unchanged ? (
            <div className="text-xs text-emerald-300">No issues found — no skill correction needed.</div>
          ) : (
            <>
              <div className="mb-2 text-[10px] uppercase tracking-wide text-emerald-300">
                ✅ Corrected skill created (child of &quot;{correctSkillResult.skill_name}&quot;)
              </div>
              {correctSkillResult.corrected?.map((skill: any, index: number) => (
                <div key={index} className="mb-3 rounded border border-gray-700 bg-gray-900/60 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs font-semibold text-gray-200">{skill.name}</span>
                    <span className="text-xs text-emerald-300">{skill.status}</span>
                  </div>
                  {skill.description && (
                    <div className="mb-2 text-xs text-gray-400">{skill.description}</div>
                  )}
                  <pre className="mb-2 whitespace-pre-wrap rounded bg-gray-950/60 p-2 text-xs text-gray-300">
                    {skill.instructions}
                  </pre>
                  {skill.addressed_issues?.length > 0 && (
                    <div className="mb-2">
                      <div className="mb-1 text-xs font-semibold text-amber-300">Addresses:</div>
                      <ul className="list-inside list-disc space-y-1 text-xs text-amber-200">
                        {skill.addressed_issues.map((item: string, issueIndex: number) => (
                          <li key={issueIndex}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <a
                    href="/skill-studio"
                    className="mt-2 inline-block rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-500"
                  >
                    Review in Skill Studio
                  </a>
                  <span className="ml-3 text-xs text-gray-500">ID: {skill.skill_id}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
