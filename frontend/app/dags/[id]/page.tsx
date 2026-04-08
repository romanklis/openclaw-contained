'use client'

import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import { StatusBadge } from '../../components/StatusComponents'
import { API, TEMPORAL_UI } from '../../lib/api'

interface DAGNode {
  id: number
  dag_id: string
  node_id: string
  skill_id: string | null
  description: string | null
  status: string
  depends_on: string[]
  config: Record<string, any>
  input_mapping: Record<string, any>
  output_data: any
  task_id: string | null
  container_id: string | null
  started_at: string | null
  completed_at: string | null
}

interface DAGDetail {
  id: string
  objective: string
  status: string
  workspace_id: string
  llm_model: string
  workflow_id: string | null
  dag_json: any
  nodes: DAGNode[]
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export default function DAGDetailPage() {
  const params = useParams()
  const dagId = params.id as string
  const [dag, setDag] = useState<DAGDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchDag = async () => {
    try {
      const res = await fetch(`${API}/api/dags/${dagId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setDag(await res.json())
    } catch (err: any) {
      setError(err.message)
    }
  }

  useEffect(() => { fetchDag() }, [dagId])

  // Auto-refresh while running
  useEffect(() => {
    if (dag?.status === 'running') {
      const interval = setInterval(fetchDag, 5000)
      return () => clearInterval(interval)
    }
  }, [dag?.status])

  const startDag = async () => {
    try {
      await fetch(`${API}/api/dags/${dagId}/start`, { method: 'POST' })
      fetchDag()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const cancelDag = async () => {
    try {
      await fetch(`${API}/api/dags/${dagId}/cancel`, { method: 'POST' })
      fetchDag()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const getWorkflowLink = (workflowId: string) => {
    return `${TEMPORAL_UI}/namespaces/default/workflows/${encodeURIComponent(workflowId)}`
  }

  const getNodeWorkflowLink = (nodeId: string) => {
    const workflowId = `dag-node-${dagId}-${nodeId}`
    return getWorkflowLink(workflowId)
  }

  if (error) return <div className="text-red-400">Error: {error}</div>
  if (!dag) return <div className="text-gray-500">Loading...</div>

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <span className="font-mono">{dag.id}</span>
            <StatusBadge status={dag.status} />
          </h1>
          <p className="text-gray-400 mt-1">{dag.objective}</p>
        </div>
        <div className="flex gap-2">
          {dag.status === 'ready' && (
            <button onClick={startDag} className="btn-success text-sm">▶ Start</button>
          )}
          {dag.status === 'running' && (
            <button onClick={cancelDag} className="btn-danger text-sm">⏹ Cancel</button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="card">
          <div className="text-xs text-gray-500 mb-1">LLM Model</div>
          <div className="text-sm font-mono">{dag.llm_model}</div>
        </div>
        <div className="card">
          <div className="text-xs text-gray-500 mb-1">Workspace</div>
          <div className="text-sm font-mono">{dag.workspace_id}</div>
        </div>
        <div className="card">
          <div className="text-xs text-gray-500 mb-1">Workflow</div>
          <div className="text-sm font-mono break-all">{dag.workflow_id || '—'}</div>
          {dag.workflow_id && (
            <a
              href={getWorkflowLink(dag.workflow_id)}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-400 hover:text-blue-300"
            >
              Open in Temporal
            </a>
          )}
        </div>
      </div>

      <h2 className="text-lg font-semibold mb-3">Nodes ({dag.nodes.length})</h2>
      <div className="space-y-3">
        {dag.nodes.map((node) => (
          <div key={node.node_id} className="card">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm font-semibold">{node.node_id}</span>
                <StatusBadge status={node.status} />
                {node.skill_id && (
                  <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded">
                    skill: {node.skill_id}
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-500">
                {node.task_id && <span className="font-mono">task: {node.task_id}</span>}
                <div>
                  <a
                    href={getNodeWorkflowLink(node.node_id)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-400 hover:text-blue-300"
                  >
                    Node workflow
                  </a>
                </div>
              </div>
            </div>
            {node.description && <p className="text-sm text-gray-400 mb-2">{node.description}</p>}
            {node.depends_on.length > 0 && (
              <div className="text-xs text-gray-500">
                Depends on: {node.depends_on.join(', ')}
              </div>
            )}
            {node.output_data && (
              <details className="mt-2">
                <summary className="text-xs text-gray-500 cursor-pointer">Output</summary>
                <pre className="text-xs bg-gray-900 rounded p-2 mt-1 overflow-auto max-h-40">
                  {JSON.stringify(node.output_data, null, 2)}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>

      {dag.dag_json && (
        <details className="mt-6">
          <summary className="text-sm text-gray-500 cursor-pointer">Raw DAG JSON</summary>
          <pre className="card text-xs overflow-auto max-h-96 mt-2">
            {JSON.stringify(dag.dag_json, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
