'use client'

import { useMemo } from 'react'
import ReactFlow, { Node, Edge, Position, MarkerType } from 'reactflow'
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

export default function DAGGraph({ nodes, onNodeClick }: DAGGraphProps) {
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
          markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
          style: { stroke: '#6b7280', strokeWidth: 2 },
        })
      }
    }

    return { flowNodes, flowEdges }
  }, [nodes])

  return (
    <div className="card" style={{ height: Math.max(300, nodes.length * 60 + 100) }}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodeClick={(_, node) => onNodeClick(node.id)}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        minZoom={0.3}
        maxZoom={1.5}
      />
    </div>
  )
}
