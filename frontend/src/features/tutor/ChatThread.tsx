import { useEffect, useRef } from 'react'
import { ChatBubble } from './ChatBubble'
import type { TutoringMessageOut } from '../../lib/api/types'

interface ChatThreadProps {
  messages: TutoringMessageOut[]
  isLoading?: boolean
}

/** Conversation thread display with auto-scroll to latest message. */
export function ChatThread({ messages, isLoading }: ChatThreadProps) {
  const threadRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight
    }
  }, [messages])

  if (isLoading) {
    return <div className="chat-thread-loading">Loading conversation...</div>
  }

  if (messages.length === 0) {
    return (
      <div className="chat-thread-empty">
        <p>Start a conversation by asking a question!</p>
      </div>
    )
  }

  return (
    <div className="chat-thread" ref={threadRef}>
      {messages.map((msg) => (
        <ChatBubble
          key={msg.id}
          role={msg.role}
          content={msg.content}
          citations={msg.citations}
          isRefusal={msg.is_refusal}
          timestamp={msg.created_at}
        />
      ))}
    </div>
  )
}
