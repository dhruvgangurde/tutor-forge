import { Fragment, useEffect, useRef } from 'react'
import { ChatBubble } from './ChatBubble'
import type { TutoringMessageOut } from '../../lib/api/types'

interface ChatThreadProps {
  messages: TutoringMessageOut[]
  isLoading?: boolean
}

/**
 * Conversation thread display with auto-scroll to latest message.
 *
 * Hint escalations get an explicit marker. The /hint endpoint persists only a
 * tutor message — pressing the hint button writes no student turn (see
 * backend/tutoring/service.py request_hint) — so the thread stacked two, three
 * or four tutor bubbles in a row with nothing to say why. A student could not
 * tell an escalating hint on the same question from a fresh answer to a new
 * one. The marker stands in for the action the student took, which is the
 * information that was actually missing.
 */
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
      {messages.map((msg, idx) => {
        // A tutor message above level 0 exists because the student pressed the
        // hint button. Every one of them gets a marker, including the first,
        // since that is the escalation the student needs to see.
        const showHintMarker = msg.role === 'tutor' && msg.hint_level > 0
        const previous = messages[idx - 1]
        // Consecutive hints belong to one ladder on one question; the class
        // lets the CSS tie them together visually.
        const continuesLadder =
          showHintMarker &&
          previous?.role === 'tutor' &&
          previous.hint_level > 0 &&
          previous.hint_level < msg.hint_level

        return (
          <Fragment key={msg.id}>
            {showHintMarker && (
              <div
                className={`hint-marker ${continuesLadder ? 'continues' : ''}`}
                role="separator"
              >
                <span className="hint-marker-text">
                  {continuesLadder
                    ? 'You asked for another hint on the same question'
                    : 'You asked for a hint'}
                </span>
              </div>
            )}
            <ChatBubble
              role={msg.role}
              content={msg.content}
              citations={msg.citations}
              isRefusal={msg.is_refusal}
              timestamp={msg.created_at}
              hintLevel={msg.hint_level}
            />
          </Fragment>
        )
      })}
    </div>
  )
}
