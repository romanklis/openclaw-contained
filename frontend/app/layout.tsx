import type { Metadata } from 'next'
import './globals.css'
import { Sidebar } from './components/Sidebar'
import { SecurityBanner } from './components/SecurityBanner'
import { ProjectProvider } from './lib/ProjectContext'

export const metadata: Metadata = {
  title: 'TaskForge',
  description: 'Auditable agent orchestration for OpenClaw',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen flex">
        <ProjectProvider>
          <Sidebar />
          <main className="flex-1 ml-60 min-h-screen p-8">
            <SecurityBanner />
            {children}
          </main>
        </ProjectProvider>
      </body>
    </html>
  )
}
