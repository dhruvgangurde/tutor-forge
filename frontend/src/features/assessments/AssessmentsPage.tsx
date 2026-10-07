import { useNavigate } from 'react-router-dom'
import { useMySubmissions, usePublishedAssessments } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { EmptyState } from '../../components/ui/EmptyState'
import { GradeStatus } from './GradeStatus'
import styles from './assessments.module.css'

export function AssessmentsPage() {
  const navigate = useNavigate()
  const { data: assessments, isLoading, error } = usePublishedAssessments()
  // A student submits each assessment once. Knowing which ones are already
  // submitted is what lets a card link to the result instead of a fresh
  // attempt that the backend would reject at the very end (409).
  const mySubmissions = useMySubmissions()
  const submissionByAssessment = new Map(
    (mySubmissions.data ?? []).map((s) => [s.assessment_id, s])
  )

  // Wait for both, so a submitted card never flashes "Start assessment".
  if (isLoading || mySubmissions.isLoading) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Assessments</h1>
          <p className="page-subtitle">Take and review your assessments.</p>
        </div>
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      </>
    )
  }

  if (error) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Assessments</h1>
          <p className="page-subtitle">Take and review your assessments.</p>
        </div>
        <div className="text-center py-8 text-red-600">
          <p>Failed to load assessments. Please try again.</p>
        </div>
      </>
    )
  }

  if (!assessments || assessments.length === 0) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Assessments</h1>
          <p className="page-subtitle">Take and review your assessments.</p>
        </div>
        <EmptyState icon="📝" label="No assessments available. Check back soon for new assessments to take." />
      </>
    )
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">Assessments</h1>
        <p className="page-subtitle">Available assessments to take and review.</p>
      </div>

      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <p className="text-sm text-gray-600">
            {assessments.length} assessment{assessments.length !== 1 ? 's' : ''} available
          </p>
          <button
            onClick={() => navigate('/assessments/submissions')}
            className="px-4 py-2 text-sm bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
          >
            View My Results
          </button>
        </div>

        <div className={styles.assessmentGrid}>
          {assessments.map((assessment) => {
            const submission = submissionByAssessment.get(assessment.id)
            return (
            <div key={assessment.id} className={styles.assessmentCard}>
              <div className={styles.cardHeader}>
                <h3 className={styles.cardTitle}>{assessment.title}</h3>
                <span className={styles.courseBadge}>{assessment.course_name}</span>
              </div>

              <div className={styles.cardContent}>
                <p className="text-sm text-gray-600">
                  {assessment.question_count} question{assessment.question_count !== 1 ? 's' : ''}
                  {' · '}
                  {assessment.total_points} point{assessment.total_points !== 1 ? 's' : ''}
                </p>
                {/* Two assessments on one course can carry the same title.
                    Without a date the cards are indistinguishable, and a
                    student has no way to know which one they are opening. */}
                <p className={styles.cardDate}>
                  {assessment.published_at
                    ? `Published ${new Date(assessment.published_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}`
                    : `Created ${new Date(assessment.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}`}
                </p>
                {submission && (
                  <GradeStatus
                    submission={submission}
                    leading={
                      <span className={`${styles.statusPill} ${styles.statusSubmitted}`}>
                        Submitted
                      </span>
                    }
                  />
                )}
              </div>

              <div className={styles.cardFooter}>
                {submission ? (
                  <button
                    type="button"
                    onClick={() => navigate(`/assessments/submissions/${submission.submission_id}`)}
                    className={styles.cardButtonSecondary}
                  >
                    View submission
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(`/assessments/${assessment.id}/take`)}
                    className={styles.cardButton}
                  >
                    Start assessment
                  </button>
                )}
              </div>
            </div>
            )
          })}
        </div>
      </div>
    </>
  )
}
