export interface ExplorationSession {
  id: string
  workflow_group_id: string
  title: string
  objective: string
  status: string
  model: string | null
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
}

export const EXPLORATION_STATUS_META: Record<string, { label: string; className: string }> = {
  planning: { label: 'Planning', className: 'text-blue-300 border-blue-500/40 bg-blue-500/10' },
  awaiting_run: { label: 'Awaiting Run', className: 'text-purple-300 border-purple-500/40 bg-purple-500/10' },
  running: { label: 'Running', className: 'text-blue-300 border-blue-500/40 bg-blue-500/10' },
  awaiting_input: { label: 'Needs Input', className: 'text-amber-300 border-amber-500/40 bg-amber-500/10' },
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
