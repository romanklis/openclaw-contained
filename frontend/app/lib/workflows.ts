export type WorkflowStage = 'experimenting' | 'locked' | 'hardened'

export interface WorkflowRun {
  id: string
  status: string
  created_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  run_number: number
  locked: boolean
  archived: boolean
  score: number | null
  flagged: boolean
  key_output: string | null
  blocker: string | null
  total_tokens: number | null
}

export interface WorkflowNode {
  node_id: string
  node_type: string
  label: string
  status: string
}

export interface WorkflowNodeCounts {
  total: number
  completed: number
  failed: number
  flagged: number
}

export interface WorkflowSummary {
  id: string
  title: string
  objective: string
  project_id: string | null
  mode?: 'experiment' | 'template' | 'exploration'
  exploration_session_id?: string | null
  created_at: string | null
  updated_at: string | null
  stage: WorkflowStage
  locked: boolean
  determinism_pct: number
  block_count: number
  step_count: number
  run_count: number
  completed_count: number
  failed_count: number
  running_count: number
  latest_run: WorkflowRun | null
  recent_runs: WorkflowRun[]
  latest_score: number | null
  reliability_pct: number | null
  avg_runtime_seconds: number | null
  latest_nodes: WorkflowNode[]
  latest_node_counts: WorkflowNodeCounts
  blocker: string | null
  key_output: string | null
  latest_active_node_id: string | null
  latest_token_usage?: Record<string, any> | null
  total_token_usage?: Record<string, any> | null
}

export interface WorkflowDetail extends WorkflowSummary {
  runs: WorkflowRun[]
}

export const STAGE_META: Record<
  WorkflowStage,
  { label: string; short: string; icon: string; badge: string; border: string }
> = {
  experimenting: {
    label: 'Active Experiment',
    short: 'Experiment',
    icon: '🧪',
    badge: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    border: 'border-amber-800/40',
  },
  locked: {
    label: 'Locked Template',
    short: 'Template',
    icon: '🔒',
    badge: 'border-purple-500/40 bg-purple-500/10 text-purple-300',
    border: 'border-purple-800/40',
  },
  hardened: {
    label: 'Hardened Workflow',
    short: 'Hardened',
    icon: '⚡',
    badge: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    border: 'border-emerald-800/40',
  },
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return '—'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`
  return String(value)
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  if (minutes < 60) return `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

export function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} mins ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`
  if (seconds < 172800) return 'yesterday'
  return `${Math.floor(seconds / 86400)} days ago`
}

export function runIcon(run: WorkflowRun): string {
  if (run.status === 'running') return '◐'
  if (run.status === 'failed') return '✕'
  if (run.status === 'cancelled') return '⊘'
  if (run.status === 'completed') return run.flagged ? '⚠' : '✓'
  if (run.status === 'ready') return '○'
  return '○'
}

export function runIconClass(run: WorkflowRun): string {
  if (run.status === 'running') return 'text-blue-400'
  if (run.status === 'failed') return 'text-red-400'
  if (run.status === 'cancelled') return 'text-gray-500'
  if (run.status === 'completed') return run.flagged ? 'text-amber-400' : 'text-emerald-400'
  return 'text-gray-500'
}

export function nodeIcon(nodeType: string): string {
  if (nodeType === 'block') return '⚡'
  if (nodeType === 'decision') return '◇'
  if (nodeType === 'input') return '⌨'
  return '🤖'
}

export function workflowNodeCountsLabel(counts: WorkflowNodeCounts): string {
  const parts = [`${counts.total} Node${counts.total === 1 ? '' : 's'}`]
  if (counts.completed) parts.push(`${counts.completed} passed`)
  if (counts.flagged) parts.push(`${counts.flagged} needs review`)
  if (counts.failed) parts.push(`${counts.failed} failed`)
  return parts.join(' • ')
}

const URL_PATTERN = /\b(?:https?:\/\/|www\.)\S+/gi
const TOKEN_PATTERN = /\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9_-]{10,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b/g

export function sanitizeObjectiveText(value: string, maxLen = 4000): string {
  let text = String(value || '').replace(/```[\s\S]*?```/g, ' ')
  const kept: string[] = []
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (!line) continue
    if (line.length > 240 && ((line.match(/;/g) || []).length > 2 || (line.match(/[{}]/g) || []).length > 2)) continue
    if (/^(?:#|import\s|from\s|class\s|def\s|if\s+__name__|print\(|torch\.|np\.|plt\.|for\s+|while\s+|return\s+)/.test(line)) continue
    kept.push(line)
  }
  text = kept.join(' ')
  text = text.replace(URL_PATTERN, ' ')
  text = text.replace(TOKEN_PATTERN, ' ')
  text = text.replace(/[*_`#>]+/g, ' ')
  text = text.replace(/\s+/g, ' ').trim()
  return (text || 'Untitled workflow').slice(0, maxLen)
}

export const MODE_ORDER: Record<string, number> = {
  exploration: 0,
  experiment: 1,
  template: 2,
}

export function modeRank(mode?: string | null): number {
  return MODE_ORDER[mode || 'experiment'] ?? MODE_ORDER.experiment
}

