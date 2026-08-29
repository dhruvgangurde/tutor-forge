import { useEffect, useState } from 'react'
import { getAvailableCourses } from '../../lib/api/courses'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import type { CourseSummary } from '../../lib/api/types'

interface CoursePickerModalProps {
  onSelectCourse: (courseId: string) => void
  onCancel: () => void
  isLoading?: boolean
}

/** Modal to select a course for a new tutoring session. */
export function CoursePickerModal({
  onSelectCourse,
  onCancel,
  isLoading,
}: CoursePickerModalProps) {
  const [courses, setCourses] = useState<CourseSummary[]>([])
  const [isLoadingCourses, setIsLoadingCourses] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function loadCourses() {
      try {
        setIsLoadingCourses(true)
        setError(null)
        const available = await getAvailableCourses()
        setCourses(available)
      } catch (err) {
        setError(getErrorMessage(err))
      } finally {
        setIsLoadingCourses(false)
      }
    }
    loadCourses()
  }, [])

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">Start a tutoring session</h2>
        <p className="modal-message">Select a course to get tutoring help:</p>

        {error && <ErrorBanner message={error} />}

        <div className="modal-body">
          {isLoadingCourses ? (
            <Spinner label="Loading available courses..." />
          ) : courses.length === 0 ? (
            <p className="modal-empty">No courses available. Check back when courses are ready.</p>
          ) : (
            <ul className="course-picker-list">
              {courses.map((course) => (
                <li key={course.id}>
                  <button
                    type="button"
                    className="course-picker-item"
                    onClick={() => onSelectCourse(course.id)}
                    disabled={isLoading}
                  >
                    <span className="course-name">{course.name}</span>
                    <span className="course-date">
                      {new Date(course.created_at).toLocaleDateString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onCancel}
            disabled={isLoading || isLoadingCourses}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
