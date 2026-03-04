'use client'

import { useState, useEffect } from 'react'
import { API } from '../lib/api'

export function SecurityBanner() {
  const [sandboxMode, setSandboxMode] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    fetch(`${API}/api/system/info`)
      .then(r => r.json())
      .then(data => setSandboxMode(data.sandbox_mode))
      .catch(() => {})
  }, [])

  if (!sandboxMode || sandboxMode === 'gvisor' || dismissed) return null

  return (
    <div className="bg-amber-900/40 border border-amber-500/50 rounded-lg px-4 py-3 mb-6 flex items-start gap-3">
      <span className="text-amber-400 text-lg mt-0.5 shrink-0">⚠️</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-amber-200">
          Insecure Docker-in-Docker Mode
        </p>
        <p className="text-xs text-amber-300/80 mt-1 leading-relaxed">
          TaskForge is running with <code className="bg-amber-800/50 px-1 py-0.5 rounded text-amber-200">AGENT_SANDBOX_MODE=insecure-dind</code>.
          Agent containers execute as <strong>privileged</strong> with full host access inside DinD.
          For production deployments, switch to Google gVisor (runsc) for secure sandboxing.
          See{' '}
          <a
            href="https://github.com/openclaw/openclaw-contained/blob/main/docs/GVISOR_SETUP.md"
            target="_blank"
            rel="noopener noreferrer"
            className="underline text-amber-100 hover:text-white"
          >
            docs/GVISOR_SETUP.md
          </a>{' '}
          for setup instructions.
        </p>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="text-amber-400/60 hover:text-amber-200 text-lg shrink-0 mt-0.5"
        title="Dismiss"
      >
        ✕
      </button>
    </div>
  )
}
