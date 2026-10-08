import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useCourse, useCourseStructure } from './hooks'
import { useCourseAssessments } from '../assessments/hooks'
import { ConceptTree } from './ConceptTree'
import { CourseEnrollmentPanel } from './CourseEnrollmentPanel'
import { AssessmentGenerateForm } from '../assessments/AssessmentGenerateForm'
import { AssessmentList } from '../assessments/AssessmentList'
import { AssessmentPreview } from '../assessments/AssessmentPreview'
import { Spinner } from '../../components/ui/Spinner'
import { SkeletonBlock, SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { Badge } from '../../components/ui/Badge'
import { EmptyState } from '../../components/ui/EmptyState'
import { TabPanel, Tabs } from '../../components/ui/Tabs'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import { plural } from '../../lib/plural'
import { NotFoundState } from '../../components/ui/NotFoundState'
import { getErrorMessage, isNotFoundError } from '../../lib/api/errors'

// Three sections behind text tabs, so a long outline no longer pushes
// Assessments and Students out of reach. No Settings tab: the course page has
// no settings content today (archive/delete live on the course card).
const TABS = [
  { id: 'outline', label: 'Outline' },
  { id: 'assessments', label: 'Assessments' },
  { id: 'students', label: 'Students' },
]

export function CourseDetailPage() {
  const { courseId } = useParams<{ courseId: string }>()
  const [showGenerateForm, setShowGenerateForm] = useState(false)
  const [selectedAssessmentId, setSelectedAssessmentId] = useState<string | null>(null)
  const [tab, setTab] = useState('assessments')

  const { data: course, isLoading, isError, error } = useCourse(courseId ?? '')
  const isReady = course?.status === 'ready'

  const structureQuery = useCourseStructure(courseId ?? '', isReady)
  const assessmentsQuery = useCourseAssessments(isReady ? (courseId ?? '') : '')

  if (isLoading) {
    return (
      <>
        <span className="sr-only" role="status">Loading course…</span>
        <div className="course-skeleton" aria-hidden="true">
          <SkeletonBlock width="8rem" />
          <SkeletonBlock width="45%" height="2.4rem" />
          <SkeletonBlock width="30%" />
          <SkeletonBlock height="2.75rem" />
        </div>
      </>
    )
  }
  if (isError) {
    // A missing course and a malformed id in the URL are the same thing to a
    // teacher: there is no such course here. Either way, offer the way back.
    if (isNotFoundError(error)) {
      return (
        <NotFoundState
          title="Course not found"
          message="This course doesn't exist, or it isn't one of yours."
          linkTo="/courses"
          linkLabel="Back to courses"
        />
      )
    }
    return (
      <>
        <div className="page-header">
          <Link to="/courses" className="back-link">
            ← Back to courses
          </Link>
        </div>
        <ErrorBanner message={getErrorMessage(error)} />
      </>
    )
  }
  if (!course) return null

  const students = (
    // Independent of ingestion status: a teacher can set up the class roster
    // while the material is still being processed.
    <section className="detail-section">
      <h2 className="section-title">Students</h2>
      <CourseEnrollmentPanel courseId={courseId ?? ''} />
    </section>
  )

  return (
    <>
      <div className="page-header course-header">
        <Link to="/courses" className="back-link">
          ← Back to courses
        </Link>
        <div className="course-title-row">
          <h1 className="page-title">{course.name}</h1>
          <Badge variant={statusToVariant(course.status)}>{statusLabel(course.status)}</Badge>
        </div>
        <p className="page-subtitle">
          {/* chapter_count is null until the course is ready: show nothing then, not 0. */}
          {course.chapter_count !== null && course.chapter_count !== undefined && (
            <>{plural(course.chapter_count, 'chapter')} · </>
          )}
          uploaded {new Date(course.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}
        </p>
      </div>

      {course.status === 'failed' && (
        <ErrorBanner
          message={course.failure_reason ?? 'Processing the course materials failed. Try uploading the course again.'}
        />
      )}

      {(course.status === 'pending' || course.status === 'ingesting') && (
        <Spinner label="Ingesting course materials — this can take a few minutes…" />
      )}

      {/* The outline and assessments exist only for a ready course; until then
          the roster is the only section, so no tabs are needed. */}
      {!isReady && students}

      {isReady && (
        <>
          <Tabs label="Course sections" tabs={TABS} active={tab} onChange={setTab} idPrefix="course" />

          <TabPanel idPrefix="course" id="outline" active={tab}>
            <section className="detail-section detail-section-first">
              <h2 className="section-title">Course structure</h2>
              {structureQuery.isLoading && <SkeletonRows label="Loading structure…" rows={4} columns={1} />}
              {structureQuery.isError && (
                <ErrorBanner message={getErrorMessage(structureQuery.error)} />
              )}
              {structureQuery.data && <ConceptTree structure={structureQuery.data} />}
            </section>
          </TabPanel>

          <TabPanel idPrefix="course" id="assessments" active={tab}>
            <section className="detail-section detail-section-first">
              <div className="page-header-row section-heading-row">
                <h2 className="section-title">Assessments</h2>
                <button
                  type="button"
                  className={showGenerateForm ? 'btn btn-secondary btn-sm' : 'btn btn-primary btn-sm'}
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

              {assessmentsQuery.isLoading && <SkeletonRows label="Loading assessments…" rows={3} columns={5} />}
              {assessmentsQuery.isError && (
                <ErrorBanner message={getErrorMessage(assessmentsQuery.error)} />
              )}

              {assessmentsQuery.data && assessmentsQuery.data.length === 0 && (
                <EmptyState label="No assessments generated yet for this course." />
              )}

              {assessmentsQuery.data && assessmentsQuery.data.length > 0 && (
                <div className="card table-card">
                  <AssessmentList
                    assessments={assessmentsQuery.data}
                    selectedId={selectedAssessmentId}
                    onSelect={setSelectedAssessmentId}
                  />
                </div>
              )}

              {selectedAssessmentId && (
                <div className="card assessment-preview-card">
                  <AssessmentPreview
                    assessmentId={selectedAssessmentId}
                    courseId={courseId ?? ''}
                  />
                </div>
              )}
            </section>
          </TabPanel>

          <TabPanel idPrefix="course" id="students" active={tab}>
            {students}
          </TabPanel>
        </>
      )}
    </>
  )
}
