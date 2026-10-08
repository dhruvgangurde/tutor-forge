import { FormEvent, useState } from 'react'
import { useEnrollments, useEnrollStudent, useRemoveEnrollment } from './hooks'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'
import { MAX_EMAIL_CHARS } from '../../lib/limits'
import { useToast } from '../../hooks/useToast'
import { useConfirm } from '../../hooks/useConfirm'
import type { Enrollment } from '../../lib/api/types'

interface CourseEnrollmentPanelProps {
  courseId: string
}

/**
 * Course roster: the students who can access this course.
 *
 * Only enrolled students see the course, start tutoring sessions on it, or
 * list/take its assessments, so this is the course's access list. Students are
 * added by the email of an existing account; removal is immediate but keeps
 * their submissions and grades.
 */
export function CourseEnrollmentPanel({ courseId }: CourseEnrollmentPanelProps) {
  const [email, setEmail] = useState('')
  const enrollments = useEnrollments(courseId)
  const enroll = useEnrollStudent(courseId)
  const remove = useRemoveEnrollment(courseId)
  const { showToast } = useToast()
  const confirm = useConfirm()

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    try {
      const added = await enroll.mutateAsync(email.trim())
      showToast(`${added.email} enrolled.`, 'success')
      setEmail('')
    } catch {
      // Error is surfaced via enroll.isError below; nothing further to do here.
    }
  }

  async function handleRemove(student: Enrollment) {
    const ok = await confirm(
      `Remove ${student.email} from this course? They lose access to its tutoring ` +
        'and assessments immediately. Their existing submissions and grades are kept.',
      { confirmLabel: 'Remove', danger: true }
    )
    if (!ok) return
    try {
      const ack = await remove.mutateAsync(student.student_id)
      // An earlier "already enrolled" (or any add error) no longer describes
      // the roster once something else has succeeded.
      enroll.reset()
      showToast(ack.message, 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  return (
    <>
      <div className="card upload-panel">
        <form onSubmit={handleAdd} className="enroll-form">
          {enroll.isError && <ErrorBanner message={getErrorMessage(enroll.error)} />}

          <div className="field-group">
            <label htmlFor="enroll-email-input" className="field-label">
              Add a student by email
            </label>
            <input
              id="enroll-email-input"
              type="email"
              className="field-input"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                // Editing the address makes the last add error stale.
                if (enroll.isError) enroll.reset()
              }}
              required
              maxLength={MAX_EMAIL_CHARS}
              placeholder="e.g. student@school.edu"
            />
            <p className="field-hint">The student must already have an account.</p>
          </div>

          <button type="submit" className="btn btn-primary enroll-submit" disabled={enroll.isPending}>
            {enroll.isPending ? 'Adding…' : 'Add student'}
          </button>
        </form>
      </div>

      {enrollments.isLoading && <SkeletonRows label="Loading students…" rows={3} columns={3} />}
      {enrollments.isError && <ErrorBanner message={getErrorMessage(enrollments.error)} />}

      {enrollments.data && enrollments.data.length === 0 && (
        <EmptyState label="No students enrolled yet. Only enrolled students can see this course." />
      )}

      {enrollments.data && enrollments.data.length > 0 && (
        <div className="card table-card">
        <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Student</th>
              <th>Enrolled</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {enrollments.data.map((s) => (
              <tr key={s.student_id}>
                <td className="cell-strong">{s.email}</td>
                <td>{new Date(s.enrolled_at).toLocaleDateString()}</td>
                <td className="cell-actions">
                  <button
                    type="button"
                    className="btn btn-tertiary btn-tertiary-danger btn-sm"
                    onClick={() => handleRemove(s)}
                    disabled={remove.isPending}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        </div>
      )}
    </>
  )
}
