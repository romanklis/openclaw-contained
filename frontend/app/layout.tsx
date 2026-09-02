import type { Metadata } from 'next'
import './globals.css'
import { AppShell } from './components/AppShell'
import { ProjectProvider } from './lib/ProjectContext'

export const metadata: Metadata = {
  title: 'TaskForge Platform',
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
          <AppShell>{children}</AppShell>
        </ProjectProvider>
      </body>
    </html>
  )
}
