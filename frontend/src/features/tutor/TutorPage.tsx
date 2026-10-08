import { isAxiosError } from 'axios'
import { Link, useParams } from 'react-router-dom'
import { useMessages, useSendChat, useRequestHint } from './hooks'
import { ChatThread } from './ChatThread'
import { ChatInput } from './ChatInput'
import { HintButton } from './HintButton'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { NotFoundState } from '../../components/ui/NotFoundState'
import { getErrorMessage, isNotFoundError } from '../../lib/api/errors'

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

  if (isError) {
    // 403 is a session that belongs to someone else: from this student's
    // point of view it does not exist either.
    if (isNotFoundError(error) || (isAxiosError(error) && error.response?.status === 403)) {
      return (
        <NotFoundState
          title="Tutoring session not found"
          message="This session doesn't exist, or it belongs to another account."
          linkTo="/tutor"
          linkLabel="Back to sessions"
        />
      )
    }
    return (
      <>
        <div className="page-header">
          <Link to="/tutor" className="back-link">
            ← Back to sessions
          </Link>
        </div>
        <ErrorBanner message={getErrorMessage(error)} />
      </>
    )
  }
  // Nothing session-shaped is drawn until the session has actually loaded: a
  // fake session URL used to show a working-looking empty chat while the
  // request was failing.
  if (isLoading || !messages) return <Spinner label="Loading session…" />

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
