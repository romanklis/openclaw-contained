'use client'

import { useState, useEffect, useCallback } from 'react'
import { API } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────

interface LLMModel {
  id: string
  provider: string
}

interface Provider {
  name: string
  type: string
  url?: string
  available: boolean
  models: string[]
  error?: string | null
  hint?: string | null
}

interface ProviderHealth {
  status: string
  url?: string
  error?: string
  hint?: string
  version?: string
  models: string[]
}

interface DiagnoseStep {
  name: string
  ok: boolean
  detail: unknown
  latency_ms: number
}

interface DiagnoseResult {
  ok: boolean
  url: string
  version?: string | null
  steps: DiagnoseStep[]
  models: string[]
  model_info?: { model?: string; capabilities?: string[]; parameter_size?: string } | null
  systemone?: { p_true?: number | null } | null
  hints: string[]
}

interface DecisionResult {
  model?: string
  answers?: Record<string, any>
  usage?: any
  _meta?: { url: string; latency_ms: number }
}

interface TriageResult {
  decision: string
  p_escalate?: number | null
  model?: string
  latency_ms: number
  reason: string
  sampled: boolean
  features?: { red_flags?: string[] }
}

interface TaskSummary {
  id: string
  name?: string
  status?: string
}

interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  model?: string
  provider?: string
  tokens?: number
  latencyMs?: number
}

// ─── Component ───────────────────────────────────────────────────

