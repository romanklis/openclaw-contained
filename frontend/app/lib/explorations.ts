export interface ExplorationSession {
  id: string
  workflow_group_id: string
  title: string
  objective: string
  status: string
  model: string | null
  thinking_budget: string
  quality_budget: string
  project_id: string | null
  current_cycle: number
  created_at: string | null
  updated_at: string | null
  last_activity_at: string | null
  max_cycles: number
  cycle_count?: number
  latest_cycle?: ExplorationCycle | null
}

export interface ExplorationCycle {
  id: string
  cycle_number: number
  dag_id: string
  status: string
  feedback: string | null
  finalized_at: string | null
  created_at: string | null
}

export interface ExplorationMessage {
  id: number
  session_id: string
  cycle_id: string | null
  role: 'user' | 'assistant' | 'system'
  kind: 'text' | 'cycle_proposal' | 'cycle_result' | 'cycle_input' | 'status' | 'error'
  content: string
  metadata: Record<string, any>
  created_at: string | null
  streaming?: boolean
}

export interface ExplorationRequest {
  id: number
  dag_id: string
  node_id: string
  task_id: string | null
  kind: 'decision' | 'input'
  prompt: string
  payload: Record<string, any>
  status: string
  answer: any
  created_at: string | null
}

export interface ExplorationDetail {
  session: ExplorationSession
  cycles: ExplorationCycle[]
  messages: ExplorationMessage[]
  pending_requests: ExplorationRequest[]
  requests: ExplorationRequest[]
  approvals: any[]
}

export const EXPLORATION_STATUS_META: Record<string, { label: string; className: string }> = {
  planning: { label: 'Planning', className: 'text-blue-300 border-blue-500/40 bg-blue-500/10' },
  awaiting_run: { label: 'Awaiting Run', className: 'text-purple-300 border-purple-500/40 bg-purple-500/10' },
  running: { label: 'Running', className: 'text-blue-300 border-blue-500/40 bg-blue-500/10' },
  awaiting_input: { label: 'Needs Input', className: 'text-amber-300 border-amber-500/40 bg-amber-500/10' },
  awaiting_approval: { label: 'Needs Approval', className: 'text-amber-300 border-amber-500/40 bg-amber-500/10' },
  awaiting_feedback: { label: 'Awaiting Feedback', className: 'text-amber-300 border-amber-500/40 bg-amber-500/10' },
  finished: { label: 'Finished', className: 'text-gray-400 border-gray-600 bg-gray-800/60' },
  error: { label: 'Error', className: 'text-red-300 border-red-500/40 bg-red-500/10' },
}

export function explorationStatus(status: string) {
  return EXPLORATION_STATUS_META[status] || {
    label: status.replace(/_/g, ' '),
    className: 'text-gray-400 border-gray-600 bg-gray-800/60',
  }
}

export interface DeployableApp {
  cycle_number: number
  dag_id: string
  task_id: string
  app_path: string
  framework: string
  entrypoint: string
  port: number
  name: string
  source_node?: string | null
  agent_image?: string | null
  existing_deployment?: {
    id: string
    status: string
    url: string | null
    host_port: number | null
    error: string | null
  } | null
}

export const DEPLOYMENT_STATUS_LABEL: Record<string, string> = {
  pending_approval: 'Waiting approval',
  approved: 'Approved',
  building: 'Building',
  built: 'Built',
  running: 'Running',
  stopped: 'Stopped',
  failed: 'Failed',
}

export const THINKING_BUDGET_OPTIONS = [
  { value: 'lean', label: 'Lean', detail: '12 turns/node · 2 iterations · 48 turns/cycle' },
  { value: 'standard', label: 'Standard', detail: '24 turns/node · 5 iterations · 96 turns/cycle' },
  { value: 'deep', label: 'Deep', detail: 'Dynamic turns · 15 iterations · 240 turns/cycle' },
] as const

export const QUALITY_BUDGET_OPTIONS = [
  { value: 'minimal', label: 'Minimal', detail: 'No gate or report artifacts; tests only' },
  { value: 'standard', label: 'Standard', detail: 'Deterministic checks; concise output' },
  { value: 'full', label: 'Full', detail: 'Deep review, retries, and full reports' },
] as const

export function budgetLabel(value: string | null | undefined, options: readonly { value: string; label: string }[]): string {
  return options.find((option) => option.value === value)?.label || 'Standard'
}
