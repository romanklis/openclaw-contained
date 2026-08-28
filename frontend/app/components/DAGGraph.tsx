'use client'

import { useMemo, useState, useEffect, useCallback } from 'react'
import ReactFlow, { Node, Edge, Position, MarkerType, applyEdgeChanges } from 'reactflow'
import 'reactflow/dist/style.css'

interface DAGNodeData {
  node_id: string
  description: string | null
  status: string
  depends_on: string[]
  task_id: string | null
  skill_id: string | null
  started_at: string | null
  completed_at: string | null
}

interface DAGGraphProps {
  nodes: DAGNodeData[]
  onNodeClick: (nodeId: string) => void
  editable?: boolean
  onConnect?: (source: string, target: string) => void
  onDisconnect?: (source: string, target: string) => void
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

export default function DAGGraph({ nodes, onNodeClick, editable = false, onConnect, onDisconnect }: DAGGraphProps) {
  const { flowNodes, flowEdges } = useMemo(() => {
    // Build adjacency for layout: group into waves (topological layers)
    const nodeMap = new Map(nodes.map(n => [n.node_id, n]))
    const waves: string[][] = []
    const assigned = new Set<string>()

    // Simple layering: nodes with no deps first, then those depending on assigned, etc.
    while (assigned.size < nodes.length) {
      const wave: string[] = []
      for (const n of nodes) {
        if (assigned.has(n.node_id)) continue
        if (n.depends_on.every(d => assigned.has(d))) {
          wave.push(n.node_id)
        }
      }
      if (wave.length === 0) {
        // Remaining nodes have circular deps — just add them
        for (const n of nodes) {
          if (!assigned.has(n.node_id)) wave.push(n.node_id)
        }
      }
      wave.forEach(id => assigned.add(id))
      waves.push(wave)
    }

    const X_GAP = 280
    const Y_GAP = 100

    const flowNodes: Node[] = []
    for (let wi = 0; wi < waves.length; wi++) {
      const wave = waves[wi]
      const totalHeight = (wave.length - 1) * Y_GAP
      const startY = -totalHeight / 2
      for (let ni = 0; ni < wave.length; ni++) {
        const nodeId = wave[ni]
        const data = nodeMap.get(nodeId)!
        const colors = getColors(data.status)
        flowNodes.push({
          id: nodeId,
          position: { x: wi * X_GAP, y: startY + ni * Y_GAP },
          data: { label: nodeId, description: data.description, status: data.status },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          style: {
            background: colors.bg,
            border: `2px solid ${colors.border}`,
            color: colors.text,
            borderRadius: '8px',
            padding: '10px 14px',
            fontSize: '12px',
            fontFamily: 'monospace',
            fontWeight: 600,
            minWidth: '140px',
            cursor: 'pointer',
          },
          ...(editable
            ? {
                handleStyle: {
                  width: 10,
                  height: 10,
                  background: '#38bdf8',
                  border: '1px solid #0c4a6e',
                },
              }
            : {}),
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

    return { flowNodes, flowEdges }
  }, [nodes, editable])

  const [edges, setEdges] = useState<Edge[]>(flowEdges)

  // Resync local edge state whenever the derived graph changes (e.g. after a
  // successful mutation/refetch from the parent).
  useEffect(() => {
    setEdges(flowEdges)
  }, [flowEdges])

  const handleConnect = useCallback(
    (conn: { source: string; target: string }) => {
      if (!onConnect || conn.source === conn.target) return
      onConnect(conn.source, conn.target)
      // Show the new connection immediately; the parent refetch reconciles it.
      setEdges(eds =>
        eds.some(e => e.source === conn.source && e.target === conn.target)
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
          const e = edges.find(x => x.id === ch.id)
          if (e) onDisconnect(e.source, e.target)
        }
      }
      setEdges(eds => applyEdgeChanges(changes, eds))
    },
    [edges, onDisconnect]
  )

  return (
    <div className="card" style={{ height: Math.max(500, nodes.length * 80 + 150), position: 'relative' }}>
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        onNodeClick={(_, node) => onNodeClick(node.id)}
        onConnect={editable ? handleConnect : undefined}
        onEdgesChange={editable ? handleEdgesChange : undefined}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={editable}
        elementsSelectable={true}
        deleteKeyCode={editable ? ['Backspace', 'Delete'] : null}
        minZoom={0.3}
        maxZoom={1.5}
      />
      {editable && (
        <div className="absolute top-2 left-2 z-10 rounded bg-gray-900/80 border border-indigo-700/40 px-2 py-1 text-[10px] text-indigo-200">
          Drag from a step's right edge to another step's left edge to connect · select an edge and press Delete to remove
        </div>
      )}
    </div>
  )
}