export default function LLMProvidersPage() {
  const [providers, setProviders] = useState<Provider[]>([])
  const [health, setHealth] = useState<Record<string, ProviderHealth>>({})
  const [allModels, setAllModels] = useState<LLMModel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [selectedModel, setSelectedModel] = useState('')
  const [prompt, setPrompt] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const [temperature, setTemperature] = useState(0.7)
  const [maxTokens, setMaxTokens] = useState(2048)

  const [showConfig, setShowConfig] = useState(false)
  const [configOllamaUrl, setConfigOllamaUrl] = useState('')
  const [configGeminiKey, setConfigGeminiKey] = useState('')
  const [configAnthropicKey, setConfigAnthropicKey] = useState('')
  const [configOpenaiKey, setConfigOpenaiKey] = useState('')
  const [configDeepseekKey, setConfigDeepseekKey] = useState('')
  const [configStatus, setConfigStatus] = useState<{ gemini: boolean; anthropic: boolean; openai: boolean; deepseek: boolean }>({
    gemini: false, anthropic: false, openai: false, deepseek: false,
  })
  const [savingConfig, setSavingConfig] = useState(false)
  const [configMsg, setConfigMsg] = useState<string | null>(null)

  // Token pricing
  const [modelPrices, setModelPrices] = useState<any[]>([])
  const [priceModel, setPriceModel] = useState('')
  const [priceInput, setPriceInput] = useState('')
  const [priceOutput, setPriceOutput] = useState('')
  const [priceCurrency, setPriceCurrency] = useState('USD')
  const [priceMsg, setPriceMsg] = useState<string | null>(null)

  // DAG model defaults
  const [dagPlanningModel, setDagPlanningModel] = useState('')
  const [dagAgentModel, setDagAgentModel] = useState('')
  const [dagDeepReviewModel, setDagDeepReviewModel] = useState('')
  const [dagTriageEnabled, setDagTriageEnabled] = useState(true)
  const [dagTriageProvider, setDagTriageProvider] = useState('ollama')
  const [dagTriageModel, setDagTriageModel] = useState('')
  const [dagTriageThreshold, setDagTriageThreshold] = useState('0.20')
  const [dagTriageSampleRate, setDagTriageSampleRate] = useState('0.05')
  const [dagContextMode, setDagContextMode] = useState('none')
  const [savingDagDefaults, setSavingDagDefaults] = useState(false)
  const [dagDefaultsMsg, setDagDefaultsMsg] = useState<string | null>(null)

  // Ollama diagnostics
  const [diagnoseLoading, setDiagnoseLoading] = useState(false)
  const [diagnoseResult, setDiagnoseResult] = useState<DiagnoseResult | null>(null)
  const [diagnoseError, setDiagnoseError] = useState<string | null>(null)

  // Decision-model (System One) test
  const [decisionModel, setDecisionModel] = useState('kev:0.8b-q8_0')
  const [decisionState, setDecisionState] = useState(
    'A report claims to have fetched 3 articles, but the log shows 0 HTTP requests and no citations.'
  )
  const [decisionType, setDecisionType] = useState<'noul' | 'choice' | 'score'>('noul')
  const [decisionInstructions, setDecisionInstructions] = useState(
    'Does this execution show evidence of fabricated data or shortcuts that require a full integrity audit?'
  )
  const [decisionChoiceOptions, setDecisionChoiceOptions] = useState('clean, needs_audit')
  const [decisionScoreLevels, setDecisionScoreLevels] = useState('clean, minor_issues, suspicious, fabricated')
  const [decisionLoading, setDecisionLoading] = useState(false)
  const [decisionResult, setDecisionResult] = useState<DecisionResult | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)

  // Triage on a recent task
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [triageTaskId, setTriageTaskId] = useState('')
  const [triageLoading, setTriageLoading] = useState(false)
  const [triageResult, setTriageResult] = useState<TriageResult | null>(null)
  const [triageError, setTriageError] = useState<string | null>(null)

  // API URL imported at top-level

  // ─── Fetch config ──────────────────────────────────────────────

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/llm/config`)
      if (!res.ok) return
      const data = await res.json()
      setConfigOllamaUrl(data.ollama_url ?? '')
      setConfigGeminiKey(data.gemini_api_key ?? '')
      setConfigAnthropicKey(data.anthropic_api_key ?? '')
      setConfigOpenaiKey(data.openai_api_key ?? '')
      setConfigDeepseekKey(data.deepseek_api_key ?? '')
      setConfigStatus({
        gemini: data.gemini_configured ?? false,
        anthropic: data.anthropic_configured ?? false,
        openai: data.openai_configured ?? false,
        deepseek: data.deepseek_configured ?? false,
      })
    } catch { /* ignore */ }
  }, [])

  const fetchDagModelDefaults = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/dags/model-defaults`)
      if (!res.ok) return
      const data = await res.json()
      setDagPlanningModel(data.planning_model ?? '')
      setDagAgentModel(data.agent_model ?? '')
      setDagDeepReviewModel(data.deep_review_model ?? '')
      setDagTriageEnabled(String(data.triage_enabled ?? 'true').toLowerCase() !== 'false')
      setDagTriageProvider(data.triage_provider ?? 'ollama')
      setDagTriageModel(data.triage_model ?? '')
      setDecisionModel(data.triage_model || 'kev:0.8b-q8_0')
      setDagTriageThreshold(data.triage_threshold ?? '0.20')
      setDagTriageSampleRate(data.triage_sample_rate ?? '0.05')
      setDagContextMode(data.agent_context_mode ?? 'none')
    } catch { /* ignore */ }
  }, [])

  const saveDagModelDefaults = async () => {
    setSavingDagDefaults(true)
    setDagDefaultsMsg(null)
    try {
      const res = await fetch(`${API}/api/dags/model-defaults`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          planning_model: dagPlanningModel,
          agent_model: dagAgentModel,
          deep_review_model: dagDeepReviewModel,
          agent_context_mode: dagContextMode,
          triage_enabled: dagTriageEnabled,
          triage_provider: dagTriageProvider,
          triage_model: dagTriageModel,
          triage_threshold: dagTriageThreshold,
          triage_sample_rate: dagTriageSampleRate,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setDagPlanningModel(data.planning_model)
      setDagAgentModel(data.agent_model)
      setDagDeepReviewModel(data.deep_review_model)
      setDagTriageEnabled(String(data.triage_enabled ?? 'true').toLowerCase() !== 'false')
      setDagTriageProvider(data.triage_provider ?? 'ollama')
      setDagTriageModel(data.triage_model ?? '')
      setDecisionModel(data.triage_model || 'kev:0.8b-q8_0')
      setDagTriageThreshold(data.triage_threshold ?? '0.20')
      setDagTriageSampleRate(data.triage_sample_rate ?? '0.05')
      setDagContextMode(data.agent_context_mode ?? 'none')
      setDagDefaultsMsg(`✓ Defaults saved — planning: ${data.planning_model}, agents: ${data.agent_model}, deep-review: ${data.deep_review_model}, triage: ${data.triage_enabled === 'false' ? 'off' : `${data.triage_model} @ ${data.triage_threshold}`}, context-mode: ${data.agent_context_mode}`)
    } catch (err) {
      setDagDefaultsMsg(`✗ ${err instanceof Error ? err.message : 'Failed'}`)
    } finally {
      setSavingDagDefaults(false)
    }
  }

  const saveConfig = async (field: string, value: string) => {
    setSavingConfig(true)
    setConfigMsg(null)
    try {
      const res = await fetch(`${API}/api/llm/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setConfigMsg(`✓ ${data.changes?.join(', ') || 'Updated'}`)
      await Promise.all([fetchConfig(), fetchAll()])
    } catch (err) {
      setConfigMsg(`✗ ${err instanceof Error ? err.message : 'Failed'}`)
    } finally {
      setSavingConfig(false)
    }
  }

  // ─── Data fetching ─────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    setError(null)
    try {
      const [pRes, hRes, mRes] = await Promise.all([
        fetch(`${API}/api/llm/providers`),
        fetch(`${API}/api/llm/health`),
        fetch(`${API}/api/llm/models`),
      ])
      if (!pRes.ok || !hRes.ok || !mRes.ok) throw new Error('Failed to fetch LLM data')
      const pData = await pRes.json()
      const hData = await hRes.json()
      const mData = await mRes.json()
      setProviders(pData.providers ?? [])
      setHealth(hData.providers ?? {})
      setAllModels(mData.models ?? [])
      if (!selectedModel && mData.models?.length) {
        setSelectedModel(mData.models[0].id)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [selectedModel])

  useEffect(() => {
    fetchAll()
    fetchConfig()
    fetchDagModelDefaults()
    const interval = setInterval(fetchAll, 15000)
    return () => clearInterval(interval)
  }, [fetchAll, fetchConfig, fetchDagModelDefaults])

  // ─── Send message ──────────────────────────────────────────────

  const sendMessage = async () => {
    if (!prompt.trim() || !selectedModel) return
    setSending(true)
    const userMsg: ChatMessage = { role: 'user', content: prompt }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setPrompt('')
    const startTime = Date.now()
    try {
      const apiMessages = newMessages.map((m) => ({ role: m.role, content: m.content }))
      const res = await fetch(`${API}/api/llm/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: selectedModel,
          messages: apiMessages,
          temperature,
          max_tokens: maxTokens,
        }),
      })
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
      const data = await res.json()
      const latencyMs = Date.now() - startTime
      const choice = data.choices?.[0]
      setMessages([
        ...newMessages,
        {
          role: 'assistant',
          content: choice?.message?.content ?? '(empty response)',
          model: data.model,
          provider: detectProvider(selectedModel),
          tokens: data.usage?.total_tokens,
          latencyMs,
        },
      ])
    } catch (err) {
      setMessages([
        ...newMessages,
        { role: 'assistant', content: `❌ Error: ${err instanceof Error ? err.message : 'Unknown error'}` },
      ])
    } finally {
      setSending(false)
    }
  }

  // ─── Ollama diagnostics ────────────────────────────────────────

  const runDiagnose = async (urlOverride?: string) => {
    setDiagnoseLoading(true)
    setDiagnoseError(null)
    setDiagnoseResult(null)
    try {
      const params = new URLSearchParams({ model: dagTriageModel || 'kev:0.8b-q8_0' })
      if (urlOverride) params.set('url', urlOverride)
      const res = await fetch(`${API}/api/llm/diagnose?${params.toString()}`)
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
      setDiagnoseResult(await res.json())
    } catch (err) {
      setDiagnoseError(err instanceof Error ? err.message : 'Diagnose failed')
    } finally {
      setDiagnoseLoading(false)
    }
  }

  // ─── Chat test (works even when the model list is empty) ───────

  const testChat = async () => {
    const model = selectedModel.trim() || allModels[0]?.id
    if (!model) {
      setMessages([{ role: 'assistant', content: '❌ Enter a model name first (e.g. gemma3:4b or kev:0.8b-q8_0).' }])
      return
    }
    setSending(true)
    const started = Date.now()
    const userMsg: ChatMessage = { role: 'user', content: 'Reply with OK' }
    const next = [...messages, userMsg]
    setMessages(next)
    try {
      const res = await fetch(`${API}/api/llm/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages: [{ role: 'user', content: 'Reply with OK' }],
          temperature: 0,
          max_tokens: 32,
        }),
      })
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
      const data = await res.json()
      setMessages([
        ...next,
        {
          role: 'assistant',
          content: data.choices?.[0]?.message?.content ?? '(empty response)',
          model: data.model,
          provider: detectProvider(model),
          tokens: data.usage?.total_tokens,
          latencyMs: Date.now() - started,
        },
      ])
    } catch (err) {
      setMessages([
        ...next,
        { role: 'assistant', content: `❌ Error: ${err instanceof Error ? err.message : 'Unknown error'}` },
      ])
    } finally {
      setSending(false)
    }
  }

  // ─── Decision-model test ───────────────────────────────────────

  const runDecision = async () => {
    setDecisionLoading(true)
    setDecisionError(null)
    setDecisionResult(null)
    try {
      let question: Record<string, any>
      if (decisionType === 'noul') {
        question = { type: 'noul', instructions: decisionInstructions }
      } else if (decisionType === 'choice') {
        const options = decisionChoiceOptions.split(',').map((s) => s.trim()).filter(Boolean)
        if (options.length < 2) throw new Error('Choice needs at least 2 comma-separated options')
        question = {
          type: 'choice',
          instructions: decisionInstructions,
          criteria: Object.fromEntries(options.map((o) => [o, `The state matches: ${o}`])),
        }
      } else {
        const levels = decisionScoreLevels.split(',').map((s) => s.trim()).filter(Boolean)
        if (levels.length < 2) throw new Error('Score needs at least 2 comma-separated levels')
        question = { type: 'score', instructions: decisionInstructions, criteria: levels }
      }

      const res = await fetch(`${API}/api/llm/systemone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: decisionModel.trim() || 'kev:0.8b-q8_0',
          state: decisionState,
          questions: { decision: question },
        }),
      })
      if (!res.ok) {
        let detail: any = await res.text()
        try { detail = JSON.parse(detail).detail ?? detail } catch { /* ignore */ }
        throw new Error(typeof detail === 'string' ? detail : (detail.error || JSON.stringify(detail)))
      }
      setDecisionResult(await res.json())
    } catch (err) {
      setDecisionError(err instanceof Error ? err.message : 'Decision test failed')
    } finally {
      setDecisionLoading(false)
    }
  }

  // ─── Triage dry-run on a recent task ──────────────────────────

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/tasks?limit=20`)
      if (!res.ok) return
      const data = await res.json()
      const list = Array.isArray(data) ? data : (data.tasks ?? [])
      setTasks(list.map((t: any) => ({ id: t.id, name: t.name, status: t.status })))
      setTriageTaskId((current) => current || list[0]?.id || '')
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    fetchTasks()
  }, [fetchTasks])

  const runTriage = async () => {
    if (!triageTaskId) return
    setTriageLoading(true)
    setTriageError(null)
    setTriageResult(null)
    try {
      const res = await fetch(`${API}/api/skill-learning/gate-triage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: triageTaskId }),
      })
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
      setTriageResult(await res.json())
    } catch (err) {
      setTriageError(err instanceof Error ? err.message : 'Triage test failed')
    } finally {
      setTriageLoading(false)
    }
  }

  // ─── Helpers ───────────────────────────────────────────────────

  const detectProvider = (model: string) => {
    const m = model.toLowerCase()
    if (m.startsWith('gemini')) return 'gemini'
    if (m.startsWith('claude')) return 'anthropic'
    if (m.startsWith('gpt-') || m.startsWith('o1-') || m.startsWith('o3-')) return 'openai'
    if (m.startsWith('deepseek')) return 'deepseek'
    return 'ollama'
  }

  const statusDot = (status: string) => {
    if (status === 'healthy' || status === 'configured') return '🟢'
    if (status === 'not_configured') return '⚪'
    return '🔴'
  }

  const providerIcon = (type: string) => {
    switch (type) {
      case 'ollama': return '🦙'
      case 'gemini': return '✨'
      case 'anthropic': return '🧠'
      case 'openai': return '🤖'
      case 'deepseek': return '🔵'
      default: return '🔌'
    }
  }

  const providerColorDark = (type: string) => {
    switch (type) {
      case 'ollama': return 'border-purple-500/30 bg-purple-500/5'
      case 'gemini': return 'border-blue-500/30 bg-blue-500/5'
      case 'anthropic': return 'border-amber-500/30 bg-amber-500/5'
      case 'openai': return 'border-green-500/30 bg-green-500/5'
      case 'deepseek': return 'border-blue-500/30 bg-blue-500/5'
      default: return 'border-gray-500/30 bg-gray-500/5'
    }
  }

  // ─── Loading ───────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto">
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-500 text-sm">Loading LLM providers...</div>
        </div>
      </div>
    )
  }

  const saveModelPrice = async () => {
    if (!priceModel.trim()) return
    setPriceMsg(null)
    try {
      const res = await fetch(`${API}/api/llm/pricing`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: priceModel.trim(),
          input_per_million: Number(priceInput) || 0,
          output_per_million: Number(priceOutput) || 0,
          currency: priceCurrency || 'USD',
        }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const refreshed = await fetch(`${API}/api/llm/pricing`).then((r) => (r.ok ? r.json() : []))
      setModelPrices(Array.isArray(refreshed) ? refreshed : [])
      setPriceMsg('Saved. Existing token counts are unchanged.')
    } catch (err: any) {
      setPriceMsg(err.message)
    }
  }

  // ─── Render ────────────────────────────────────────────────────

  const decisionAnswer = (decisionResult?.answers?.decision ?? {}) as Record<string, any>
  const triageThreshold = parseFloat(dagTriageThreshold) || 0.2

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-1">LLM Router</h1>
        <p className="text-sm text-gray-500">
          Unified OpenAI-compatible proxy — dispatches to the right backend by model name
        </p>
      </div>

      {error && (
        <div className="mb-6 bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {health['ollama']?.error && (
        <div className="mb-6 bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 text-amber-300 text-sm">
          <strong>Ollama unreachable from the control-plane.</strong>{' '}
          If Ollama runs on Windows and TaskForge runs in WSL2, <code>host.docker.internal</code>{' '}
          points at the WSL VM, not Windows. In WSL run{' '}
          <code className="mx-1">make set-ollama-url</code>, then recreate the control-plane.
        </div>
      )}

      {/* ─── Provider Cards ──────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">Providers</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {providers.map((p) => {
            const h = health[p.name]
            return (
              <div key={p.name} className={`border rounded-xl p-4 transition-all hover:border-opacity-60 ${providerColorDark(p.type)}`}>
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xl">{providerIcon(p.type)}</span>
                  <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-[#12121a] border border-[#232333] text-gray-400">
                    {h ? statusDot(h.status) : '⚪'} {h?.status ?? 'unknown'}
                  </span>
                </div>
                <h3 className="text-base font-bold text-white capitalize mb-1">{p.name}</h3>
                {p.url && <p className="text-xs text-gray-600 font-mono truncate mb-2">{p.url}</p>}
                <div className="mt-2">
                  <p className="text-xs font-medium text-gray-500 mb-1.5">Models ({p.models.length})</p>
                  <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
                    {p.models.map((m) => (
                      <button
                        key={m}
                        onClick={() => setSelectedModel(m)}
                        className={`text-xs px-2 py-0.5 rounded-full border transition-colors cursor-pointer ${
                          selectedModel === m
                            ? 'bg-indigo-600 text-white border-indigo-600'
                            : 'bg-[#12121a] hover:bg-[#1a1a2a] border-[#232333] text-gray-300'
                        }`}
                      >
                        {m}
                      </button>
                    ))}
                  </div>
                </div>
                {h?.error && (
                  <p className="mt-2 text-[11px] text-red-400/80 break-words">{h.error}</p>
                )}
                {h?.hint && (
                  <p className="mt-1 text-[11px] text-amber-400/80 break-words">{h.hint}</p>
                )}
                {p.type === 'ollama' && (
                  <button
                    onClick={() => runDiagnose()}
                    disabled={diagnoseLoading}
                    className="mt-3 btn-secondary text-xs"
                  >
                    {diagnoseLoading ? 'Diagnosing…' : '🔍 Diagnose'}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </section>

      {/* ─── Ollama Diagnostics ──────────────────────────────── */}
      {(diagnoseResult || diagnoseError) && (
        <section className="mb-8">
          <h2 className="section-title">Ollama Diagnostics</h2>
          <div className="card p-5 space-y-3">
            {diagnoseError && <div className="text-sm text-red-400">{diagnoseError}</div>}
            {diagnoseResult && (
              <>
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className={diagnoseResult.ok ? 'text-emerald-400 font-semibold' : 'text-red-400 font-semibold'}>
                    {diagnoseResult.ok ? '✅ reachable' : '❌ not fully reachable'}
                  </span>
                  <span className="font-mono text-gray-400">{diagnoseResult.url}</span>
                  {diagnoseResult.version && <span className="text-gray-500">Ollama {diagnoseResult.version}</span>}
                  {diagnoseResult.model_info?.capabilities && (
                    <span className="text-xs text-gray-500">
                      capabilities: {diagnoseResult.model_info.capabilities.join(', ') || 'none'}
                    </span>
                  )}
                </div>
                <div className="space-y-1">
                  {diagnoseResult.steps.map((s) => (
                    <div key={s.name} className="flex items-start gap-2 text-xs">
                      <span>{s.ok ? '✅' : '❌'}</span>
                      <span className="font-mono text-gray-400 w-24 shrink-0">{s.name}</span>
                      <span className="text-gray-500 flex-1 break-words">
                        {typeof s.detail === 'string' ? s.detail : JSON.stringify(s.detail)}
                      </span>
                      <span className="text-gray-600 shrink-0">{s.latency_ms}ms</span>
                    </div>
                  ))}
                </div>
                {diagnoseResult.hints.map((hint, i) => (
                  <p key={`hint-${i}`} className="text-xs text-amber-400/90">💡 {hint}</p>
                ))}
              </>
            )}
          </div>
        </section>
      )}

      {/* ─── Token Pricing ───────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">Token Pricing</h2>
        <div className="card p-4 space-y-3">
          <p className="text-[11px] text-gray-500">
            Optional per-million-token rates for cost estimates. Token counts are provider-reported; costs are estimates.
          </p>
          <div className="grid gap-2 sm:grid-cols-4">
            <input value={priceModel} onChange={(event) => setPriceModel(event.target.value)} placeholder="model id" className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300" />
            <input value={priceInput} onChange={(event) => setPriceInput(event.target.value)} placeholder="input $/1M" type="number" step="0.0001" className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300" />
            <input value={priceOutput} onChange={(event) => setPriceOutput(event.target.value)} placeholder="output $/1M" type="number" step="0.0001" className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-xs text-gray-300" />
            <button onClick={saveModelPrice} className="btn-primary text-xs">Save pricing</button>
          </div>
          {priceMsg && <div className="text-xs text-gray-400">{priceMsg}</div>}
          {modelPrices.length > 0 && (
            <div className="space-y-1 text-[10px] text-gray-500">
              {modelPrices.map((row) => (
                <div key={row.model} className="flex flex-wrap gap-3">
                  <span className="font-mono text-gray-300">{row.model}</span>
                  <span>in {row.input_per_million}/1M</span>
                  <span>out {row.output_per_million}/1M</span>
                  <span>{row.currency || 'USD'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* ─── API Keys ────────────────────────────────────────── */}
      <section className="mb-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="section-title mb-0">API Keys</h2>
          <button
            onClick={() => { setShowConfig(!showConfig); if (!showConfig) fetchConfig() }}
            className="btn-secondary text-xs"
          >
            {showConfig ? 'Hide' : '⚙ Configure'}
          </button>
        </div>

        {!showConfig && (
          <div className="flex gap-2 text-sm">
            {(['gemini', 'anthropic', 'openai', 'deepseek'] as const).map((p) => (
              <span
                key={p}
                className={`px-2.5 py-1 rounded-full border text-xs font-medium capitalize ${
                  configStatus[p]
                    ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                    : 'bg-[#12121a] border-[#232333] text-gray-600'
                }`}
              >
                {configStatus[p] ? '🔑' : '⚪'} {p}
              </span>
            ))}
          </div>
        )}

        {showConfig && (
          <div className="card p-5 space-y-4 animate-fade-in">
            {configMsg && (
              <div className={`p-3 rounded-lg text-sm ${
                configMsg.startsWith('✓')
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : 'bg-red-500/10 border border-red-500/20 text-red-400'
              }`}>
                {configMsg}
              </div>
            )}

            {/* Ollama URL */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">🦙 Ollama URL</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={configOllamaUrl}
                  onChange={(e) => setConfigOllamaUrl(e.target.value)}
                  placeholder="http://host.docker.internal:11434"
                  className="input-field flex-1 font-mono text-sm"
                />
                <button onClick={() => saveConfig('ollama_url', configOllamaUrl)} disabled={savingConfig} className="btn-primary text-xs">
                  Save
                </button>
                <button onClick={() => runDiagnose(configOllamaUrl)} disabled={diagnoseLoading} className="btn-secondary text-xs">
                  Test
                </button>
              </div>
              <p className="text-xs text-gray-600 mt-1">
                WSL2: use the Windows LAN IP (e.g. <code>http://192.168.1.20:11434</code>) or run{' '}
                <code>make set-ollama-url</code> — <code>host.docker.internal</code> is the WSL VM.
              </p>
            </div>

            {/* Gemini */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                ✨ Gemini API Key
                <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener" className="ml-2 text-indigo-400 hover:text-indigo-300">Get key →</a>
              </label>
              <div className="flex gap-2">
                <input type="password" value={configGeminiKey} onChange={(e) => setConfigGeminiKey(e.target.value)} placeholder={configStatus.gemini ? '••••••••  (configured)' : 'Paste API key'} className="input-field flex-1 font-mono text-sm" />
                <button onClick={() => saveConfig('gemini_api_key', configGeminiKey)} disabled={savingConfig} className="btn-primary text-xs">Save</button>
                {configStatus.gemini && (
                  <button onClick={() => { setConfigGeminiKey(''); saveConfig('gemini_api_key', '') }} disabled={savingConfig} className="btn-danger text-xs">✗</button>
                )}
              </div>
            </div>

            {/* Anthropic */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                🧠 Anthropic API Key
                <a href="https://console.anthropic.com/" target="_blank" rel="noopener" className="ml-2 text-indigo-400 hover:text-indigo-300">Get key →</a>
              </label>
              <div className="flex gap-2">
                <input type="password" value={configAnthropicKey} onChange={(e) => setConfigAnthropicKey(e.target.value)} placeholder={configStatus.anthropic ? '••••••••  (configured)' : 'Paste API key'} className="input-field flex-1 font-mono text-sm" />
                <button onClick={() => saveConfig('anthropic_api_key', configAnthropicKey)} disabled={savingConfig} className="btn-primary text-xs">Save</button>
                {configStatus.anthropic && (
                  <button onClick={() => { setConfigAnthropicKey(''); saveConfig('anthropic_api_key', '') }} disabled={savingConfig} className="btn-danger text-xs">✗</button>
                )}
              </div>
            </div>

            {/* OpenAI */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                🤖 OpenAI API Key
                <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener" className="ml-2 text-indigo-400 hover:text-indigo-300">Get key →</a>
              </label>
              <div className="flex gap-2">
                <input type="password" value={configOpenaiKey} onChange={(e) => setConfigOpenaiKey(e.target.value)} placeholder={configStatus.openai ? '••••••••  (configured)' : 'Paste API key'} className="input-field flex-1 font-mono text-sm" />
                <button onClick={() => saveConfig('openai_api_key', configOpenaiKey)} disabled={savingConfig} className="btn-primary text-xs">Save</button>
                {configStatus.openai && (
                  <button onClick={() => { setConfigOpenaiKey(''); saveConfig('openai_api_key', '') }} disabled={savingConfig} className="btn-danger text-xs">✗</button>
                )}
              </div>
            </div>

            {/* DeepSeek */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">
                🔵 DeepSeek API Key
                <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener" className="ml-2 text-indigo-400 hover:text-indigo-300">Get key →</a>
              </label>
              <div className="flex gap-2">
                <input type="password" value={configDeepseekKey} onChange={(e) => setConfigDeepseekKey(e.target.value)} placeholder={configStatus.deepseek ? '••••••••  (configured)' : 'Paste API key'} className="input-field flex-1 font-mono text-sm" />
                <button onClick={() => saveConfig('deepseek_api_key', configDeepseekKey)} disabled={savingConfig} className="btn-primary text-xs">Save</button>
                {configStatus.deepseek && (
                  <button onClick={() => { setConfigDeepseekKey(''); saveConfig('deepseek_api_key', '') }} disabled={savingConfig} className="btn-danger text-xs">✗</button>
                )}
              </div>
            </div>

            <p className="text-xs text-gray-600 mt-2">
              Keys are stored in-memory on the control-plane and take effect immediately. They do not persist across container restarts.
            </p>
          </div>
        )}
      </section>

      {/* ─── DAG Model Defaults ──────────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">DAG Model Defaults</h2>
        <p className="text-xs text-gray-500 mb-3">
          Choose which LLM is used for DAG planning (decomposing objectives) and which runs inside each agent container.
        </p>
        <div className="card p-5 space-y-4">
          {dagDefaultsMsg && (
            <div className={`p-3 rounded-lg text-sm ${
              dagDefaultsMsg.startsWith('✓')
                ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                : 'bg-red-500/10 border border-red-500/20 text-red-400'
            }`}>
              {dagDefaultsMsg}
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">📋 Planning Model</label>
              <select
                value={dagPlanningModel}
                onChange={(e) => setDagPlanningModel(e.target.value)}
                className="input-field w-full text-sm"
              >
                {allModels.map((m) => (
                  <option key={`plan-${m.provider}-${m.id}`} value={m.id}>{m.id}</option>
                ))}
                {dagPlanningModel && !allModels.find(m => m.id === dagPlanningModel) && (
                  <option value={dagPlanningModel}>{dagPlanningModel}</option>
                )}
              </select>
              <p className="text-xs text-gray-600 mt-1">Decomposes objectives into DAG task graphs</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">🤖 Agent Model</label>
              <select
                value={dagAgentModel}
                onChange={(e) => setDagAgentModel(e.target.value)}
                className="input-field w-full text-sm"
              >
                {allModels.map((m) => (
                  <option key={`agent-${m.provider}-${m.id}`} value={m.id}>{m.id}</option>
                ))}
                {dagAgentModel && !allModels.find(m => m.id === dagAgentModel) && (
                  <option value={dagAgentModel}>{dagAgentModel}</option>
                )}
              </select>
              <p className="text-xs text-gray-600 mt-1">Runs inside each agent container for task execution</p>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">🔍 Deep Review / Skill-Learning Model</label>
              <select
                value={dagDeepReviewModel}
                onChange={(e) => setDagDeepReviewModel(e.target.value)}
                className="input-field w-full text-sm"
              >
                {allModels.map((m) => (
                  <option key={`review-${m.provider}-${m.id}`} value={m.id}>{m.id}</option>
                ))}
                {dagDeepReviewModel && !allModels.find(m => m.id === dagDeepReviewModel) && (
                  <option value={dagDeepReviewModel}>{dagDeepReviewModel}</option>
                )}
              </select>
              <p className="text-xs text-gray-600 mt-1">Used for examining logs, hallucination/synthetic-data audits, and skill extraction</p>
            </div>
            <div className="md:col-span-2">
              <label className="block text-xs font-medium text-gray-400 mb-1.5">🧪 Decision-Model Triage (pre-screen)</label>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[11px] text-gray-500 mb-1">Mode</label>
                  <select
                    value={dagTriageEnabled ? 'true' : 'false'}
                    onChange={(e) => setDagTriageEnabled(e.target.value === 'true')}
                    className="input-field w-full text-sm"
                  >
                    <option value="true">enabled</option>
                    <option value="false">disabled</option>
                  </select>
                </div>
                <div className="md:col-span-2">
                  <label className="block text-[11px] text-gray-500 mb-1">Ollama decision model</label>
                  <input
                    value={dagTriageModel}
                    onChange={(e) => setDagTriageModel(e.target.value)}
                    placeholder="kev:0.8b-q8_0"
                    className="input-field w-full text-sm font-mono"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-gray-500 mb-1">Escalate threshold (p)</label>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.05"
                    value={dagTriageThreshold}
                    onChange={(e) => setDagTriageThreshold(e.target.value)}
                    className="input-field w-full text-sm"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-gray-500 mb-1">Deep-review sample rate</label>
                  <input
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    value={dagTriageSampleRate}
                    onChange={(e) => setDagTriageSampleRate(e.target.value)}
                    className="input-field w-full text-sm"
                  />
                </div>
                <div className="flex items-end">
                  <span className="text-[11px] text-gray-600">
                    provider: <span className="font-mono">{dagTriageProvider}</span> · /v1/systemone
                  </span>
                </div>
              </div>
              <p className="text-xs text-gray-600 mt-1">
                Clean outputs skip the deep review; uncertain or unavailable triage escalates. Requires Ollama &gt;= 0.40.4
                and <code className="text-gray-400">ollama pull {dagTriageModel || 'kev:0.8b-q8_0'}</code>. Triage never fails a node.
              </p>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">🧠 Agent Context Management</label>
              <select
                value={dagContextMode}
                onChange={(e) => setDagContextMode(e.target.value)}
                className="input-field w-full text-sm"
              >
                <option value="none">none (no context management)</option>
                <option value="linear">linear (trim + summarize old turns)</option>
                <option value="graph">graph (short-term memory + compressed context graph)</option>
              </select>
              <p className="text-xs text-gray-600 mt-1">none replays the full history each turn (cost grows quadratically); linear folds old turns into a summary; graph keeps ~5 recent turns raw and compresses everything older into a JSON context graph.</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={saveDagModelDefaults} disabled={savingDagDefaults} className="btn-primary text-sm">
              {savingDagDefaults ? 'Saving…' : 'Save Defaults'}
            </button>
            <span className="text-xs text-gray-600">
              Current: <span className="font-mono text-gray-400">{dagPlanningModel}</span> / <span className="font-mono text-gray-400">{dagAgentModel}</span> / review: <span className="font-mono text-gray-400">{dagDeepReviewModel}</span> / context: <span className="font-mono text-gray-400">{dagContextMode}</span>
            </span>
          </div>
        </div>
      </section>

      {/* ─── All Models ──────────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">
          All Available Models <span className="text-sm font-normal text-gray-600">({allModels.length})</span>
        </h2>
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#1a1a2a]">
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Model</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Provider</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1a1a2a]">
              {allModels.map((m) => (
                <tr key={`${m.provider}-${m.id}`} className="hover:bg-[#12121a] transition-colors">
                  <td className="px-4 py-2.5 font-mono text-gray-300 text-sm">{m.id}</td>
                  <td className="px-4 py-2.5">
                    <span className="inline-flex items-center gap-1.5 text-gray-400">
                      {providerIcon(m.provider)} {m.provider}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    <button
                      onClick={() => setSelectedModel(m.id)}
                      className={`text-xs px-3 py-1 rounded-md transition-colors ${
                        selectedModel === m.id
                          ? 'bg-indigo-600 text-white'
                          : 'bg-[#12121a] hover:bg-[#1a1a2a] text-gray-400 border border-[#232333]'
                      }`}
                    >
                      {selectedModel === m.id ? '✓ Selected' : 'Select'}
                    </button>
                  </td>
                </tr>
              ))}
              {allModels.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-4 py-8 text-center text-gray-600 text-sm">
                    No models available. Configure at least one provider.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ─── Chat Playground ─────────────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">Playground</h2>
        <div className="card overflow-hidden">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-4 p-4 bg-[#0d0d14] border-b border-[#1a1a2a]">
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">Model</label>
              <input
                list="llm-model-options"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                placeholder="model (e.g. gemma3:4b, kev:0.8b-q8_0)"
                className="input-field text-sm py-1 px-2 font-mono w-64"
              />
              <datalist id="llm-model-options">
                {allModels.map((m) => (
                  <option key={`${m.provider}-${m.id}`} value={m.id} />
                ))}
              </datalist>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">Temp</label>
              <input type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} className="input-field text-sm py-1 px-2 w-20" />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">Max tokens</label>
              <input type="number" min={64} max={32000} step={64} value={maxTokens} onChange={(e) => setMaxTokens(parseInt(e.target.value))} className="input-field text-sm py-1 px-2 w-24" />
            </div>
            <div className="ml-auto flex items-center gap-2">
              <button onClick={testChat} disabled={sending} className="btn-secondary text-xs">
                Test chat
              </button>
              <button onClick={() => setMessages([])} className="btn-secondary text-xs">
                Clear chat
              </button>
            </div>
          </div>

          {/* Messages */}
          <div className="h-96 overflow-y-auto p-4 space-y-4 bg-[#0a0a12]">
            {messages.length === 0 && (
              <div className="flex items-center justify-center h-full text-gray-600 text-sm">
                Send a message to test the LLM router
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[80%] rounded-xl px-4 py-3 ${
                  m.role === 'user'
                    ? 'bg-indigo-600/30 border border-indigo-500/30 text-white'
                    : 'bg-[#12121a] border border-[#232333] text-gray-200'
                }`}>
                  <div className="whitespace-pre-wrap text-sm">{m.content}</div>
                  {m.role === 'assistant' && (m.model || m.tokens || m.latencyMs) && (
                    <div className="mt-2 pt-2 border-t border-[#232333] text-xs text-gray-600 flex flex-wrap gap-3">
                      {m.model && <span>model: {m.model}</span>}
                      {m.provider && <span>via {m.provider}</span>}
                      {m.tokens != null && <span>{m.tokens} tokens</span>}
                      {m.latencyMs != null && <span>{(m.latencyMs / 1000).toFixed(1)}s</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="bg-[#12121a] border border-[#232333] rounded-xl px-4 py-3">
                  <div className="flex items-center gap-2 text-sm text-gray-500">
                    <span className="animate-pulse">●</span> Thinking…
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="p-4 border-t border-[#1a1a2a] bg-[#0d0d14]">
            <form onSubmit={(e) => { e.preventDefault(); sendMessage() }} className="flex gap-2">
              <input
                type="text"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Type a message…"
                className="input-field flex-1"
                disabled={sending}
              />
              <button
                type="submit"
                disabled={sending || !prompt.trim() || !selectedModel}
                className="btn-primary text-sm"
              >
                Send
              </button>
            </form>
          </div>
        </div>
      </section>

      {/* ─── Decision Model (System One) ─────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">Decision Model (System One)</h2>
        <div className="card p-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="md:col-span-2">
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Model</label>
              <input
                value={decisionModel}
                onChange={(e) => setDecisionModel(e.target.value)}
                placeholder="kev:0.8b-q8_0"
                className="input-field w-full font-mono text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Question type</label>
              <select
                value={decisionType}
                onChange={(e) => setDecisionType(e.target.value as 'noul' | 'choice' | 'score')}
                className="input-field w-full text-sm"
              >
                <option value="noul">noul — yes/no probability</option>
                <option value="choice">choice — pick one</option>
                <option value="score">score — rubric level</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">Instructions</label>
            <input
              value={decisionInstructions}
              onChange={(e) => setDecisionInstructions(e.target.value)}
              className="input-field w-full text-sm"
            />
          </div>

          {decisionType === 'choice' && (
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Options (comma-separated)</label>
              <input
                value={decisionChoiceOptions}
                onChange={(e) => setDecisionChoiceOptions(e.target.value)}
                className="input-field w-full text-sm font-mono"
              />
            </div>
          )}
          {decisionType === 'score' && (
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Levels, lowest first (comma-separated)</label>
              <input
                value={decisionScoreLevels}
                onChange={(e) => setDecisionScoreLevels(e.target.value)}
                className="input-field w-full text-sm font-mono"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">State (text to judge)</label>
            <textarea
              rows={4}
              value={decisionState}
              onChange={(e) => setDecisionState(e.target.value)}
              className="input-field w-full text-sm font-mono"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button onClick={runDecision} disabled={decisionLoading} className="btn-primary text-sm">
              {decisionLoading ? 'Running…' : 'Run decision'}
            </button>
            <button
              onClick={() => setDecisionModel(dagTriageModel || 'kev:0.8b-q8_0')}
              className="btn-secondary text-xs"
            >
              Use triage model
            </button>
            {decisionResult?._meta && (
              <span className="text-xs text-gray-500">
                {decisionResult._meta.latency_ms}ms · {decisionResult._meta.url}
              </span>
            )}
          </div>

          {decisionError && <div className="text-sm text-red-400">{decisionError}</div>}

          {decisionResult && (
            <div className="bg-[#0d0d14] border border-[#1a1a2a] rounded-lg p-4 space-y-2">
              <div className="text-xs text-gray-500">
                model: <span className="font-mono">{decisionResult.model}</span>
              </div>
              {decisionType === 'noul' && (
                <>
                  <div className="text-sm text-gray-200">
                    P(true) = <span className="font-mono text-indigo-300">{decisionAnswer.noul ?? 'n/a'}</span>
                  </div>
                  {decisionAnswer.noul != null && (
                    <div className={`text-sm font-semibold ${Number(decisionAnswer.noul) >= triageThreshold ? 'text-red-400' : 'text-emerald-400'}`}>
                      Triage verdict at threshold {triageThreshold}:{' '}
                      {Number(decisionAnswer.noul) >= triageThreshold
                        ? 'ESCALATE (run deep review)'
                        : 'PASS (skip deep review)'}
                    </div>
                  )}
                </>
              )}
              {decisionType === 'choice' && (
                <>
                  <div className="text-sm text-gray-200">
                    choice: <span className="font-mono text-indigo-300">{decisionAnswer.choice ?? 'n/a'}</span>
                    {' '}· confidence: {decisionAnswer.confidence ?? 'n/a'}
                  </div>
                  <pre className="text-xs text-gray-400 overflow-x-auto">
                    {JSON.stringify(decisionAnswer.probabilities ?? {}, null, 2)}
                  </pre>
                </>
              )}
              {decisionType === 'score' && (
                <>
                  <div className="text-sm text-gray-200">
                    score: <span className="font-mono text-indigo-300">{decisionAnswer.score ?? 'n/a'}</span>
                    {' '}· confidence: {decisionAnswer.confidence ?? 'n/a'}
                  </div>
                  <pre className="text-xs text-gray-400 overflow-x-auto">
                    {JSON.stringify(decisionAnswer.probabilities ?? {}, null, 2)}
                  </pre>
                </>
              )}
              <details>
                <summary className="text-xs text-gray-500 cursor-pointer">Raw response</summary>
                <pre className="mt-2 text-xs text-gray-400 overflow-x-auto">
                  {JSON.stringify(decisionResult, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      </section>

      {/* ─── Triage on a Recent Task ─────────────────────────── */}
      <section className="mb-8">
        <h2 className="section-title">Triage on a Recent Task</h2>
        <div className="card p-5 space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[280px]">
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Task</label>
              <select
                value={triageTaskId}
                onChange={(e) => setTriageTaskId(e.target.value)}
                className="input-field w-full text-sm font-mono"
              >
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.id} — {t.status}
                  </option>
                ))}
              </select>
            </div>
            <button onClick={fetchTasks} className="btn-secondary text-xs">Refresh</button>
            <button
              onClick={runTriage}
              disabled={triageLoading || !triageTaskId}
              className="btn-primary text-xs"
            >
              {triageLoading ? 'Running…' : 'Run triage'}
            </button>
          </div>
          {triageError && <div className="text-sm text-red-400">{triageError}</div>}
          {triageResult && (
            <div className="bg-[#0d0d14] border border-[#1a1a2a] rounded-lg p-4 text-sm space-y-1">
              <div className={triageResult.decision === 'pass' ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>
                {triageResult.decision === 'pass'
                  ? 'PASS — deep review skipped'
                  : 'ESCALATE — deep review runs'}
              </div>
              <div className="text-xs text-gray-500">
                reason: {triageResult.reason} · p_escalate: {triageResult.p_escalate ?? 'n/a'} ·
                model: {triageResult.model ?? 'n/a'} · {triageResult.latency_ms}ms
                {triageResult.sampled ? ' · sampled' : ''}
              </div>
              {triageResult.features?.red_flags && triageResult.features.red_flags.length > 0 && (
                <div className="text-xs text-red-400">
                  red flags: {triageResult.features.red_flags.join(', ')}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* ─── API Reference ───────────────────────────────────── */}
      <section>
        <h2 className="section-title">API Reference</h2>
        <div className="card p-5 space-y-4">
          <div>
            <h3 className="text-sm font-semibold text-white mb-2">Chat Completions (OpenAI-compatible)</h3>
            <pre className="bg-[#0a0a12] text-emerald-400 rounded-lg p-4 overflow-x-auto text-xs font-mono">
{`POST /api/llm/v1/chat/completions
Content-Type: application/json

{
  "model": "gemma3:4b",
  "messages": [{"role": "user", "content": "Hello"}],
  "temperature": 0.7,
  "max_tokens": 2048
}`}
            </pre>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {[
              { endpoint: 'GET /api/llm/providers', desc: 'List providers + models' },
              { endpoint: 'GET /api/llm/models', desc: 'Flat list of all models' },
              { endpoint: 'GET /api/llm/health', desc: 'Provider health status' },
              { endpoint: 'GET /api/llm/diagnose', desc: 'Ollama connectivity + capability steps' },
              { endpoint: 'POST /api/llm/systemone', desc: 'Proxy a decision-model request' },
            ].map((e) => (
              <div key={e.endpoint} className="bg-[#0d0d14] border border-[#1a1a2a] rounded-lg p-3">
                <code className="text-xs font-mono text-indigo-400">{e.endpoint}</code>
                <p className="text-xs text-gray-600 mt-1">{e.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}
