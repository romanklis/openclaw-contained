'use client'

import { useEffect, useRef, useState } from 'react'
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
import CycleResultCard from '../../components/exploration/CycleResultCard'
import CycleProgressCard from '../../components/exploration/CycleProgressCard'
import {
  explorationStatus,
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


function cycleLabel(cycle: any) {
  return `Cycle ${cycle?.cycle_number ?? '?'}`
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
  const [progress, setProgress] = useState<any>(null)
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
    if (!['running', 'awaiting_input', 'awaiting_run'].includes(status)) {
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

  const finish = async () => {
    if (!session) return
    if (!window.confirm('Finish this exploration? No further cycles will be planned.')) return
    try {
      const res = await fetch(`${API}/api/explorations/${session.id}/finish`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
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
      let tree = treeCache.current.get(dagId)
      if (!tree) {
        const res = await fetch(`${API}/api/dags/${dagId}/workspace/tree`)
        const data = await res.json().catch(() => ({}))
        tree = Array.isArray(data) ? data : (data?.entries || [])
        treeCache.current.set(dagId, tree)
      }
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

  const renderMessage = (message: ExplorationMessage) => {
    if (message.kind === 'cycle_proposal') {
      const meta = message.metadata || {}
      return (
        <div key={message.id} className="my-2 border-l-2 border-purple-500/60 py-1 pl-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] uppercase tracking-wide text-purple-300">🛠 Planned cycle {meta.cycle_number}</span>
            <span className="text-[10px] text-gray-500">{meta.node_count} nodes</span>
            {meta.model && <span className="font-mono text-[10px] text-gray-500">{meta.model}</span>}
          </div>
          <p className="mt-1 line-clamp-3 text-xs text-gray-400">{meta.objective}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Link href={`/dags/${meta.dag_id}`} className="rounded border border-gray-700 px-2.5 py-1 text-[10px] text-gray-300 hover:border-gray-500">
              Review DAG
            </Link>
            {session.status === 'awaiting_run' && latestCycle?.dag_id === meta.dag_id && (
              <button onClick={() => runCycle(meta.dag_id)} className="rounded bg-emerald-600 px-2.5 py-1 text-[10px] font-semibold text-white hover:bg-emerald-500">
                ▷ Run cycle
              </button>
            )}
          </div>
        </div>
      )
    }

    if (message.kind === 'cycle_result') {
      return (
        <CycleResultCard
          key={message.id}
          meta={message.metadata || {}}
          onPreview={openPreview}
          onRetry={retryCycle}
        />
      )
    }

    if (message.kind === 'cycle_input') {
      const meta = message.metadata || {}
      const request = detail?.requests?.find((item) => item.id === meta.request_id)
      const answered = request?.status === 'answered'
      return (
        <div key={message.id} className="my-2 border-l-2 border-amber-500/60 py-1 pl-3">
          <div className="text-[10px] uppercase tracking-wide text-amber-300">Needs input • {meta.node_id}</div>
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
      return <div key={message.id} className="my-2 rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{message.content}</div>
    }

    if (message.kind === 'status') {
      return <div key={message.id} className="my-1 text-center text-[10px] text-gray-600">{message.content}</div>
    }

    const isUser = message.role === 'user'
    return (
      <div key={message.id} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs ${isUser ? 'bg-indigo-600 text-white' : 'border border-gray-800 bg-[#161b22] text-gray-200'}`}>
          {isUser ? message.content : <RenderedMarkdown content={message.content} />}
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
      <aside className="hidden w-64 shrink-0 flex-col rounded-lg border border-gray-800 bg-[#12121a] p-2 md:flex">
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
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-gray-800 bg-[#12121a] px-3 py-2">
          <Link href="/explorations" className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 md:hidden">← Sessions</Link>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-white">{session.title}</div>
            <div className="flex flex-wrap items-center gap-2 text-[10px] text-gray-500">
              {status && <span className={`rounded border px-1.5 py-0.5 uppercase ${status.className}`}>{status.label}</span>}
              <span>Cycle {session.current_cycle}/{session.max_cycles}</span>
              {session.model && <span className="font-mono">{session.model}</span>}
            </div>
          </div>
          <select
            value={session.model || ''}
            onChange={(event) => changeModel(event.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[10px] text-gray-300"
          >
            <option value="">Default model</option>
            {session.model && !models.some((item) => item.id === session.model) && (
              <option value={session.model}>{session.model}</option>
            )}
            {models.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
          <div className="flex items-center gap-1 rounded border border-gray-700 p-0.5">
            <button
              onClick={() => setMode('chat')}
              className={`rounded px-2 py-1 text-[10px] ${mode === 'chat' ? 'bg-gray-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
            >
              💬 Chat
            </button>
            <button
              onClick={() => feedbackAvailable && setMode('feedback')}
              disabled={!feedbackAvailable}
              className={`rounded px-2 py-1 text-[10px] disabled:opacity-40 ${mode === 'feedback' ? 'bg-amber-700 text-white' : 'text-gray-500 hover:text-gray-300'}`}
              title={feedbackAvailable ? 'Finalize this cycle and plan the next one' : 'Available after a cycle finishes'}
            >
              ↩ Feedback
            </button>
          </div>
          {latestCycle && (
            <Link href={`/dags/${latestCycle.dag_id}`} className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:border-gray-500">Open latest DAG</Link>
          )}
          {(session.status === 'running' || session.status === 'awaiting_input') && latestCycle && (
            <button onClick={cancelCycle} className="rounded border border-red-700/50 px-2 py-1 text-[10px] text-red-300 hover:border-red-500">Cancel cycle</button>
          )}
          <button
            onClick={finish}
            disabled={session.status === 'running' || session.status === 'awaiting_input'}
            className="rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-400 hover:border-red-500 hover:text-red-300 disabled:opacity-40"
          >
            Finish
          </button>
        </div>

        {error && <div className="mb-2 rounded border border-red-700/40 bg-red-950/30 p-2 text-xs text-red-300">{error}</div>}

        <div className="relative min-h-0 flex-1">
          <MainContainer className="h-full rounded-lg border border-gray-800">
            <ChatContainer>
              <MessageList>
                {detail.messages.map(renderMessage)}
                <CycleProgressCard sessionStatus={session.status} progress={progress} />
                {streamingText && (
                  <div className="flex justify-start">
                    <div className="max-w-[80%] rounded-lg border border-gray-800 bg-[#161b22] px-3 py-2 text-xs text-gray-200">
                      <RenderedMarkdown content={streamingText} />
                    </div>
                  </div>
                )}
                {sending && !streamingText && <TypingIndicator content="Thinking…" />}
                <div ref={bottomRef} />
              </MessageList>
              <MessageInput
                placeholder={mode === 'feedback' ? 'Describe the feedback and what the next cycle should do…' : 'Ask about the results or discuss the exploration…'}
                onSend={(_html, text) => send(text)}
                attachButton={false}
                sendButton
                disabled={sending}
              />
            </ChatContainer>
          </MainContainer>
        </div>
      </div>

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
