import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSessions, useCreateSession } from './hooks'
import { CoursePickerModal } from './CoursePickerModal'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'

/**
 * The student's list of tutoring sessions.
 *
 * Cards are titled by the session's first question (derived server-side, see
 * tutoring/service.py derive_session_title). They used to read
 * "Session 4bc8fb2b" with a "Level 3" badge: the UUID prefix is not something
 * a student can recognise their own work by, and the level was the hint-ladder
 * depth of the last question only — it said nothing about what the session was
 * about, and moved every time a hint was requested. The course name replaces
 * it, which is a real distinguisher when a student tutors across two courses.
 */
export function SessionsPage() {
  const navigate = useNavigate()
  const { data: sessions, isLoading, isError, error } = useSessions()
  const createSession = useCreateSession()
  const [showModal, setShowModal] = useState(false)
  const { showToast } = useToast()

  async function handleCreateSession(courseId: string) {
    try {
      const result = await createSession.mutateAsync(courseId)
      showToast('Tutoring session created!', 'success')
      navigate(`/tutor/${result.session_id}`)
    } catch {
      // Error handled by mutation state
    }
  }

  return (
    <>
      <div className="page-header page-header-row">
        <div>
          <h1 className="page-title">Tutoring</h1>
          <p className="page-subtitle">Get Socratic guidance on course materials.</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setShowModal(true)}
          disabled={createSession.isPending}
        >
          New tutoring session
        </button>
      </div>

      {createSession.isError && (
        <ErrorBanner message={getErrorMessage(createSession.error)} />
      )}

      {showModal && (
        <CoursePickerModal
          onSelectCourse={handleCreateSession}
          onCancel={() => setShowModal(false)}
          isLoading={createSession.isPending}
        />
      )}

      {isLoading && <Spinner label="Loading sessions…" />}
      {isError && <ErrorBanner message={getErrorMessage(error)} />}

      {!isLoading && !isError && sessions && sessions.length === 0 && (
        <EmptyState
          icon="🧑‍🏫"
          label="No tutoring sessions yet. Start your first session!"
        />
      )}

      {!isLoading && !isError && sessions && sessions.length > 0 && (
        <div className="sessions-list">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className="session-card"
              onClick={() => navigate(`/tutor/${session.id}`)}
            >
              <div className="session-card-header">
                <h3 className="session-title">
                  {session.title ?? 'New session — no questions yet'}
                </h3>
                <span className="session-course">{session.course_name}</span>
              </div>
              <p className="session-meta">
                {session.message_count > 0
                  ? `${session.message_count} message${session.message_count === 1 ? '' : 's'} · last active ${new Date(session.last_activity_at).toLocaleDateString()}`
                  : `Started ${new Date(session.created_at).toLocaleDateString()}`}
              </p>
            </button>
          ))}
        </div>
      )}
    </>
  )
}
