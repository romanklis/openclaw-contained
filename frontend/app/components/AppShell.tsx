'use client'

import { useState } from 'react'
import { Sidebar } from './Sidebar'
import { SecurityBanner } from './SecurityBanner'

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />
      <main className={`flex-1 min-h-screen p-8 transition-all duration-200 ${collapsed ? 'ml-16' : 'ml-60'}`}>
        <SecurityBanner />
        {children}
      </main>
    </>
  )
}
