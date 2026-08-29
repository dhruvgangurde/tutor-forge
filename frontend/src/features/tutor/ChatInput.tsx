import { FormEvent, useRef, useState } from 'react'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'

interface ChatInputProps {
  onSendMessage: (question: string) => Promise<void>
  isLoading?: boolean
  isError?: boolean
  error?: unknown
}

/** Chat input field with send button. */
export function ChatInput({
  onSendMessage,
  isLoading,
  isError,
  error,
}: ChatInputProps) {
  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  async function submitMessage() {
    if (!input.trim()) return
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
      <form onSubmit={handleSubmit} className="chat-input-form">
        <input
          ref={inputRef}
          type="text"
          className="chat-input-field"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question about the course material..."
          disabled={isLoading}
          autoFocus
        />
        <button
          type="submit"
          className="btn btn-primary btn-sm"
          disabled={isLoading || !input.trim()}
        >
          {isLoading ? 'Sending...' : 'Send'}
        </button>
      </form>
    </div>
  )
}
