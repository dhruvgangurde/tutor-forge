import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { Link, useParams } from 'react-router-dom'
import { useMessages, useSendChat, useRequestHint, useSessions } from './hooks'
import { ChatThread, TUTOR_NOTE } from './ChatThread'
import { ChatInput } from './ChatInput'
import { HintButton } from './HintButton'
import { SkeletonBlock } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { NotFoundState } from '../../components/ui/NotFoundState'
import { getErrorMessage, isNotFoundError } from '../../lib/api/errors'

export function TutorPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { data: messages, isLoading, isError, error } = useMessages(sessionId ?? '')
  const sendChat = useSendChat(sessionId ?? '')
  const requestHint = useRequestHint(sessionId ?? '')
  // Course name and session title come from the student's existing session
  // list (the same query the sessions page uses, usually already cached).
  // Until it arrives the header falls back to the generic title.
  const sessions = useSessions()
  const session = sessions.data?.find((s) => s.id === sessionId)

  // Derive hint level from messages
  const hintLevel =
    messages && messages.length > 0 ? Math.max(...messages.map((m) => m.hint_level), 0) : 0
  // A hint needs a question to build on; the backend 400s without one.
  const hasQuestion = (messages ?? []).some((m) => m.role === 'student')

  // Screen-reader announcements for the reply lifecycle only. The reply text
  // itself reaches the conversation log, which announces new messages.
  const replying = sendChat.isPending || requestHint.isPending
  const failed = sendChat.isError || requestHint.isError
  const [announcement, setAnnouncement] = useState('')
  const wasReplying = useRef(false)
  useEffect(() => {
    if (replying) setAnnouncement('Tutor is replying')
    else if (wasReplying.current) setAnnouncement(failed ? 'Something went wrong' : 'Reply ready')
    wasReplying.current = replying
  }, [replying, failed])

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
  if (isLoading || !messages) {
    return (
      <div className="tutor-page">
        <span className="sr-only" role="status">Loading session…</span>
        <div className="tutor-skeleton" aria-hidden="true">
          <SkeletonBlock width="12rem" />
          <SkeletonBlock width="55%" height="2rem" />
          <SkeletonBlock width="40%" height="3rem" />
          <SkeletonBlock width="70%" height="4rem" />
        </div>
      </div>
    )
  }

  const isEmpty = messages.length === 0

  return (
    <div className="tutor-page">
      {/* Always present, so the first announcement is not missed. */}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
      <div className="tutor-header">
        <Link to="/tutor" className="back-link">
          ← Back to sessions
        </Link>
        {session?.course_name && <p className="tutor-course">{session.course_name}</p>}
        <h1 className="page-title tutor-title">{session?.title ?? 'Tutoring Session'}</h1>
        {/* The empty chat shows this note under its prompt instead. */}
        {!isEmpty && <p className="page-subtitle">{TUTOR_NOTE}</p>}
      </div>

      <ChatThread
        messages={messages}
        isLoading={isLoading}
        isReplying={sendChat.isPending || requestHint.isPending}
      />

      <div className="tutor-composer">
        {/* A failed hint used to be an unhandled promise rejection with
            nothing on screen. The mutation's error is shown here instead. */}
        {requestHint.isError && (
          <ErrorBanner
            message={getErrorMessage(requestHint.error, 'Could not get a hint. Please try again.')}
          />
        )}

        <ChatInput
          onSendMessage={async (question) => {
            // A new question makes any earlier hint error stale.
            requestHint.reset()
            await sendChat.mutateAsync(question)
          }}
          isLoading={sendChat.isPending}
          isError={sendChat.isError}
          error={sendChat.error}
          leading={
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
          }
        />
      </div>
    </div>
  )
}
