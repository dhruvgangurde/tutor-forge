import { Link, useParams } from 'react-router-dom'
import { useMessages, useSendChat, useRequestHint } from './hooks'
import { ChatThread } from './ChatThread'
import { ChatInput } from './ChatInput'
import { HintButton } from './HintButton'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'

export function TutorPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { data: messages, isLoading, isError, error } = useMessages(sessionId ?? '')
  const sendChat = useSendChat(sessionId ?? '')
  const requestHint = useRequestHint(sessionId ?? '')

  // Derive hint level from messages
  const hintLevel =
    messages && messages.length > 0 ? Math.max(...messages.map((m) => m.hint_level), 0) : 0
  // A hint needs a question to build on; the backend 400s without one.
  const hasQuestion = (messages ?? []).some((m) => m.role === 'student')

  if (isLoading) return <Spinner label="Loading session…" />
  if (isError) return <ErrorBanner message={getErrorMessage(error)} />
  if (!sessionId) return <ErrorBanner message="Session ID is required." />

  return (
    <>
      <div className="page-header">
        <Link to="/tutor" className="back-link">
          ← Back to sessions
        </Link>
        <h1 className="page-title">Tutoring Session</h1>
        <p className="page-subtitle">Ask questions about the course material.</p>
      </div>

      <div className="tutor-container">
        <ChatThread messages={messages ?? []} isLoading={isLoading} />

        <div className="tutor-controls">
          {/* A failed hint used to be an unhandled promise rejection with
              nothing on screen. The mutation's error is shown here instead. */}
          {requestHint.isError && (
            <ErrorBanner
              message={getErrorMessage(requestHint.error, 'Could not get a hint. Please try again.')}
            />
          )}

          <HintButton
            currentHintLevel={hintLevel}
            hasQuestion={hasQuestion}
            onRequestHint={async () => {
              try {
                await requestHint.mutateAsync()
              } catch {
                // Surfaced via requestHint.isError above.
              }
            }}
            isLoading={requestHint.isPending}
          />

          <ChatInput
            onSendMessage={async (question) => {
              // A new question makes any earlier hint error stale.
              requestHint.reset()
              await sendChat.mutateAsync(question)
            }}
            isLoading={sendChat.isPending}
            isError={sendChat.isError}
            error={sendChat.error}
          />
        </div>
      </div>
    </>
  )
}
