import { useState } from 'react'
import { useMyProgress } from './hooks'
import { CourseProgressList } from './CourseProgressList'
import { CourseProgressPanel } from './CourseProgressPanel'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { EmptyState } from '../../components/ui/EmptyState'
import { getErrorMessage } from '../../lib/api/errors'

/**
 * Student progress view.
 *
 * Shows only grades an instructor has released. A submission whose AI
 * recommendation is still awaiting review is counted as awaiting and carries no
 * score — surfacing the recommendation here would route around the
 * teacher-approval gate the whole grading flow exists to enforce.
 */
export function ProgressPage() {
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null)
  const { data: courses, isLoading, isError, error } = useMyProgress()

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">My progress</h1>
        <p className="page-subtitle">
          Concept mastery and released grades from the assessments you have taken.
        </p>
      </div>

      {isLoading && <SkeletonRows label="Loading progress…" rows={3} columns={5} />}
      {isError && <ErrorBanner message={getErrorMessage(error)} />}

      {courses && courses.length === 0 && (
        <EmptyState label="No progress yet. Take an assessment and your results will appear here." />
      )}

      {courses && courses.length > 0 && (
        <section className="detail-section">
          <h2 className="section-title">Courses ({courses.length})</h2>
          <CourseProgressList
            courses={courses}
            selectedId={selectedCourseId}
            onSelect={setSelectedCourseId}
          />
        </section>
      )}

      {selectedCourseId && (
        <div className="card course-progress-card">
          {/* Keyed by course so switching rows remounts rather than briefly
              showing the previous course's data under the new heading. */}
          <CourseProgressPanel key={selectedCourseId} courseId={selectedCourseId} />
        </div>
      )}
    </>
  )
}
