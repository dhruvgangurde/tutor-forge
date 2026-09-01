import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCourseDeletionImpact, useDeleteCourse, useRestoreCourse } from './hooks'
import { Badge } from '../../components/ui/Badge'
import { Spinner } from '../../components/ui/Spinner'
import { statusToVariant } from '../../lib/statusVariant'
import { getErrorMessage } from '../../lib/api/errors'
import { useToast } from '../../hooks/useToast'
import { useConfirm } from '../../hooks/useConfirm'
import type { CourseSummary } from '../../lib/api/types'

interface CourseCardProps {
  course: CourseSummary
}

/**
 * One course in the teacher's list.
 *
 * Archived courses stay visible to their owner — archiving tidies the list, it
 * does not hide a course from the person who made it — and are clearly marked
 * plus restorable.
 */
export function CourseCard({ course }: CourseCardProps) {
  const [showDelete, setShowDelete] = useState(false)
  const impact = useCourseDeletionImpact(course.id, showDelete)
  const remove = useDeleteCourse()
  const restore = useRestoreCourse()
  const { showToast } = useToast()
  const confirm = useConfirm()

  const busy = remove.isPending || restore.isPending

  async function handleArchive() {
    const ok = await confirm(
      `Archive "${course.name}"? Students will no longer be able to start new work ` +
        'on it. All existing submissions, grades and history are kept, and you can ' +
        'restore it at any time.',
      { confirmLabel: 'Archive' }
    )
    if (!ok) return
    try {
      const ack = await remove.mutateAsync({ courseId: course.id })
      showToast(ack.message, 'success')
      setShowDelete(false)
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleRestore() {
    try {
      const ack = await restore.mutateAsync(course.id)
      showToast(ack.message, 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  async function handleHardDelete() {
    const ok = await confirm(
      `Permanently delete "${course.name}"? This removes the course, its ` +
        'assessments and its indexed material for good. It cannot be undone.',
      { confirmLabel: 'Delete permanently', danger: true }
    )
    if (!ok) return
    try {
      const ack = await remove.mutateAsync({ courseId: course.id, hard: true })
      showToast(ack.message, 'success')
      setShowDelete(false)
    } catch (err) {
      // The backend refuses with a 409 naming exactly what blocks the delete;
      // surface it rather than a generic failure.
      showToast(getErrorMessage(err), 'error')
    }
  }

  return (
    <div className={`card${course.is_archived ? ' card-archived' : ''}`}>
      <div className="card-header-row">
        <Link to={`/courses/${course.id}`} className="card-title-link">
          <h3 className="card-title">{course.name}</h3>
        </Link>
        <div className="card-badges">
          {course.is_archived && <Badge variant="muted">archived</Badge>}
          <Badge variant={statusToVariant(course.status)}>{course.status}</Badge>
        </div>
      </div>

      <p className="card-meta">
        Uploaded {new Date(course.created_at).toLocaleDateString()}
        {course.is_archived && course.archived_at && (
          <> · archived {new Date(course.archived_at).toLocaleDateString()}</>
        )}
      </p>

      <div className="card-actions">
        {course.is_archived ? (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleRestore}
            disabled={busy}
          >
            {restore.isPending ? 'Restoring…' : 'Restore'}
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleArchive}
            disabled={busy}
          >
            {remove.isPending ? 'Archiving…' : 'Archive'}
          </button>
        )}
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => setShowDelete((v) => !v)}
          disabled={busy}
        >
          {showDelete ? 'Cancel' : 'Delete…'}
        </button>
      </div>

      {showDelete && (
        <div className="delete-panel">
          {impact.isLoading && <Spinner label="Checking what this would remove…" />}
          {impact.data && (
            <>
              {/* Shown BEFORE the confirm, because a teacher cannot otherwise
                  tell a disposable mis-upload from a course with students'
                  released grades behind it. */}
              <p className="delete-panel-summary">
                {impact.data.impact.assessments} assessment(s) ·{' '}
                {impact.data.impact.submissions} submission(s) ·{' '}
                {impact.data.impact.final_grades} released grade(s) ·{' '}
                {impact.data.impact.tutoring_sessions} tutoring session(s)
              </p>
              {impact.data.can_hard_delete ? (
                <>
                  <p className="field-hint">
                    No student work on this course — it can be removed permanently.
                  </p>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={handleHardDelete}
                    disabled={busy}
                  >
                    Delete permanently
                  </button>
                </>
              ) : (
                <p className="field-error">
                  Cannot delete permanently: {impact.data.blocking_reason}. Archive
                  it instead — that hides it from students and keeps the record.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
