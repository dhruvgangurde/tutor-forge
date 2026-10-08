import { useNavigate } from 'react-router-dom'
import { useMySubmissions, usePublishedAssessments } from './hooks'
import { SkeletonCards } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { GradeStatus } from './GradeStatus'
import { plural } from '../../lib/plural'
import styles from './assessments.module.css'

function Header({ subtitle }: { subtitle: string }) {
  return (
    <div className="page-header">
      <h1 className="page-title">Assessments</h1>
      <p className="page-subtitle">{subtitle}</p>
    </div>
  )
}

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
        <Header subtitle="Take and review your assessments." />
        <SkeletonCards label="Loading assessments" count={2} />
      </>
    )
  }

  if (error) {
    return (
      <>
        <Header subtitle="Take and review your assessments." />
        <ErrorBanner message="Failed to load assessments. Please try again." />
      </>
    )
  }

  if (!assessments || assessments.length === 0) {
    return (
      <>
        <Header subtitle="Take and review your assessments." />
        <EmptyState label="No assessments available. Check back soon for new assessments to take." />
      </>
    )
  }

  return (
    <>
      <Header subtitle="Available assessments to take and review." />

      <div className={styles.toolbar}>
        <p className={styles.toolbarCount}>{plural(assessments.length, 'assessment')} available</p>
        <button
          type="button"
          onClick={() => navigate('/assessments/submissions')}
          className="btn btn-secondary btn-sm"
        >
          View My Results
        </button>
      </div>

      <div className={styles.assessmentGrid}>
        {assessments.map((assessment) => {
          const submission = submissionByAssessment.get(assessment.id)
          return (
            <div key={assessment.id} className={styles.assessmentCard}>
              <h3 className={styles.cardTitle}>{assessment.title}</h3>
              <p className={styles.courseName}>{assessment.course_name}</p>

              <p className={styles.cardMeta}>
                {plural(assessment.question_count, 'question')}
                {' · '}
                {plural(assessment.total_points, 'point')}
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
                  leading={<span className={styles.submittedLabel}>Submitted</span>}
                />
              )}

              <div className={styles.cardActions}>
                {submission ? (
                  <button
                    type="button"
                    onClick={() => navigate(`/assessments/submissions/${submission.submission_id}`)}
                    className="btn btn-secondary"
                  >
                    View submission
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(`/assessments/${assessment.id}/take`)}
                    className="btn btn-primary"
                  >
                    Start assessment
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
