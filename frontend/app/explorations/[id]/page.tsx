'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  ChatContainer,
  MainContainer,
  MessageInput,
  MessageList,
  TypingIndicator,
} from '@chatscope/chat-ui-kit-react'
import { API, TEMPORAL_UI } from '../../lib/api'
import { useProject } from '../../lib/ProjectContext'
import { RenderedMarkdown } from '../../components/DeliverablePreview'
import DeliverablePreview from '../../components/DeliverablePreview'
import CycleCard from '../../components/exploration/CycleCard'
import CycleProposalCard from '../../components/exploration/CycleProposalCard'
import ChatMessageBubble from '../../components/exploration/ChatMessageBubble'
import FilesDrawer from '../../components/exploration/FilesDrawer'
import ApprovalCard from '../../components/exploration/ApprovalCard'
import DeployDialog from '../../components/exploration/DeployDialog'
import CycleProgressCard from '../../components/exploration/CycleProgressCard'
import {
  QUALITY_BUDGET_OPTIONS,
  THINKING_BUDGET_OPTIONS,
  budgetLabel,
  explorationStatus,
  type DeployableApp,
  type ExplorationDetail,
  type ExplorationMessage,
  type ExplorationRequest,
  type ExplorationSession,
} from '../../lib/explorations'

function encodePath(path: string) {
  return path.split('/').map((part) => encodeURIComponent(part)).join('/')
}

function basename(path: string) {
  return path.split('/').pop() || path
}



