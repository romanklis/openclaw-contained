'use client'

import type { ReactNode } from 'react'

interface ChatMessageBubbleProps {
  sender: 'user' | 'agent'
  children: ReactNode
  streaming?: boolean
}

export default function ChatMessageBubble({ sender, children, streaming = false }: ChatMessageBubbleProps) {
  const isUser = sender === 'user'
  return (
    <div className={`my-3 flex max-w-[78%] flex-col ${isUser ? 'ml-auto items-end' : 'items-start'}`}>
      <div className="mb-1 flex h-4 items-center gap-1.5 text-[9px] uppercase tracking-wide">
        <span className="font-medium text-indigo-300/70">
          {isUser ? 'You' : 'TaskForge Agent'}
        </span>
      </div>
      <div className="w-fit max-w-full rounded-lg bg-indigo-600 px-3 py-2 text-xs leading-relaxed text-white">
        {children}
        {streaming && (
          <span className="ml-0.5 inline-block h-3 w-1 animate-pulse rounded-sm bg-cyan-300 align-middle" />
        )}
      </div>
    </div>
  )
}
