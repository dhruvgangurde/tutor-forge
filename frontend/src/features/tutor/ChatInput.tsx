import { FormEvent, ReactNode, useRef, useState } from 'react'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { SendIcon } from '../../components/ui/icons'
import { MAX_TUTOR_QUESTION_CHARS } from '../../lib/limits'

interface ChatInputProps {
  onSendMessage: (question: string) => Promise<void>
  isLoading?: boolean
  isError?: boolean
  error?: unknown
  /** Rendered on the action row beside Send (the tutor page puts Hint here). */
  leading?: ReactNode
}

/**
 * Docked composer: the question field on top, then one row with the hint
 * control and Send. A failed send keeps the question in the field, so Send
 * itself is the way to try again.
 */
export function ChatInput({
  onSendMessage,
  isLoading,
  isError,
  error,
  leading,
}: ChatInputProps) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  async function submitMessage() {
    if (isLoading || !input.trim()) return
    try {
      await onSendMessage(input)
      setInput('')
      inputRef.current?.focus()
    } catch {
      // Error surfaced via isError
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    await submitMessage()
  }

  async function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !isLoading) {
      e.preventDefault()
      await submitMessage()
    }
  }

  return (
    <div className="chat-input-container">
      {isError && (
        <ErrorBanner message={getErrorMessage(error)} />
      )}
      <form onSubmit={handleSubmit} className="chat-input-form composer">
        {/* The placeholder is a hint, not a label. */}
        <label htmlFor="chat-question-input" className="sr-only">
          Ask a question about the course material
        </label>
        <input
          ref={inputRef}
          id="chat-question-input"
          type="text"
          className="chat-input-field"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          maxLength={MAX_TUTOR_QUESTION_CHARS}
          placeholder="Ask a question about the course material..."
          // Read-only, not disabled, while a reply is pending: a disabled field
          // drops keyboard focus, and the student would have to find it again.
          readOnly={isLoading}
          aria-busy={isLoading || undefined}
          autoFocus
        />
        <div className="composer-actions">
          {leading}
          <button
            type="submit"
            className="btn btn-primary btn-sm composer-send"
            disabled={isLoading || !input.trim()}
          >
            <SendIcon size={16} />
            {isLoading ? 'Sending...' : 'Send'}
          </button>
        </div>
      </form>
      {input.length >= MAX_TUTOR_QUESTION_CHARS && (
        <p className="field-hint" role="status">
          You have reached the {MAX_TUTOR_QUESTION_CHARS.toLocaleString('en-US')}-character limit for a question.
        </p>
      )}
    </div>
  )
}