export default function ExplorationChatPage() {
  const params = useParams<{ id: string }>()
  const sessionId = params.id
  const { activeProject } = useProject()

  const [detail, setDetail] = useState<ExplorationDetail | null>(null)
  const [sessions, setSessions] = useState<ExplorationSession[]>([])
  const [models, setModels] = useState<{ id: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const mode = 'auto' as const
  const [streamingText, setStreamingText] = useState('')
  const [preview, setPreview] = useState<{ dagId: string; path: string } | null>(null)
  const [answering, setAnswering] = useState<number | null>(null)
  const [reviewingApproval, setReviewingApproval] = useState<number | null>(null)
  const [approvalNotice, setApprovalNotice] = useState<string | null>(null)
  const [commandNotice, setCommandNotice] = useState<string | null>(null)
  const [planningQueued, setPlanningQueued] = useState(false)
  const [progress, setProgress] = useState<any>(null)
  const [cycleViews, setCycleViews] = useState<Record<number, 'collapsed' | 'summary' | 'full'>>({})
  const [filesOpen, setFilesOpen] = useState(false)
  const [fileEntries, setFileEntries] = useState<{ path: string; size: number }[]>([])
  const [deployables, setDeployables] = useState<DeployableApp[]>([])
  const [deployCandidate, setDeployCandidate] = useState<DeployableApp | null>(null)
  const treeCache = useRef<Map<string, any[]>>(new Map())
  const dagOrderCache = useRef<Map<string, string[]>>(new Map())
  const bottomRef = useRef<HTMLDivElement | null>(null)

  const load = async (showLoading = false) => {
    if (showLoading) setLoading(true)
    try {
      const res = await fetch(`${API}/api/explorations/${sessionId}`)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      setDetail(data)
      setError(null)
    } catch (err: any) {
      setError(err.message)
    } finally {
      if (showLoading) setLoading(false)
    }
  }

  const loadSessions = async () => {
    const qs = new URLSearchParams({ limit: '100' })
    if (activeProject) qs.set('project_id', activeProject)
    const res = await fetch(`${API}/api/explorations?${qs}`)
    if (res.ok) {
      const data = await res.json().catch(() => [])
      setSessions(Array.isArray(data) ? data : [])
    }
  }

  useEffect(() => {
    load(true)
    loadSessions()
    fetch(`${API}/api/llm/models`)
      .then((res) => (res.ok ? res.json() : { models: [] }))
      .then((data) => {
        const list = Array.isArray(data?.models) ? data.models : []
        setModels(list.filter((item: any) => item?.id).map((item: any) => ({ id: item.id })))
      })
      .catch(() => {})
  }, [sessionId, activeProject])

  useEffect(() => {
    if (!detail) return
    const status = detail.session.status
    if (!['running', 'awaiting_input', 'awaiting_run', 'awaiting_approval'].includes(status)) {
      setProgress(null)
      return
    }
    let cancelled = false
    const tick = async () => {
      try {
        const res = await fetch(`${API}/api/explorations/${detail.session.id}/progress`)
        if (!res.ok) return
        const data = await res.json().catch(() => null)
        if (!data || cancelled) return
        setProgress(data.progress)
        if (data.session_status && data.session_status !== status) {
          await load(false)
        }
      } catch { /* polling is best-effort */ }
    }
    tick()
    const interval = window.setInterval(tick, status === 'running' ? 1500 : 2500)
    return () => { cancelled = true; window.clearInterval(interval) }
  }, [detail?.session.id, detail?.session.status])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [detail?.messages.length, streamingText, sending])

  useEffect(() => {
    if (!commandNotice) return
    const timer = window.setTimeout(() => setCommandNotice(null), 6000)
    return () => window.clearTimeout(timer)
  }, [commandNotice])

  useEffect(() => {
    if (!approvalNotice) return
    const timer = window.setTimeout(() => setApprovalNotice(null), 4000)
    return () => window.clearTimeout(timer)
  }, [approvalNotice])

  const session = detail?.session
  const status = session ? explorationStatus(session.status) : null
  const latestCycle = detail?.cycles?.[detail.cycles.length - 1]
  const feedbackAvailable = session?.status === 'awaiting_feedback' || session?.status === 'error'
  const queuedFeedback = useMemo(
    () => (detail?.messages || []).filter(
      (message) => message.role === 'user' && message.metadata?.feedback_queue === true,
    ),
    [detail?.messages],
  )
  const hasInitialRequest = Boolean(
    (detail?.messages || []).some(
      (message) => message.role === 'user' && message.metadata?.initial_request === true,
    ),
  )

  const fetchTree = async (dagId: string) => {
    const cached = treeCache.current.get(dagId)
    if (cached) return cached
    const res = await fetch(`${API}/api/dags/${dagId}/workspace/tree`)
    const data = await res.json().catch(() => ({}))
    const entries = Array.isArray(data) ? data : (data?.entries || [])
    treeCache.current.set(dagId, entries)
    return entries
  }

  useEffect(() => {
    if (!latestCycle?.dag_id) {
      setFileEntries([])
      return
    }
    let cancelled = false
    treeCache.current.delete(latestCycle.dag_id)
    fetchTree(latestCycle.dag_id)
      .then((entries: any[]) => {
        if (cancelled) return
        const files = entries
          .filter((entry: any) => entry?.path && !['AGENTS.md', 'SOUL.md'].includes(entry.path))
          .map((entry: any) => ({ path: entry.path, size: Number(entry.size) || 0 }))
        setFileEntries(files)
      })
      .catch(() => { if (!cancelled) setFileEntries([]) })
    return () => { cancelled = true }
  }, [latestCycle?.dag_id, detail?.session.status, detail?.cycles.length])

  const fetchDeployables = async () => {
    try {
      const res = await fetch(`${API}/api/explorations/${sessionId}/deployables`)
      if (!res.ok) return
      const data = await res.json().catch(() => ({}))
      setDeployables(Array.isArray(data?.deployables) ? data.deployables : [])
    } catch {
      setDeployables([])
    }
  }

  useEffect(() => {
    if (!detail) return
    fetchDeployables()
  }, [detail?.session.id, detail?.session.status, detail?.cycles.length])

  const send = async (text: string) => {
    if (!text.trim() || !session || sending) return
    if (text.trim().startsWith('/')) {
      await handleCommand(text.trim())
      return
    }
    setSending(true)
    setError(null)
    setStreamingText('')
    const tempMessage: ExplorationMessage = {
      id: -Date.now(),
      session_id: session.id,
      cycle_id: null,
      role: 'user',
      kind: 'text',
      content: text.trim(),
      metadata: { mode },
      created_at: new Date().toISOString(),
    }
    setDetail((current) => current ? { ...current, messages: [...current.messages, tempMessage] } : current)
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text.trim(), mode }),
      })
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}))
        throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let assistant = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const raw = line.slice(5).trim()
          if (!raw) continue
          let event: any
          try { event = JSON.parse(raw) } catch { continue }
          if (event.type === 'text_delta') {
            assistant += event.text || ''
            setStreamingText(assistant)
          } else if (event.type === 'error') {
            setError(event.message || 'Chat failed')
          }
        }
      }
      setStreamingText('')
      await load(false)
      await loadSessions()
    } catch (err: any) {
      setError(err.message)
      await load(false)
    } finally {
      setSending(false)
    }
  }

  const runCycle = async (dagId: string) => {
    if (!session) return
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}/cycles/${dagId}/start`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      await load(false)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const retryCycle = async (dagId: string, nodeId?: string, status?: string) => {
    if (!session) return
    if (!window.confirm('Retry this cycle? Its previous result will be replaced.')) return
    try {
      const url = nodeId && status === 'failed'
        ? `${API}/api/dags/${dagId}/retry-from/${encodeURIComponent(nodeId)}`
        : `${API}/api/explorations/${session.id}/cycles/${dagId}/retry`
      const res = await fetch(url, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      await load(false)
      await loadSessions()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const finish = async () => {
    if (!session) return
    if (!window.confirm('Finish this exploration? No further cycles will be planned.')) return
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}/finish`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await load(false)
      await loadSessions()
      setCommandNotice('Exploration finished and locked.')
    } catch (err: any) {
      setError(err.message)
    }
  }

  const planQueuedFeedback = async () => {
    if (!session) return
    setPlanningQueued(true)
    setError(null)
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}/feedback/plan`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      await load(false)
      await loadSessions()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setPlanningQueued(false)
    }
  }

  const focusComposer = () => {
    const editor = document.querySelector<HTMLElement>('.exploration-chat .cs-message-input__content-editor')
    editor?.focus()
  }

  const handleCommand = async (raw: string) => {
    const command = raw.trim().split(/\s+/)[0].toLowerCase()
    if (command === '/run' || command === '/start') {
      if (session?.status === 'awaiting_run' && latestCycle) {
        await runCycle(latestCycle.dag_id)
      } else {
        setCommandNotice('There is no proposed cycle ready to run.')
      }
      return
    }
    if (command === '/retry') {
      if (latestCycle) await retryCycle(latestCycle.dag_id)
      else setCommandNotice('There is no cycle to retry.')
      return
    }
    if (command === '/finish' || command === '/lock') {
      if (session?.status === 'finished') setCommandNotice('Exploration is already finished.')
      else await finish()
      return
    }
    if (command === '/files') {
      setFilesOpen(true)
      return
    }
    if (command === '/dag') {
      if (latestCycle) window.location.href = `/dags/${latestCycle.dag_id}`
      else setCommandNotice('There is no DAG yet.')
      return
    }
    if (command === '/feedback') {
      focusComposer()
      setCommandNotice('Type a question or change request; the copilot will detect which one it is.')
      return
    }
    if (command === '/help') {
      setCommandNotice('Commands: /run, /retry, /finish, /files, /dag, /feedback, /help')
      return
    }
    setCommandNotice(`Unknown command: ${command}. Type /help for commands.`)
  }

  const answerRequest = async (request: ExplorationRequest, answer: any) => {
    setAnswering(request.id)
    try {
      const res = await fetch(`${API}/api/dags/${request.dag_id}/user-requests/${request.id}/answer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      await load(false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setAnswering(null)
    }
  }

  const reviewApproval = async (request: any, decision: 'approved' | 'denied') => {
    setReviewingApproval(request.id)
    setError(null)
    try {
      const res = await fetch(`${API}/api/capabilities/requests/${request.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          decision,
          comment: decision === 'approved' ? 'Approved from exploration chat' : 'Denied from exploration chat',
          reviewed_by: 'chat',
        }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      setApprovalNotice(`${decision === 'approved' ? 'Approved' : 'Denied'} ${request.resource_name}`)
      await load(false)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setReviewingApproval(null)
    }
  }

  const cancelCycle = async () => {
    if (!session || !latestCycle) return
    if (!window.confirm('Cancel the running cycle? The exploration will wait for your feedback.')) return
    try {
      const res = await fetch(`${API}/api/dags/${latestCycle.dag_id}/cancel`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      }
      await load(false)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const changeModel = async (value: string) => {
    if (!session) return
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: value || null }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await load(false)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const changeBudget = async (field: 'thinking_budget' | 'quality_budget', value: string) => {
    if (!session) return
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDetail((current) => current ? { ...current, session: { ...current.session, [field]: value } } : current)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const openPreview = async (dagId: string, path: string) => {
    let normalized = String(path || '').replace(/^\/workspace\//, '').replace(/^\/+/, '')
    try {
      const tree = await fetchTree(dagId)
      const exact = tree.find((entry: any) => entry.path === normalized)
      if (exact) {
        setPreview({ dagId, path: exact.path })
        return
      }
      let candidates = tree.filter((entry: any) => entry.path?.endsWith('/' + normalized))
      if (candidates.length > 1) {
        let nodeOrder = dagOrderCache.current.get(dagId)
        if (!nodeOrder) {
          const res = await fetch(`${API}/api/dags/${dagId}`)
          const data = await res.json().catch(() => null)
          nodeOrder = Array.isArray(data?.nodes) ? data.nodes.map((node: any) => node.node_id) : []
          dagOrderCache.current.set(dagId, nodeOrder)
        }
        const order = new Map(nodeOrder.map((id, index) => [id, index]))
        candidates = [...candidates].sort((a: any, b: any) => {
          const aPrefix = a.path.split('/')[0]
          const bPrefix = b.path.split('/')[0]
          return (order.get(bPrefix) ?? -1) - (order.get(aPrefix) ?? -1)
        })
      }
      if (candidates.length > 0) {
        setPreview({ dagId, path: candidates[0].path })
        return
      }
      setPreview({ dagId, path: normalized })
    } catch (err: any) {
      setError(err.message)
      setPreview({ dagId, path: normalized })
    }
  }

  const cycleResults = useMemo(
    () => (detail?.messages || [])
      .filter((message) => message.kind === 'cycle_result')
      .sort((a, b) => (a.metadata?.cycle_number || 0) - (b.metadata?.cycle_number || 0)),
    [detail?.messages],
  )

  const versionMap = useMemo(() => {
    const versions = new Map<number, Map<string, number>>()
    const counters = new Map<string, number>()
    for (const message of cycleResults) {
      const number = message.metadata?.cycle_number
      if (number === undefined || number === null) continue
      const cycleVersions = new Map<string, number>()
      const seen = new Set<string>()
      for (const path of (message.metadata?.deliverables || [])) {
        const name = String(path).split('/').pop() || String(path)
        if (seen.has(name)) continue
        seen.add(name)
        const next = (counters.get(name) || 0) + 1
        counters.set(name, next)
        cycleVersions.set(name, next)
      }
      versions.set(number, cycleVersions)
    }
    return versions
  }, [cycleResults])

  const filteredMessages = useMemo(() => {
    const messages = detail?.messages || []
    const resultNumbers = new Set(
      messages
        .filter((message) => message.kind === 'cycle_result')
        .map((message) => message.metadata?.cycle_number),
    )
    const proposedNumbers = new Set(
      (detail?.cycles || [])
        .filter((cycle) => cycle.status === 'proposed')
        .map((cycle) => cycle.cycle_number),
    )
    const result: ExplorationMessage[] = []
    let lastNonStatus: ExplorationMessage | null = null
    for (const message of messages) {
      if (message.kind === 'status') continue
      if (
        message.role === 'assistant' &&
        message.kind === 'text' &&
        lastNonStatus?.kind === 'cycle_proposal'
      ) {
        continue
      }
      const superseded = message.kind === 'cycle_proposal' && (
        resultNumbers.has(message.metadata?.cycle_number) ||
        !proposedNumbers.has(message.metadata?.cycle_number)
      )
      if (!superseded) result.push(message)
      lastNonStatus = message
    }
    return result
  }, [detail?.messages, detail?.cycles])

  const conversationItems = useMemo(() => {
    type CycleItem = { type: 'cycle'; number: number; message: ExplorationMessage }
    type MessageItem = { type: 'message'; message: ExplorationMessage }
    const items: (CycleItem | MessageItem)[] = []
    for (const message of filteredMessages) {
      if (message.kind === 'cycle_result') {
        items.push({ type: 'cycle', number: Number(message.metadata?.cycle_number || 0), message })
      } else {
        items.push({ type: 'message', message })
      }
    }
    return items
  }, [filteredMessages])

  const fileSizes = useMemo(() => {
    const map = new Map<string, number>()
    for (const file of fileEntries) {
      map.set(file.path, file.size)
      const name = file.path.split('/').pop() || file.path
      if (!map.has(name)) map.set(name, file.size)
    }
    return map
  }, [fileEntries])


  const renderMessage = (message: ExplorationMessage) => {
    if (message.kind === 'cycle_proposal') {
      const meta = message.metadata || {}
      return (
        <CycleProposalCard
          key={message.id}
          meta={meta}
          canRun={session.status === 'awaiting_run' && latestCycle?.dag_id === meta.dag_id}
          onRun={runCycle}
        />
      )
    }

    if (message.kind === 'cycle_input') {
      const meta = message.metadata || {}
      const request = detail?.requests?.find((item) => item.id === meta.request_id)
      const answered = request?.status === 'answered'
      return (
        <div key={message.id} className="my-3 border-l-2 border-amber-500/60 py-1 pl-3">
          <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-wide text-gray-500">
            <span className="rounded border border-cyan-700/40 bg-cyan-500/10 px-1.5 py-0.5 text-cyan-300">Agent</span>
            <span>TaskForge Agent</span>
          </div>
          <div className="mt-1 text-[10px] uppercase tracking-wide text-amber-300">Needs input • {meta.node_id}</div>
          <p className="mt-1 text-xs text-gray-200">{meta.prompt}</p>
          {answered ? (
            <div className="mt-2 text-[10px] text-emerald-300">Answered</div>
          ) : (
            <InputAnswerForm request={request} answering={answering === meta.request_id} onAnswer={(answer) => answerRequest(request || { ...meta, id: meta.request_id } as any, answer)} />
          )}
        </div>
      )
    }

    if (message.kind === 'error') {
      return <div key={message.id} className="my-3 border-l-2 border-red-700/50 py-1 pl-3 text-xs text-red-300">{message.content}</div>
    }

    if (message.kind === 'status') return null

    const isUser = message.role === 'user'
    if (isUser) {
      const consumedCycle = message.metadata?.feedback_consumed_cycle
      return (
        <ChatMessageBubble key={message.id} sender="user">
          <div>{message.content}</div>
          {message.metadata?.feedback_queue === true && (
            <div className="mt-1 text-[9px] text-indigo-200/80">Queued for next cycle</div>
          )}
          {consumedCycle && (
            <div className="mt-1 text-[9px] text-indigo-200/80">Applied to cycle {consumedCycle}</div>
          )}
        </ChatMessageBubble>
      )
    }
    return (
      <ChatMessageBubble key={message.id} sender="agent">
        <RenderedMarkdown content={message.content} />
      </ChatMessageBubble>
    )
  }

  if (loading && !detail) {
    return <div className="p-8 text-sm text-gray-500">Loading exploration…</div>
  }
  if (!detail || !session) {
    return <div className="p-8 text-sm text-red-300">{error || 'Exploration not found.'}</div>
  }

  return (
    <div className="exploration-chat flex h-full min-h-0 gap-3">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-gray-800/60 pr-2 md:flex">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-[10px] uppercase tracking-wide text-gray-500">Sessions</span>
          <Link href="/explorations?new=1" className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-indigo-300 hover:border-indigo-500">+ New</Link>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto">
          {sessions.map((item) => (
            <Link
              key={item.id}
              href={`/explorations/${item.id}`}
              className={`block rounded border px-2 py-1.5 ${item.id === session.id ? 'border-indigo-500 bg-indigo-950/30' : 'border-transparent hover:bg-gray-900'}`}
            >
              <div className="truncate text-[11px] text-gray-300">{item.title}</div>
              <div className="mt-0.5 text-[9px] text-gray-600">{item.status.replace(/_/g, ' ')} • {item.current_cycle} cycles</div>
            </Link>
          ))}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="mb-2 flex flex-wrap items-center gap-2 border-b border-gray-800/60 pb-2">
          <Link href="/explorations" className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 md:hidden">← Sessions</Link>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-white">{session.title}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-gray-500">
              <span className="rounded border border-gray-700 px-1.5 py-0.5">Cycle {session.current_cycle}/{session.max_cycles}</span>
              {status && <span className={`rounded border px-1.5 py-0.5 uppercase ${status.className}`}>{status.label}</span>}
            </div>
          </div>
          <button
            onClick={() => setFilesOpen(true)}
            disabled={!latestCycle}
            className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:border-gray-500 disabled:opacity-40"
          >
            📁 Files ({fileEntries.length})
          </button>
          {latestCycle && (
            <Link href={`/dags/${latestCycle.dag_id}`} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:border-gray-500">👁 DAG</Link>
          )}
          {progress?.workflow_id && (
            <a
              href={`${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(progress.workflow_id)}`}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-blue-700/50 px-2 py-1 text-[10px] text-blue-300 hover:border-blue-500"
              title={progress.workflow_id}
            >
              Temporal ↗
            </a>
          )}
          {(session.status === 'running' || session.status === 'awaiting_input') && latestCycle && (
            <button onClick={cancelCycle} className="rounded border border-red-700/50 px-2 py-1 text-[10px] text-red-300 hover:border-red-500">Cancel cycle</button>
          )}
          {session.status === 'finished' ? (
            <span className="rounded border border-emerald-700/40 px-2 py-1 text-[10px] text-emerald-300">🔒 Locked</span>
          ) : (
            <button
              onClick={finish}
              disabled={['running', 'awaiting_input', 'awaiting_approval'].includes(session.status)}
              className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:border-emerald-500 hover:text-emerald-300 disabled:opacity-40"
              title="Finish and lock this exploration"
            >
              Finish 🔒
            </button>
          )}
        </div>

        {error && <div className="mb-2 rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}
        {commandNotice && (
          <div className="mb-2 rounded border border-cyan-700/30 bg-cyan-950/20 p-2 text-xs text-cyan-200">{commandNotice}</div>
        )}

        <div className="relative flex min-h-0 flex-1 flex-col">
          {queuedFeedback.length > 0 && (
            <div className="mb-1 flex flex-wrap items-center gap-2 rounded border border-amber-700/30 bg-amber-950/20 px-2 py-1 text-[10px] text-amber-200">
              <span>
                {queuedFeedback.length} feedback note{queuedFeedback.length === 1 ? '' : 's'} queued for after Cycle {session.current_cycle}.
              </span>
              {feedbackAvailable && (
                <button
                  onClick={planQueuedFeedback}
                  disabled={planningQueued}
                  className="ml-auto rounded border border-amber-600/50 px-2 py-0.5 text-[10px] text-amber-200 hover:border-amber-400 disabled:opacity-50"
                >
                  {planningQueued ? 'Planning…' : '▷ Plan next cycle with queued feedback'}
                </button>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-b border-gray-800/60 px-2 py-1">
            <span className="text-[9px] uppercase tracking-wide text-gray-600">Config:</span>
            <select
              value={session.model || ''}
              onChange={(event) => changeModel(event.target.value)}
              className="rounded border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-400"
            >
              <option value="">Default model</option>
              {session.model && !models.some((item) => item.id === session.model) && (
                <option value={session.model}>{session.model}</option>
              )}
              {models.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
            </select>
            <select
              value={session.thinking_budget || 'standard'}
              onChange={(event) => changeBudget('thinking_budget', event.target.value)}
              className="rounded border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-400"
              title="Thought/turn budget for the next cycle"
            >
              {THINKING_BUDGET_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>🧠 {option.label} Mode</option>
              ))}
            </select>
            <select
              value={session.quality_budget || 'standard'}
              onChange={(event) => changeBudget('quality_budget', event.target.value)}
              className="rounded border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-[10px] text-gray-400"
              title="Quality cross-check budget for the next cycle"
            >
              {QUALITY_BUDGET_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  🛡 {option.value === 'standard' ? 'Standard Tests' : option.value === 'full' ? 'Full Audit' : 'Minimal Checks'}
                </option>
              ))}
            </select>
            <span className="ml-auto text-[9px] text-gray-600">
              Questions are answered; change requests plan or queue the next cycle.
            </span>
          </div>
          <MainContainer className="min-h-0 flex-1">
            <ChatContainer className="exploration-chat-stream">
              <MessageList>
                {!hasInitialRequest && session.objective && (
                  <ChatMessageBubble sender="user">
                    <div className="whitespace-pre-wrap">{session.objective}</div>
                  </ChatMessageBubble>
                )}
                {conversationItems.map((item) => {
                  if (item.type === 'message') return renderMessage(item.message)
                  if (item.type === 'cycle') {
                    const meta = item.message.metadata || {}
                    return (
                      <CycleCard
                        key={item.message.id}
                        meta={meta}
                        view={cycleViews[item.number] || 'collapsed'}
                        onViewChange={(next) => setCycleViews((current) => ({ ...current, [item.number]: next }))}
                        versionMap={versionMap.get(item.number)}
                        fileSizes={fileSizes}
                        onPreview={openPreview}
                        onRetry={retryCycle}
                        onFinish={finish}
                        sessionStatus={session.status}
                        deployable={deployables.find((candidate) => candidate.cycle_number === item.number) || null}
                        onDeploy={(candidate) => setDeployCandidate(candidate)}
                      />
                    )
                  }
                  return null
                })}
                {progress && ['running', 'awaiting_input', 'awaiting_approval'].includes(session.status) && (
                  <CycleProgressCard
                    sessionStatus={session.status}
                    progress={progress}
                    thinkingBudget={session.thinking_budget}
                    qualityBudget={session.quality_budget}
                  />
                )}
                {(detail.approvals || [])
                  .filter((approval: any) => approval.status === 'pending')
                  .map((approval: any) => (
                    <ApprovalCard
                      key={approval.id}
                      request={approval}
                      reviewing={reviewingApproval === approval.id}
                      onReview={(decision) => reviewApproval(approval, decision)}
                    />
                  ))}
                {approvalNotice && (
                  <div className="my-2 flex items-center justify-center gap-2 text-[10px] text-emerald-300">
                    <span>{approvalNotice}</span>
                    <button onClick={() => setApprovalNotice(null)} className="text-emerald-500/70 hover:text-emerald-300">✕</button>
                  </div>
                )}
                {feedbackAvailable && latestCycle && (
                  <div className="my-3 flex flex-wrap items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2 text-[10px] text-gray-400">
                    <span className="uppercase tracking-wide text-gray-600">Suggested next steps:</span>
                    <button onClick={finish} className="rounded border border-emerald-700/50 px-2 py-0.5 text-emerald-300 hover:border-emerald-500">
                      ▷ Looks good, finalize 🔒
                    </button>
                    <button onClick={focusComposer} className="rounded border border-gray-700 px-2 py-0.5 text-gray-300 hover:border-gray-500">
                      ↔ Give feedback
                    </button>
                  </div>
                )}
                {streamingText && (
                  <ChatMessageBubble sender="agent" streaming>
                    <RenderedMarkdown content={streamingText} />
                  </ChatMessageBubble>
                )}
                {sending && !streamingText && <TypingIndicator content="Thinking…" />}
                <div ref={bottomRef} />
              </MessageList>
              <MessageInput
                placeholder="Ask about results or describe changes for the next cycle…"
                onSend={(_html, text) => send(text)}
                attachButton={false}
                sendButton
                disabled={sending}
              />
            </ChatContainer>
          </MainContainer>
        </div>
      </div>

      {deployCandidate && (
        <DeployDialog
          candidate={deployCandidate}
          sessionTitle={session?.title || 'Exploration'}
          onClose={() => { setDeployCandidate(null); void fetchDeployables() }}
          onDeployed={() => { void fetchDeployables() }}
        />
      )}

      {filesOpen && (
        <FilesDrawer
          files={fileEntries}
          onClose={() => setFilesOpen(false)}
          onPreview={(path) => {
            setFilesOpen(false)
            if (latestCycle) void openPreview(latestCycle.dag_id, path)
          }}
        />
      )}

      {preview && (
        <div className="fixed inset-0 z-[70] flex justify-end bg-black/50" onClick={() => setPreview(null)}>
          <div className="h-full w-full max-w-3xl border-l border-gray-700 bg-[#12121a]" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-gray-800 px-3 py-2">
              <span className="truncate font-mono text-xs text-gray-300">{preview.path}</span>
              <button onClick={() => setPreview(null)} className="text-gray-500 hover:text-gray-200">✕</button>
            </div>
            <div className="h-[calc(100%-2.5rem)]">
              <DeliverablePreview
                url={`${API}/api/dags/${preview.dagId}/workspace/files/${encodePath(preview.path)}?inline=1`}
                filename={basename(preview.path)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function InputAnswerForm({
  request,
  answering,
  onAnswer,
}: {
  request?: ExplorationRequest
  answering: boolean
  onAnswer: (answer: any) => void
}) {
  const payload = request?.payload || {}
  const options = Array.isArray(payload.options) ? payload.options : []
  const fields = Array.isArray(payload.fields) ? payload.fields : []
  const [choice, setChoice] = useState('')
  const [justification, setJustification] = useState('')
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [text, setText] = useState('')
  const requireJustification = payload.require_justification === true

  if (options.length > 0) {
    const needsJust = requireJustification || options.find((option: any) => (option.value ?? option) === choice)?.require_justification === true
    return (
      <div className="mt-2 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {options.map((option: any, index: number) => {
            const value = typeof option === 'string' ? option : option.value ?? option.label ?? String(index)
            const label = typeof option === 'string' ? option : option.label ?? option.value ?? String(index)
            return (
              <button
                key={`${value}-${index}`}
                disabled={answering}
                onClick={() => setChoice(value)}
                className={`rounded border px-2 py-1 text-[10px] disabled:opacity-50 ${choice === value ? 'border-amber-400 bg-amber-500 text-black' : 'border-amber-700/50 text-amber-200 hover:border-amber-500'}`}
              >
                {label}
              </button>
            )
          })}
        </div>
        <textarea
          value={justification}
          onChange={(event) => setJustification(event.target.value)}
          rows={2}
          placeholder={needsJust ? 'Justification required…' : 'Justification (optional)…'}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
        />
        <button
          disabled={answering || !choice || (needsJust && !justification.trim())}
          onClick={() => onAnswer({ choice, justification: justification.trim() })}
          className="rounded bg-amber-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
        >
          Submit decision
        </button>
      </div>
    )
  }

  if (fields.length > 0) {
    return (
      <div className="mt-2 space-y-2">
        {fields.map((field: any, index: number) => {
          const key = field.key ?? field.name ?? String(index)
          return (
            <div key={key}>
              <label className="mb-0.5 block text-[10px] text-gray-500">{field.label || key}</label>
              <input
                type={field.type === 'number' ? 'number' : 'text'}
                value={fieldValues[key] || ''}
                onChange={(event) => setFieldValues((prev) => ({ ...prev, [key]: event.target.value }))}
                className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
              />
            </div>
          )
        })}
        <button
          disabled={answering}
          onClick={() => onAnswer({ fields: fieldValues })}
          className="rounded bg-amber-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
        >
          Submit
        </button>
      </div>
    )
  }

  return (
    <div className="mt-2 flex gap-1.5">
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        className="flex-1 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-xs text-gray-200"
        placeholder="Your answer…"
      />
      <button
        disabled={answering || !text.trim()}
        onClick={() => onAnswer({ text })}
        className="rounded bg-amber-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
      >
        Submit
      </button>
    </div>
  )
}
