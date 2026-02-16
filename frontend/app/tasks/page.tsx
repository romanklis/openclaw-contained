'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { StatusDot, StatusBadge } from '../components/StatusComponents'
import { API } from '../lib/api'

interface Task {
  id: string
  name: string
  description: string
  status: string
  workspace_id: string | null
  workflow_id: string | null
  created_at: string
  updated_at: string
}

interface Deployment {
  id: string
  name: string
  task_id: string
  status: string
  host_port: number | null
  url: string | null
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [deployments, setDeployments] = useState<Record<string, Deployment[]>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>('all')

  // Form state
  const [taskName, setTaskName] = useState('')
  const [taskDescription, setTaskDescription] = useState('')
  const [llmModel, setLlmModel] = useState('gemini-flash-latest')
  const [maxIterations, setMaxIterations] = useState(10)
  const [availableModels, setAvailableModels] = useState<{ id: string; provider: string }[]>([])

  useEffect(() => {
    fetch(`${API}/api/llm/models`)
      .then((r) => r.json())
      .then((data) => {
        const models = data.models || []
        setAvailableModels(models)
        if (models.length > 0) setLlmModel(models[0].id)
      })
      .catch(() => {})
  }, [])

  const fetchTasks = async () => {
    try {
      const [tasksRes, deploysRes] = await Promise.all([
        fetch(`${API}/api/tasks`).then((r) => r.json()),
        fetch(`${API}/api/deployments`).then((r) => r.json()).catch(() => []),
      ])
      setTasks(Array.isArray(tasksRes) ? tasksRes : [])

      // Group deployments by task
      const dMap: Record<string, Deployment[]> = {}
      for (const d of (Array.isArray(deploysRes) ? deploysRes : [])) {
        if (!dMap[d.task_id]) dMap[d.task_id] = []
        dMap[d.task_id].push(d)
      }
      setDeployments(dMap)
    } catch (err) {
      console.error('Error fetching tasks:', err)
    }
  }

  useEffect(() => {
    fetchTasks()
    const interval = setInterval(fetchTasks, 5000)
    return () => clearInterval(interval)
  }, [])

  const createTask = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`${API}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: taskName,
          description: taskDescription,
          model: llmModel,
          agent_config: { max_iterations: maxIterations, timeout: 600 },
        }),
      })
      if (!response.ok) throw new Error('Failed to create task')
      setTaskName('')
      setTaskDescription('')
      setShowForm(false)
      fetchTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const filteredTasks = statusFilter === 'all'
    ? tasks
    : tasks.filter((t) => t.status === statusFilter)

  const statusCounts = {
    all: tasks.length,
    running: tasks.filter((t) => t.status === 'running').length,
    completed: tasks.filter((t) => t.status === 'completed').length,
    failed: tasks.filter((t) => t.status === 'failed').length,
  }

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">Tasks</h1>
          <p className="text-sm text-gray-500">Create and monitor agent tasks</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className={showForm ? 'btn-secondary text-sm' : 'btn-primary text-sm'}
        >
          {showForm ? 'Cancel' : '+ New Task'}
        </button>
      </div>

      {/* Create Form */}
      {showForm && (
        <div className="card p-6 mb-6 animate-fade-in">
          <h2 className="font-semibold text-white mb-4">Create New Task</h2>
          <form onSubmit={createTask} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Task Name</label>
              <input
                type="text"
                value={taskName}
                onChange={(e) => setTaskName(e.target.value)}
                className="input-field"
                placeholder="e.g., Build Fibonacci API"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1.5">Description</label>
              <textarea
                value={taskDescription}
                onChange={(e) => setTaskDescription(e.target.value)}
                className="input-field"
                rows={4}
                placeholder="Describe what the agent should build..."
                required
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">LLM Model</label>
                <select
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                  className="input-field"
                >
                  {availableModels.map((m) => (
                    <option key={`${m.provider}-${m.id}`} value={m.id}>
                      {m.id} ({m.provider})
                    </option>
                  ))}
                  {availableModels.length === 0 && (
                    <option value="gemini-flash-latest">gemini-flash-latest</option>
                  )}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Max Iterations</label>
                <input
                  type="number"
                  value={maxIterations}
                  onChange={(e) => setMaxIterations(parseInt(e.target.value))}
                  className="input-field"
                  min="1"
                  max="30"
                />
              </div>
            </div>
            {error && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm rounded-lg p-3">
                {error}
              </div>
            )}
            <button type="submit" disabled={loading} className="btn-success text-sm">
              {loading ? 'Creating...' : 'Create & Start Task'}
            </button>
          </form>
        </div>
      )}

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-4 bg-[#12121a] rounded-lg p-1 w-fit">
        {(['all', 'running', 'completed', 'failed'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors capitalize ${
              statusFilter === f
                ? 'bg-[#232333] text-white'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {f} {statusCounts[f] > 0 && `(${statusCounts[f]})`}
          </button>
        ))}
      </div>

      {/* Task List */}
      <div className="space-y-3">
        {filteredTasks.length === 0 ? (
          <div className="card p-12 text-center">
            <div className="text-4xl mb-3">📋</div>
            <p className="text-gray-500 text-sm">
              {statusFilter === 'all' ? 'No tasks yet. Create one to get started!' : `No ${statusFilter} tasks`}
            </p>
          </div>
        ) : (
          filteredTasks.map((task) => {
            const taskDeployments = deployments[task.id] || []
            return (
              <div key={task.id} className="card card-hover p-5 animate-fade-in">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <StatusDot status={task.status} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <Link
                          href={`/tasks/${task.id}`}
                          className="font-medium text-white hover:text-indigo-400 transition-colors"
                        >
                          {task.name}
                        </Link>
                        <StatusBadge status={task.status} />
                      </div>
                      <p className="text-sm text-gray-500 mb-2 line-clamp-2">{task.description}</p>
                      <div className="flex items-center gap-3 text-xs text-gray-600">
                        <span className="font-mono">{task.id}</span>
                        <span>•</span>
                        <span>{new Date(task.created_at).toLocaleString()}</span>
                        {task.workflow_id && (
                          <>
                            <span>•</span>
                            <a
                              href={`http://localhost:8088/namespaces/default/workflows/${task.workflow_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-indigo-500 hover:text-indigo-400"
                            >
                              Temporal ↗
                            </a>
                          </>
                        )}
                      </div>

                      {/* Deployment badges */}
                      {taskDeployments.length > 0 && (
                        <div className="flex items-center gap-2 mt-2 flex-wrap">
                          <span className="text-xs text-gray-600">Deployments:</span>
                          {taskDeployments.map((d) => (
                            <Link
                              key={d.id}
                              href="/deployments"
                              className="inline-flex items-center gap-1.5 text-xs bg-[#12121a] border border-[#232333] rounded-lg px-2 py-1 hover:border-[#2d2d44] transition-colors"
                            >
                              <StatusDot status={d.status} />
                              <span className="text-gray-300">{d.name}</span>
                              {d.status === 'running' && d.host_port && (
                                <span className="text-emerald-400 font-mono">:{d.host_port}</span>
                              )}
                            </Link>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  <Link
                    href={`/tasks/${task.id}`}
                    className="btn-secondary text-xs shrink-0"
                  >
                    Details →
                  </Link>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
