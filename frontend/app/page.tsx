'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { StatusDot, StatusBadge } from './components/StatusComponents'
import { API } from './lib/api'

interface Stats {
  tasks: { total: number; running: number; completed: number; failed: number }
  deployments: { total: number; running: number; pending: number }
  approvals: { pending: number }
}

interface RecentTask {
  id: string
  name: string
  status: string
  created_at: string
}

interface RecentDeployment {
  id: string
  name: string
  status: string
  host_port: number | null
  url: string | null
  task_id: string
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats>({
    tasks: { total: 0, running: 0, completed: 0, failed: 0 },
    deployments: { total: 0, running: 0, pending: 0 },
    approvals: { pending: 0 },
  })
  const [recentTasks, setRecentTasks] = useState<RecentTask[]>([])
  const [recentDeployments, setRecentDeployments] = useState<RecentDeployment[]>([])

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [tasksRes, deploysRes, approvalsRes] = await Promise.all([
          fetch(`${API}/api/tasks`).then(r => r.json()).catch(() => []),
          fetch(`${API}/api/deployments`).then(r => r.json()).catch(() => []),
          fetch(`${API}/api/capabilities/requests?status_filter=pending`).then(r => r.json()).catch(() => []),
        ])

        const tasks = Array.isArray(tasksRes) ? tasksRes : []
        const deploys = Array.isArray(deploysRes) ? deploysRes : []
        const approvals = Array.isArray(approvalsRes) ? approvalsRes : []

        setStats({
          tasks: {
            total: tasks.length,
            running: tasks.filter((t: any) => t.status === 'running').length,
            completed: tasks.filter((t: any) => t.status === 'completed').length,
            failed: tasks.filter((t: any) => t.status === 'failed').length,
          },
          deployments: {
            total: deploys.length,
            running: deploys.filter((d: any) => d.status === 'running').length,
            pending: deploys.filter((d: any) => d.status === 'pending_approval').length,
          },
          approvals: { pending: approvals.length },
        })
        setRecentTasks(tasks.slice(0, 5))
        setRecentDeployments(deploys.slice(0, 5))
      } catch (err) {
        console.error('Dashboard fetch error:', err)
      }
    }
    fetchAll()
    const interval = setInterval(fetchAll, 8000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white mb-1">Dashboard</h1>
        <p className="text-sm text-gray-500">Overview of your TaskForge platform</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          label="Total Tasks"
          value={stats.tasks.total}
          icon="📋"
          color="text-indigo-400"
        />
        <StatCard
          label="Running Tasks"
          value={stats.tasks.running}
          icon="⚡"
          color="text-blue-400"
          pulse={stats.tasks.running > 0}
        />
        <StatCard
          label="Deployments Live"
          value={stats.deployments.running}
          icon="🟢"
          color="text-emerald-400"
          pulse={stats.deployments.running > 0}
        />
        <StatCard
          label="Pending Approvals"
          value={stats.approvals.pending + stats.deployments.pending}
          icon="🔔"
          color="text-amber-400"
          highlight={stats.approvals.pending + stats.deployments.pending > 0}
        />
      </div>

      {/* Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Tasks */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-white">Recent Tasks</h2>
            <Link href="/tasks" className="text-xs text-indigo-400 hover:text-indigo-300">
              View all →
            </Link>
          </div>
          {recentTasks.length === 0 ? (
            <p className="text-sm text-gray-600 text-center py-8">No tasks yet</p>
          ) : (
            <div className="space-y-2">
              {recentTasks.map((task) => (
                <Link
                  key={task.id}
                  href={`/tasks/${task.id}`}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-[#1c1c28] transition-colors group"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <StatusDot status={task.status} />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-200 truncate group-hover:text-white">
                        {task.name}
                      </div>
                      <div className="text-xs text-gray-600 font-mono">{task.id}</div>
                    </div>
                  </div>
                  <StatusBadge status={task.status} />
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Deployments */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-white">Deployments</h2>
            <Link href="/deployments" className="text-xs text-indigo-400 hover:text-indigo-300">
              View all →
            </Link>
          </div>
          {recentDeployments.length === 0 ? (
            <p className="text-sm text-gray-600 text-center py-8">No deployments yet</p>
          ) : (
            <div className="space-y-2">
              {recentDeployments.map((d) => (
                <div
                  key={d.id}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-[#1c1c28] transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <StatusDot status={d.status} />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-200 truncate">
                        {d.name}
                      </div>
                      <div className="text-xs text-gray-600 font-mono">{d.id}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {d.status === 'running' && d.host_port && (
                      <a
                        href={`http://localhost:${d.host_port}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-emerald-400 hover:text-emerald-300 font-mono"
                      >
                        :{d.host_port}
                      </a>
                    )}
                    <StatusBadge status={d.status} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Quick Actions */}
      <div className="mt-6 card p-5">
        <h2 className="font-semibold text-white mb-4">Quick Actions</h2>
        <div className="flex flex-wrap gap-3">
          <Link href="/tasks" className="btn-primary text-sm">
            + New Task
          </Link>
          <Link href="/approvals" className="btn-warning text-sm">
            Review Approvals {stats.approvals.pending > 0 && `(${stats.approvals.pending})`}
          </Link>
          <Link href="/deployments" className="btn-secondary text-sm">
            Manage Deployments
          </Link>
          <a
            href={`${API}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-secondary text-sm"
          >
            API Docs ↗
          </a>
        </div>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  icon,
  color,
  pulse,
  highlight,
}: {
  label: string
  value: number
  icon: string
  color: string
  pulse?: boolean
  highlight?: boolean
}) {
  return (
    <div className={`stat-card ${highlight ? 'border-amber-500/30' : ''}`}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">{label}</div>
          <div className={`text-3xl font-bold ${color} ${pulse ? 'pulse-dot' : ''}`}>
            {value}
          </div>
        </div>
        <span className="text-2xl">{icon}</span>
      </div>
    </div>
  )
}


