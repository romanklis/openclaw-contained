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
import { API } from '../../lib/api'
import { useProject } from '../../lib/ProjectContext'
import { RenderedMarkdown } from '../../components/DeliverablePreview'
import DeliverablePreview from '../../components/DeliverablePreview'
import CycleCard from '../../components/exploration/CycleCard'
import CycleGroupCard from '../../components/exploration/CycleGroupCard'
import FilesDrawer from '../../components/exploration/FilesDrawer'
import ApprovalCard from '../../components/exploration/ApprovalCard'
import DeployDialog from '../../components/exploration/DeployDialog'
import CycleProgressCard from '../../components/exploration/CycleProgressCard'
import {
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
  const [mode, setMode] = useState<'chat' | 'feedback'>('chat')
  const [streamingText, setStreamingText] = useState('')
  const [preview, setPreview] = useState<{ dagId: string; path: string } | null>(null)
  const [answering, setAnswering] = useState<number | null>(null)
  const [reviewingApproval, setReviewingApproval] = useState<number | null>(null)
  const [approvalNotice, setApprovalNotice] = useState<string | null>(null)
  const [progress, setProgress] = useState<any>(null)
  const [expandedCycles, setExpandedCycles] = useState<Set<number>>(new Set())
  const [filesOpen, setFilesOpen] = useState(false)
  const [fileEntries, setFileEntries] = useState<{ path: string; size: number }[]>([])
  const [deployables, setDeployables] = useState<DeployableApp[]>([])
  const [deployCandidate, setDeployCandidate] = useState<DeployableApp | null>(null)
  const treeCache = useRef<Map<string, any[]>>(new Map())
  const dagOrderCache = useRef<Map<string, string[]>>(new Map())
  const autoExpandedCycle = useRef<number | null>(null)
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

  const session = detail?.session
  const status = session ? explorationStatus(session.status) : null
  const latestCycle = detail?.cycles?.[detail.cycles.length - 1]
  const feedbackAvailable = session?.status === 'awaiting_feedback' || session?.status === 'error'

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
      setMode('chat')
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

  const retryCycle = async (dagId: string, nodeId?: string) => {
    if (!nodeId) {
      window.location.href = `/dags/${dagId}`
      return
    }
    try {
      const res = await fetch(`${API}/api/dags/${dagId}/retry-from/${encodeURIComponent(nodeId)}`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error((data as any)?.detail || `HTTP ${res.status}`)
      await load(false)
    } catch (err: any) {
      setError(err.message)
    }
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

  const latestResultNumber = cycleResults.length > 0
    ? cycleResults[cycleResults.length - 1].metadata?.cycle_number
    : null

  useEffect(() => {
    if (latestResultNumber === null || latestResultNumber === undefined) return
    const previousAuto = autoExpandedCycle.current
    if (previousAuto === latestResultNumber) return
    autoExpandedCycle.current = latestResultNumber
    setExpandedCycles((prev) => {
      const next = new Set(prev)
      if (previousAuto !== null) next.delete(previousAuto)
      next.add(latestResultNumber)
      return next
    })
  }, [latestResultNumber])

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
    type GroupItem = { type: 'cycleGroup'; items: CycleItem[] }
    const items: (CycleItem | MessageItem | GroupItem)[] = []
    let group: CycleItem[] = []
    const flush = () => {
      if (group.length === 1) items.push(group[0])
      else if (group.length > 1) items.push({ type: 'cycleGroup', items: [...group] })
      group = []
    }
    for (const message of filteredMessages) {
      if (message.kind === 'cycle_result') {
        const number = Number(message.metadata?.cycle_number || 0)
        const item: CycleItem = { type: 'cycle', number, message }
        if (expandedCycles.has(number)) {
          flush()
          items.push(item)
        } else {
          group.push(item)
        }
      } else {
        flush()
        items.push({ type: 'message', message })
      }
    }
    flush()
    return items
  }, [filteredMessages, expandedCycles])

  const fileSizes = useMemo(() => {
    const map = new Map<string, number>()
    for (const file of fileEntries) {
      map.set(file.path, file.size)
      const name = file.path.split('/').pop() || file.path
      if (!map.has(name)) map.set(name, file.size)
    }
    return map
  }, [fileEntries])

  const toggleCycle = (number: number) => {
    setExpandedCycles((prev) => {
      const next = new Set(prev)
      if (next.has(number)) next.delete(number)
      else next.add(number)
      return next
    })
  }

  const renderMessage = (message: ExplorationMessage) => {
    if (message.kind === 'cycle_proposal') {
      const meta = message.metadata || {}
      return (
        <div key={message.id} className="my-3 border-l-2 border-purple-500/50 py-0.5 pl-3 text-xs text-gray-300">
          <span>Planned cycle {meta.cycle_number}</span>
          <span className="ml-1 text-gray-600">· {meta.node_count} steps</span>
          <Link href={`/dags/${meta.dag_id}`} className="ml-2 text-cyan-400 hover:text-cyan-300">
            Review DAG →
          </Link>
          {session.status === 'awaiting_run' && latestCycle?.dag_id === meta.dag_id && (
            <button
              onClick={() => runCycle(meta.dag_id)}
              className="ml-2 text-emerald-400 hover:text-emerald-300"
            >
              ▷ Run cycle
            </button>
          )}
        </div>
      )
    }

    if (message.kind === 'cycle_input') {
      const meta = message.metadata || {}
      const request = detail?.requests?.find((item) => item.id === meta.request_id)
      const answered = request?.status === 'answered'
      return (
        <div key={message.id} className="my-3 border-l-2 border-amber-500/60 py-1 pl-3">
          <div className="text-[9px] uppercase tracking-wide text-gray-600">🤖 TaskForge Agent</div>
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
      return (
        <div key={message.id} className="my-3 flex justify-end">
          <div className="max-w-[78%] rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white">
            {message.content}
          </div>
        </div>
      )
    }
    return (
      <div key={message.id} className="my-3">
        <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-600">🤖 TaskForge Agent</div>
        <div className="border-l-2 border-indigo-500/30 py-0.5 pl-3 text-xs text-gray-200">
          <RenderedMarkdown content={message.content} />
        </div>
      </div>
    )
  }

  if (loading && !detail) {
    return <div className="p-8 text-sm text-gray-500">Loading exploration…</div>
  }
  if (!detail || !session) {
    return <div className="p-8 text-sm text-red-300">{error || 'Exploration not found.'}</div>
  }

  return (
    <div className="exploration-chat flex h-[calc(100vh-4rem)] gap-3">
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
            <div className="flex flex-wrap items-center gap-2 text-[10px] text-gray-500">
              {status && <span className={`rounded border px-1.5 py-0.5 uppercase ${status.className}`}>{status.label}</span>}
              <span>Cycle {session.current_cycle}/{session.max_cycles}</span>
              {session.model && <span className="font-mono">{session.model}</span>}
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
          {(session.status === 'running' || session.status === 'awaiting_input') && latestCycle && (
            <button onClick={cancelCycle} className="rounded border border-red-700/50 px-2 py-1 text-[10px] text-red-300 hover:border-red-500">Cancel cycle</button>
          )}
        </div>

        {error && <div className="mb-2 rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}

        <div className="relative min-h-0 flex-1">
          <MainContainer className="h-full">
            <ChatContainer>
              <MessageList>
                {conversationItems.map((item) => {
                  if (item.type === 'message') return renderMessage(item.message)
                  if (item.type === 'cycle') {
                    const meta = item.message.metadata || {}
                    return (
                      <CycleCard
                        key={item.message.id}
                        meta={meta}
                        expanded={expandedCycles.has(item.number)}
                        onToggle={() => toggleCycle(item.number)}
                        versionMap={versionMap.get(item.number)}
                        fileSizes={fileSizes}
                        onPreview={openPreview}
                        onRetry={retryCycle}
                        deployable={deployables.find((candidate) => candidate.cycle_number === item.number) || null}
                        onDeploy={(candidate) => setDeployCandidate(candidate)}
                      />
                    )
                  }
                  return (
                    <CycleGroupCard
                      key={`group-${item.items[0].message.id}`}
                      cycles={item.items.map((cycle) => ({ message: cycle.message, meta: cycle.message.metadata || {} }))}
                      onExpand={toggleCycle}
                    />
                  )
                })}
                {progress && ['running', 'awaiting_input', 'awaiting_approval'].includes(session.status) && (
                  <CycleProgressCard sessionStatus={session.status} progress={progress} />
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
                  <div className="my-2 text-center text-[10px] text-emerald-300">{approvalNotice}</div>
                )}
                {streamingText && (
                  <div className="my-3">
                    <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-600">🤖 TaskForge Agent</div>
                    <div className="border-l-2 border-indigo-500/30 py-0.5 pl-3 text-xs text-gray-200">
                      <RenderedMarkdown content={streamingText} />
                    </div>
                  </div>
                )}
                {sending && !streamingText && <TypingIndicator content="Thinking…" />}
                <div ref={bottomRef} />
              </MessageList>
              <div className="flex flex-wrap items-center gap-2 border-t border-gray-800/60 px-2 py-1">
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
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setMode('chat')}
                    className={`rounded px-2 py-0.5 text-[10px] ${mode === 'chat' ? 'text-gray-200' : 'text-gray-600 hover:text-gray-400'}`}
                  >
                    💬 Chat
                  </button>
                  <button
                    onClick={() => feedbackAvailable && setMode('feedback')}
                    disabled={!feedbackAvailable}
                    className={`rounded px-2 py-0.5 text-[10px] disabled:opacity-40 ${mode === 'feedback' ? 'text-amber-300' : 'text-gray-600 hover:text-gray-400'}`}
                    title={feedbackAvailable ? 'Finalize this cycle and plan the next one' : 'Available after a cycle finishes'}
                  >
                    ↩ Feedback
                  </button>
                </div>
                {mode === 'feedback' && <span className="text-[9px] text-amber-300/80">Finalizes the current cycle</span>}
              </div>
              <MessageInput
                placeholder={mode === 'feedback' ? 'Describe the feedback and what the next cycle should do…' : 'Ask about results or give steering feedback…'}
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
