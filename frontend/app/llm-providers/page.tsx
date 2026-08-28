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
}

interface ProviderHealth {
  status: string
  url?: string
  error?: string
  models: string[]
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

  // DAG model defaults
  const [dagPlanningModel, setDagPlanningModel] = useState('')
  const [dagAgentModel, setDagAgentModel] = useState('')
  const [dagDeepReviewModel, setDagDeepReviewModel] = useState('')
  const [dagContextMode, setDagContextMode] = useState('none')
  const [savingDagDefaults, setSavingDagDefaults] = useState(false)
  const [dagDefaultsMsg, setDagDefaultsMsg] = useState<string | null>(null)

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
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setDagPlanningModel(data.planning_model)
      setDagAgentModel(data.agent_model)
      setDagDeepReviewModel(data.deep_review_model)
      setDagContextMode(data.agent_context_mode ?? 'none')
      setDagDefaultsMsg(`✓ Defaults saved — planning: ${data.planning_model}, agents: ${data.agent_model}, deep-review: ${data.deep_review_model}, context-mode: ${data.agent_context_mode}`)
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

  // ─── Render ────────────────────────────────────────────────────

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
              </div>
            )
          })}
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
              </div>
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
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="input-field text-sm py-1 px-2"
              >
                {allModels.map((m) => (
                  <option key={`${m.provider}-${m.id}`} value={m.id}>
                    {m.id}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">Temp</label>
              <input type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} className="input-field text-sm py-1 px-2 w-20" />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-gray-500">Max tokens</label>
              <input type="number" min={64} max={32000} step={64} value={maxTokens} onChange={(e) => setMaxTokens(parseInt(e.target.value))} className="input-field text-sm py-1 px-2 w-24" />
            </div>
            <button onClick={() => setMessages([])} className="ml-auto btn-secondary text-xs">
              Clear chat
            </button>
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
