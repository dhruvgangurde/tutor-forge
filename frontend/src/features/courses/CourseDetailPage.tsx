import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useCourse, useCourseStructure } from './hooks'
import { useCourseAssessments } from '../assessments/hooks'
import { ConceptTree } from './ConceptTree'
import { AssessmentGenerateForm } from '../assessments/AssessmentGenerateForm'
import { AssessmentList } from '../assessments/AssessmentList'
import { AssessmentPreview } from '../assessments/AssessmentPreview'
import { Spinner } from '../../components/ui/Spinner'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { Badge } from '../../components/ui/Badge'
import { EmptyState } from '../../components/ui/EmptyState'
import { statusToVariant } from '../../lib/statusVariant'
import { getErrorMessage } from '../../lib/api/errors'

export function CourseDetailPage() {
  const { courseId } = useParams<{ courseId: string }>()
  const [showGenerateForm, setShowGenerateForm] = useState(false)
  const [selectedAssessmentId, setSelectedAssessmentId] = useState<string | null>(null)

  const { data: course, isLoading, isError, error } = useCourse(courseId ?? '')
  const isReady = course?.status === 'ready'

  const structureQuery = useCourseStructure(courseId ?? '', isReady)
  const assessmentsQuery = useCourseAssessments(isReady ? (courseId ?? '') : '')

  if (isLoading) return <Spinner label="Loading course…" />
  if (isError) return <ErrorBanner message={getErrorMessage(error)} />
  if (!course) return null

  return (
    <>
      <div className="page-header">
        <Link to="/courses" className="back-link">
          ← Back to courses
        </Link>
        <div className="page-header-row">
          <h1 className="page-title">{course.name}</h1>
          <Badge variant={statusToVariant(course.status)}>{course.status}</Badge>
        </div>
      </div>

      {course.status === 'failed' && (
        <ErrorBanner message="Ingestion failed for this course. Please upload it again." />
      )}

      {(course.status === 'pending' || course.status === 'ingesting') && (
        <Spinner label="Ingesting course materials — this can take a few minutes…" />
      )}

      {isReady && (
        <>
          <section className="detail-section">
            <h2 className="section-title">Course structure</h2>
            {structureQuery.isLoading && <Spinner label="Loading structure…" />}
            {structureQuery.isError && (
              <ErrorBanner message={getErrorMessage(structureQuery.error)} />
            )}
            {structureQuery.data && <ConceptTree structure={structureQuery.data} />}
          </section>

          <section className="detail-section">
            <div className="page-header-row">
              <h2 className="section-title">Assessments</h2>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowGenerateForm((v) => !v)}
              >
                {showGenerateForm ? 'Cancel' : 'Generate assessment'}
              </button>
            </div>

            {showGenerateForm && (
              <div className="card upload-panel">
                <AssessmentGenerateForm
                  courseId={courseId ?? ''}
                  onDone={(newId) => {
                    setShowGenerateForm(false)
                    setSelectedAssessmentId(newId)
                  }}
                />
              </div>
            )}

            {assessmentsQuery.isLoading && <Spinner label="Loading assessments…" />}
            {assessmentsQuery.isError && (
              <ErrorBanner message={getErrorMessage(assessmentsQuery.error)} />
            )}

            {assessmentsQuery.data && assessmentsQuery.data.length === 0 && (
              <EmptyState icon="📝" label="No assessments generated yet for this course." />
            )}

            {assessmentsQuery.data && assessmentsQuery.data.length > 0 && (
              <AssessmentList
                assessments={assessmentsQuery.data}
                selectedId={selectedAssessmentId}
                onSelect={setSelectedAssessmentId}
              />
            )}

            {selectedAssessmentId && (
              <div className="card">
                <AssessmentPreview
                  assessmentId={selectedAssessmentId}
                  courseId={courseId ?? ''}
                />
              </div>
            )}
          </section>
        </>
      )}
    </>
  )
}
