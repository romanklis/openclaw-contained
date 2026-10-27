'use client'

import { useMemo, useState } from 'react'
import { StatusBadge } from './StatusComponents'

interface DAGTreeProps {
  nodes: any[]
  selectedNodeId?: string | null
  onSelectNode: (nodeId: string) => void
  deepReviews?: Record<string, any>
  turnCounts?: Record<string, number>
}

function shortSkill(value?: string | null) {
  if (!value) return ''
  return value.length > 16 ? `${value.slice(0, 16)}…` : value
}

function deliverablesCount(node: any) {
  const keys =
    node.deliverables_keys ||
    node.output_data?.deliverables_keys ||
    Object.keys(node.output_data?.deliverables || {})
  return Array.isArray(keys) ? keys.length : 0
}

export default function DAGTree({
  nodes,
  selectedNodeId,
  onSelectNode,
  deepReviews = {},
  turnCounts = {},
}: DAGTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')

  const byId = useMemo(() => {
    const map = new Map<string, any>()
    for (const node of nodes) map.set(node.node_id, node)
    return map
  }, [nodes])

  const children = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const node of nodes) map.set(node.node_id, [])
    for (const node of nodes) {
      for (const dep of node.depends_on || []) {
        if (map.has(dep)) map.get(dep)!.push(node.node_id)
      }
    }
    return map
  }, [nodes])

  const roots = useMemo(
    () =>
      nodes
        .filter((node) => (node.depends_on || []).every((dep: string) => !byId.has(dep)))
        .map((node) => node.node_id),
    [nodes, byId],
  )

  const filtered = query.trim()
  const matches = (node: any) => {
    if (!filtered) return true
    const q = filtered.toLowerCase()
    return (
      String(node.node_id).toLowerCase().includes(q) ||
      String(node.description || '').toLowerCase().includes(q) ||
      String(node.config?.base_image || '').toLowerCase().includes(q) ||
      String(node.selected_skill_v2_id || '').toLowerCase().includes(q)
    )
  }

  const toggle = (nodeId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }

  const expandAll = () => setExpanded(new Set(nodes.map((node) => node.node_id)))
  const collapseAll = () => setExpanded(new Set())

  const visibleChildren = (nodeId: string) => (children.get(nodeId) || []).filter((id) => byId.has(id))

  const renderNode = (nodeId: string, depth: number, path: Set<string>) => {
    const node = byId.get(nodeId)
    if (!node) return null
    const kids = visibleChildren(nodeId)
    const isExpanded = expanded.has(nodeId)
    const isSelected = selectedNodeId === nodeId
    const review = deepReviews[nodeId]
    const turns = turnCounts[nodeId] || node.output_data?.iteration || 0
    const cycle = path.has(nodeId)
    const model = node.config?.llm_model || ''
    const skill = shortSkill(node.selected_skill_v2_id)
    const count = deliverablesCount(node)

    if (!matches(node) && !kids.some((id) => matches(byId.get(id)))) {
      return null
    }

    return (
      <div key={`${nodeId}-${depth}`}>
        <div
          className={`flex items-start gap-2 rounded px-2 py-1.5 hover:bg-gray-800/60 ${
            isSelected ? 'bg-indigo-500/10 border-l-2 border-indigo-500' : ''
          }`}
          style={{ paddingLeft: `${8 + depth * 16}px` }}
        >
          <button
            onClick={(event) => {
              event.stopPropagation()
              if (kids.length > 0) toggle(nodeId)
            }}
            className={`mt-0.5 w-4 shrink-0 text-[10px] ${kids.length > 0 ? 'text-gray-400' : 'text-transparent'}`}
            aria-label={isExpanded ? 'Collapse' : 'Expand'}
          >
            {kids.length > 0 ? (isExpanded ? '▾' : '▸') : '·'}
          </button>
          <button onClick={() => onSelectNode(nodeId)} className="min-w-0 flex-1 text-left">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs font-semibold text-white">{node.node_id}</span>
              <StatusBadge status={node.status} />
              {cycle && <span className="text-[10px] text-amber-400">↻ loop</span>}
              {node.node_type === 'decision' && <span className="text-[10px] text-amber-300">decision</span>}
              {node.node_type === 'input' && <span className="text-[10px] text-cyan-300">input</span>}
              {node.node_type === 'block' && <span className="text-[10px] text-emerald-300">block</span>}
              {review && (
                <span
                  className={`text-[10px] px-1 rounded ${
                    review.verdict === 'clean'
                      ? 'bg-emerald-900/40 text-emerald-300'
                      : 'bg-amber-900/40 text-amber-300'
                  }`}
                  title={`Deep review: ${review.verdict} (${review.score ?? '?'}/100)`}
                >
                  review {review.score ?? '?'}
                </span>
              )}
            </div>
            {node.description && <div className="mt-0.5 text-[11px] text-gray-400 line-clamp-2">{node.description}</div>}
            <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-gray-500">
              {model && <span className="font-mono">{model}</span>}
              {node.config?.base_image && <span>{node.config.base_image}</span>}
              {skill && <span className="text-blue-300">skill {skill}</span>}
              {turns > 0 && <span>{turns} turn{turns === 1 ? '' : 's'}</span>}
              {count > 0 && <span>{count} deliverable{count === 1 ? '' : 's'}</span>}
              {node.output_data?.error && <span className="text-red-400">error</span>}
            </div>
          </button>
        </div>
        {isExpanded &&
          kids.map((childId) => {
            if (path.has(childId) || childId === nodeId) {
              return (
                <div
                  key={`${childId}-cycle`}
                  className="text-[10px] text-amber-400"
                  style={{ paddingLeft: `${32 + depth * 16}px` }}
                >
                  ↻ {childId} (already in this branch)
                </div>
              )
            }
            const nextPath = new Set(path)
            nextPath.add(nodeId)
            return renderNode(childId, depth + 1, nextPath)
          })}
      </div>
    )
  }

  if (nodes.length === 0) {
    return <div className="card p-6 text-sm text-gray-500">No nodes in this DAG.</div>
  }

  return (
    <div className="card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter nodes…"
          className="input-field flex-1 min-w-[160px] text-xs"
        />
        <button onClick={expandAll} className="btn-secondary text-[10px] px-2 py-0.5">Expand all</button>
        <button onClick={collapseAll} className="btn-secondary text-[10px] px-2 py-0.5">Collapse all</button>
      </div>
      <div className="max-h-[60vh] overflow-y-auto">
        {roots.length > 0 ? (
          roots.map((rootId) => renderNode(rootId, 0, new Set()))
        ) : (
          <div className="text-xs text-gray-500">No roots found; showing all nodes.</div>
        )}
        {roots.length === 0 &&
          nodes.map((node) => renderNode(node.node_id, 0, new Set()))}
      </div>
    </div>
  )
}
