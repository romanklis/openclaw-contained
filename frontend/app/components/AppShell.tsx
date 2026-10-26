'use client'

import { useState } from 'react'
import { usePathname } from 'next/navigation'
import { Sidebar } from './Sidebar'
import { SecurityBanner } from './SecurityBanner'

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const pathname = usePathname()
  const isExplorationDetail = Boolean(pathname && /^\/explorations\/[^/]+/.test(pathname))

  return (
    <>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />
      {isExplorationDetail ? (
        <main className={`flex h-screen min-h-0 flex-1 flex-col overflow-hidden transition-all duration-200 ${collapsed ? 'ml-16' : 'ml-60'}`}>
          <div className="shrink-0 px-3 pt-3">
            <SecurityBanner />
          </div>
          <div className="flex min-h-0 flex-1 flex-col">
            {children}
          </div>
        </main>
      ) : (
        <main className={`flex-1 min-h-screen p-8 transition-all duration-200 ${collapsed ? 'ml-16' : 'ml-60'}`}>
          <SecurityBanner />
          {children}
        </main>
      )}
    </>
  )
}
