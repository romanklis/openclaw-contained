import type { Metadata } from 'next'
import './globals.css'
import { Sidebar } from './components/Sidebar'

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
        <Sidebar />
        <main className="flex-1 ml-60 min-h-screen p-8">
          {children}
        </main>
      </body>
    </html>
  )
}
