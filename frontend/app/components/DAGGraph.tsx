'use client'

import { useMemo, useState, useEffect, useCallback, useRef } from 'react'
import ReactFlow, { Node, Edge, Position, MarkerType, applyEdgeChanges, BaseEdge, EdgeProps, EdgeLabelRenderer, Handle, NodeProps } from 'reactflow'
import 'reactflow/dist/style.css'
import { API_GATEWAY, TEMPORAL_UI } from '../lib/api'

interface DAGNodeData {
  node_id: string
  description: string | null
  status: string
  depends_on: string[]
  task_id: string | null
  skill_id: string | null
  started_at: string | null
  completed_at: string | null
  node_type?: string
  deliverables_keys?: string[]
  selected_skill_v2_id?: string | null
  base_image?: string
  error?: string
  gate_failure?: string
  dag_id?: string
  iteration?: number | null
  output_message?: string
}

interface DAGEdgeData {
  from_node?: string
  to_node?: string
  condition?: string
  edge_type?: string
}

interface DAGGraphProps {
  nodes: DAGNodeData[]
  onNodeClick: (nodeId: string) => void
  editable?: boolean
  onConnect?: (source: string, target: string) => void
  onDisconnect?: (source: string, target: string) => void
  edges?: DAGEdgeData[]
}

const STATUS_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  completed: { bg: '#064e3b', border: '#10b981', text: '#6ee7b7' },
  running: { bg: '#1e3a5f', border: '#3b82f6', text: '#93c5fd' },
  failed: { bg: '#7f1d1d', border: '#ef4444', text: '#fca5a5' },
  pending: { bg: '#1f2937', border: '#6b7280', text: '#9ca3af' },
  pending_approval: { bg: '#713f12', border: '#f59e0b', text: '#fcd34d' },
  skipped: { bg: '#1f2937', border: '#4b5563', text: '#6b7280' },
}

function getColors(status: string) {
  return STATUS_COLORS[status] || STATUS_COLORS.pending
}

const EDGE_MARKER = { type: MarkerType.ArrowClosed, color: '#6b7280' }
const EDGE_STYLE = { stroke: '#6b7280', strokeWidth: 2 }

// Custom loop-back edge: draws a U-arc that dips BELOW the connected nodes so
// the re-approval return is clearly separate from the forward flow.
function LoopEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, labelStyle, labelBgStyle, labelBgPadding, labelBgBorderRadius } = props
  const dip = 140
  const midY = Math.max(sourceY, targetY) + dip
  // Go down from the source, across, and back up to the target (a rounded U).
  const path = `M ${sourceX} ${sourceY} C ${sourceX} ${midY}, ${targetX} ${midY}, ${targetX} ${targetY}`
  const labelX = (sourceX + targetX) / 2
  const labelY = midY
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
              fontSize: 10,
              color: labelStyle?.fill ?? '#f59e0b',
              background: labelBgStyle?.fill ?? '#1f2937',
              padding: 3,
              borderRadius: 4,
              zIndex: 10,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const edgeTypes = { loop: LoopEdge }

const TYPE_STYLES: Record<string, { bg: string; border: string; text: string; label: string; radius: string }> = {
  decision: { bg: '#78350f', border: '#f59e0b', text: '#fcd34d', label: '🛑', radius: '2px' },
  input: { bg: '#164e63', border: '#06b6d4', text: '#67e8f9', label: '📥', radius: '50%' },
}

