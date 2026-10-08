import { useNavigate } from 'react-router-dom'
import { useMySubmissions } from './hooks'
import { SkeletonCards } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { GradeStatus } from './GradeStatus'
import { gradeState } from './gradeState'
import { plural } from '../../lib/plural'
import styles from './assessments.module.css'

function Header({ subtitle }: { subtitle: string }) {
  return (
    <div className="page-header">
      <h1 className="page-title">My Submissions</h1>
      <p className="page-subtitle">{subtitle}</p>
    </div>
  )
}

export function MySubmissionsPage() {
  const navigate = useNavigate()
  const { data: submissions, isLoading, error } = useMySubmissions()

  if (isLoading) {
    return (
      <>
        <Header subtitle="View your assessment results." />
        <SkeletonCards label="Loading submissions" count={2} />
      </>
    )
  }

  if (error) {
    return (
      <>
        <Header subtitle="View your assessment results." />
        <ErrorBanner message="Failed to load submissions. Please try again." />
      </>
    )
  }

  if (!submissions || submissions.length === 0) {
    return (
      <>
        <Header subtitle="View your assessment results." />
        <EmptyState
          label="No submissions yet. Start taking assessments to see your results here."
          action={
            <button type="button" onClick={() => navigate('/assessments')} className="btn btn-primary">
              Browse Assessments
            </button>
          }
        />
      </>
    )
  }

  return (
    <>
      <Header subtitle="View your assessment results and feedback." />

      <div className={styles.toolbar}>
        <p className={styles.toolbarCount}>{plural(submissions.length, 'submission')}</p>
        <button type="button" onClick={() => navigate('/assessments')} className="btn btn-secondary btn-sm">
          Take Assessment
        </button>
      </div>

      <div className={styles.assessmentGrid}>
        {submissions.map((submission) => {
          const open = () => navigate(`/assessments/submissions/${submission.submission_id}`)
          return (
            <div
              key={submission.submission_id}
              className={`${styles.assessmentCard} ${styles.clickableCard}`}
              onClick={open}
            >
              <h3 className={styles.cardTitle}>{submission.assessment_title}</h3>
              <p className={styles.courseName}>{submission.course_name}</p>

              {/* GradeStatus is the source of truth: no score until the
                  teacher finalizes it -- see gradeState. */}
              <GradeStatus submission={submission} />
              {!gradeState(submission).released && (
                <p className={styles.statusNote}>
                  Your score will appear here once your teacher has reviewed it.
                </p>
              )}

              <div className={styles.cardFooterRow}>
                <span className={styles.cardDate}>
                  Submitted: {new Date(submission.submitted_at).toLocaleDateString()}
                </span>
                {/* No handler of its own: the click reaches the card, as before. */}
                <button type="button" className="btn btn-tertiary btn-sm">
                  View Details
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}
