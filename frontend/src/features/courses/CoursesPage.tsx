import { useState } from 'react'
import { useCourses } from './hooks'
import { CourseCard } from './CourseCard'
import { CourseUploadForm } from './CourseUploadForm'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'

export function CoursesPage() {
  const [showUpload, setShowUpload] = useState(false)
  const { data: courses, isLoading, isError, error } = useCourses()

  return (
    <>
      <div className="page-header page-header-row">
        <div>
          <h1 className="page-title">Courses</h1>
          <p className="page-subtitle">Upload and manage your course materials.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setShowUpload((v) => !v)}>
          {showUpload ? 'Cancel' : 'Upload course'}
        </button>
      </div>

      {showUpload && (
        <div className="card upload-panel">
          <CourseUploadForm onDone={() => setShowUpload(false)} />
        </div>
      )}

      {isLoading && <Spinner label="Loading courses…" />}
      {isError && <ErrorBanner message={getErrorMessage(error)} />}

      {!isLoading && !isError && courses && courses.length === 0 && (
        <EmptyState
          icon="📚"
          label="No courses yet. Upload your first course to get started."
        />
      )}

      {!isLoading && !isError && courses && courses.length > 0 && (
        <div className="card-grid">
          {courses.map((course) => (
            <CourseCard key={course.id} course={course} />
          ))}
        </div>
      )}
    </>
  )
}