// Custom step node: label + a compact info sub-box (deliverables/skill/image);
// clicking expands the box to show full details (image, skill, deliverables,
// error). As deliverables appear (live poll), they animate in below the step.
function DagStepNode({ data }: NodeProps) {
  const d = data as any
  const tstyle = d.node_type === 'decision' || d.node_type === 'input' ? TYPE_STYLES[d.node_type] : null
  const colors = tstyle ? { bg: tstyle.bg, border: tstyle.border, text: tstyle.text } : getColors(d.status)
  const deliverables: string[] = d.deliverables_keys || []
  const skill = d.skill || d.selected_skill_v2_id || ''
  const image = d.base_image || ''
  const error = d.error || d.gate_failure || ''
  const expanded = !!d.expanded
  const label = d.label || d.node_id || ''

  return (
    <div
      style={{
        background: colors.bg,
        border: `2px solid ${colors.border}`,
        color: colors.text,
        borderRadius: tstyle ? tstyle.radius : '8px',
        padding: '8px 10px',
        fontSize: '12px',
        fontFamily: 'monospace',
        fontWeight: 600,
        minWidth: '150px',
        maxWidth: expanded ? '280px' : '200px',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ width: 10, height: 10, background: '#38bdf8', border: '1px solid #0c4a6e' }} />
      <div className="flex items-center gap-1">
        {tstyle?.label && <span>{tstyle.label}</span>}
        <span>{label}</span>
      </div>
      {!expanded && (
        <div className="mt-1 border-t border-white/15 pt-1 text-[10px] font-normal flex flex-wrap gap-x-2">
          <span title="deliverables">📦 {deliverables.length}</span>
          <span title="skill">{skill ? `🧠 ${skill}` : '🧠 —'}</span>
          <span title="image">{image ? `🛠 ${image}` : '🛠 —'}</span>
        </div>
      )}
      {expanded && (
        <div className="mt-1 border-t border-white/15 pt-1 text-[10px] font-normal space-y-1">
          {image && <div>🛠 image: {image}</div>}
          {skill && <div>🧠 skill: {skill}</div>}
          {deliverables.length > 0 && (
            <div>
              <div>📦 deliverables:</div>
              <ul className="list-disc pl-4 space-y-0.5">
                {deliverables.map((dl) => (
                  <li key={dl} style={{ animation: 'fadeIn 0.4s ease' }}>
                    <a
                      href={`${API_GATEWAY}/v1/files/${d.task_id}/${d.iteration}/${encodeURIComponent(dl)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline decoration-dotted hover:text-white"
                      title="Open deliverable"
                    >
                      {dl}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {error && <div className="text-red-300">⚠ {error.slice(0, 180)}</div>}
          {d.output_message && !error && (
            <div className="pt-1 text-gray-300 whitespace-pre-wrap">{d.output_message.slice(0, 320)}</div>
          )}
          {d.dag_id && (
            <div className="pt-1">
              <a
                href={`${TEMPORAL_UI}/namespaces/default/workflows/${d.task_id ? `agent-task-${d.dag_id}-${label}` : `dag-node-${d.dag_id}-${label}`}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-300 underline decoration-dotted hover:text-white"
                title="Track in Temporal"
              >
                » Temporal step
              </a>
            </div>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ width: 10, height: 10, background: '#38bdf8', border: '1px solid #0c4a6e' }} />
    </div>
  )
}

const nodeTypes = { step: DagStepNode }

export default function DAGGraph({ nodes, onNodeClick, editable = false, onConnect, onDisconnect, edges: explicitEdges }: DAGGraphProps) {
  // Expanded node ids (click a step to expand/collapse its details).
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  // Manual drag positions — only reset when the set of node ids changes.
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({})
  const layoutSigRef = useRef('')

  const sig = nodes.map((n) => n.node_id).sort().join('|')
  useEffect(() => {
    if (layoutSigRef.current && layoutSigRef.current !== sig) {
      setPositions({})
      setExpandedIds(new Set())
    }
    layoutSigRef.current = sig
  }, [sig])

  const { flowNodes, flowEdges } = useMemo(() => {
    // Build adjacency for layout: group into waves (topological layers)
    const nodeMap = new Map(nodes.map((n) => [n.node_id, n]))
    const waves: string[][] = []
    const assigned = new Set<string>()

    while (assigned.size < nodes.length) {
      const wave: string[] = []
      for (const n of nodes) {
        if (assigned.has(n.node_id)) continue
        if (n.depends_on.every((d) => assigned.has(d))) {
          wave.push(n.node_id)
        }
      }
      if (wave.length === 0) {
        for (const n of nodes) {
          if (!assigned.has(n.node_id)) wave.push(n.node_id)
        }
      }
      wave.forEach((id) => assigned.add(id))
      waves.push(wave)
    }

    const X_GAP = 300
    const Y_GAP = 150

    const flowNodes: Node[] = []
    for (let wi = 0; wi < waves.length; wi++) {
      const wave = waves[wi]
      const totalHeight = (wave.length - 1) * Y_GAP
      const startY = -totalHeight / 2
      for (let ni = 0; ni < wave.length; ni++) {
        const nodeId = wave[ni]
        const data = nodeMap.get(nodeId)!
        const autoPos = { x: wi * X_GAP, y: startY + ni * Y_GAP }
        flowNodes.push({
          id: nodeId,
          type: 'step',
          position: positions[nodeId] || autoPos,
          data: {
            ...data,
            label: data.node_id,
            expanded: expandedIds.has(nodeId),
          },
        })
      }
    }

    const flowEdges: Edge[] = []
    for (const n of nodes) {
      for (const dep of n.depends_on) {
        flowEdges.push({
          id: `${dep}->${n.node_id}`,
          source: dep,
          target: n.node_id,
          markerEnd: EDGE_MARKER,
          style: EDGE_STYLE,
        })
      }
    }

    const seen = new Set(flowEdges.map((e) => `${e.source}->${e.target}`))
    for (const e of explicitEdges || []) {
      const src = e.from_node || ''
      const tgt = e.to_node || ''
      if (!src || !tgt) continue
      const key = `${src}->${tgt}`
      if (seen.has(key)) continue
      seen.add(key)
      const isLoop = e.edge_type === 'loop' || e.condition === 'loop'
      flowEdges.push({
        id: key,
        source: src,
        target: tgt,
        type: isLoop ? 'loop' : 'smoothstep',
        markerEnd: EDGE_MARKER,
        style: isLoop ? { stroke: '#f59e0b', strokeWidth: 2, strokeDasharray: '6 4' } : EDGE_STYLE,
        label: isLoop ? 're-approve' : undefined,
        labelStyle: { fill: '#f59e0b', fontSize: 10 },
        labelBgStyle: { fill: '#1f2937' },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
      })
    }

    return { flowNodes, flowEdges }
  }, [nodes, explicitEdges, editable, positions, expandedIds])

  const [edges, setEdges] = useState<Edge[]>(flowEdges)

  useEffect(() => {
    setEdges(flowEdges)
  }, [flowEdges])

  const handleConnect = useCallback(
    (conn: { source: string; target: string }) => {
      if (!onConnect || conn.source === conn.target) return
      onConnect(conn.source, conn.target)
      setEdges((eds) =>
        eds.some((e) => e.source === conn.source && e.target === conn.target)
          ? eds
          : [...eds, { id: `${conn.source}->${conn.target}`, source: conn.source, target: conn.target, markerEnd: EDGE_MARKER, style: EDGE_STYLE }]
      )
    },
    [onConnect]
  )

  const handleEdgesChange = useCallback(
    (changes: any[]) => {
      for (const ch of changes) {
        if (ch.type === 'remove' && onDisconnect) {
          const e = edges.find((x) => x.id === ch.id)
          if (e) onDisconnect(e.source, e.target)
        }
      }
      setEdges((eds) => applyEdgeChanges(changes, eds))
    },
    [edges, onDisconnect]
  )

  const handleNodeClick = useCallback(
    (_: any, node: any) => {
      setExpandedIds((prev) => {
        const next = new Set(prev)
        if (next.has(node.id)) next.delete(node.id)
        else next.add(node.id)
        return next
      })
      onNodeClick(node.id)
    },
    [onNodeClick]
  )

  const handleNodeDragStop = useCallback((_: any, node: any) => {
    setPositions((prev) => ({ ...prev, [node.id]: { x: node.position.x, y: node.position.y } }))
  }, [])

  return (
    <div className="card" style={{ height: Math.max(500, nodes.length * 90 + 150), position: 'relative' }}>
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        edgeTypes={edgeTypes}
        nodeTypes={nodeTypes}
        onNodeClick={handleNodeClick}
        onConnect={editable ? handleConnect : undefined}
        onEdgesChange={editable ? handleEdgesChange : undefined}
        onNodeDragStop={handleNodeDragStop}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        nodesConnectable={editable}
        elementsSelectable={true}
        deleteKeyCode={editable ? ['Backspace', 'Delete'] : null}
        minZoom={0.3}
        maxZoom={1.5}
      />
      {editable && (
        <div className="absolute top-2 left-2 z-10 rounded bg-gray-900/80 border border-indigo-700/40 px-2 py-1 text-[10px] text-indigo-200">
          Drag steps to rearrange · click a step to expand its details · drag right→left edges to connect · Delete removes an edge
        </div>
      )}
    </div>
  )
}
