import { FormEvent, useState } from 'react'
import { useEnrollments, useEnrollStudent, useRemoveEnrollment } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'
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
      showToast(ack.message, 'success')
    } catch (err) {
      showToast(getErrorMessage(err), 'error')
    }
  }

  return (
    <>
      <div className="card upload-panel">
        <form onSubmit={handleAdd} className="login-form">
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
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="e.g. student@school.edu"
            />
            <p className="field-hint">The student must already have an account.</p>
          </div>

          <button type="submit" className="btn btn-primary" disabled={enroll.isPending}>
            {enroll.isPending ? 'Adding…' : 'Add student'}
          </button>
        </form>
      </div>

      {enrollments.isLoading && <Spinner label="Loading students…" />}
      {enrollments.isError && <ErrorBanner message={getErrorMessage(enrollments.error)} />}

      {enrollments.data && enrollments.data.length === 0 && (
        <EmptyState
          icon="👥"
          label="No students enrolled yet. Only enrolled students can see this course."
        />
      )}

      {enrollments.data && enrollments.data.length > 0 && (
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
                <td>{s.email}</td>
                <td>{new Date(s.enrolled_at).toLocaleDateString()}</td>
                <td>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
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
      )}
    </>
  )
}
