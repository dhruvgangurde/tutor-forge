import { useNavigate, useParams } from 'react-router-dom'
import { useSubmissionDetail } from './hooks'
import { SkeletonBlock } from '../../components/ui/Skeleton'
import { NotFoundState } from '../../components/ui/NotFoundState'
import { Markdown } from '../../components/ui/Markdown'
import { GradeStatus } from './GradeStatus'
import { gradeState } from './gradeState'
import { plural } from '../../lib/plural'
import styles from './assessments.module.css'

export function SubmissionDetailPage() {
  const { submissionId } = useParams<{ submissionId: string }>()
  const navigate = useNavigate()
  const { data: submission, isLoading, error } = useSubmissionDetail(submissionId || '', true)

  if (!submissionId) {
    return <p className="placeholder-label">Invalid submission ID</p>
  }

  if (isLoading) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Loading Submission</h1>
        </div>
        <div className={`card ${styles.skeletonPanel}`} aria-hidden="true">
          <SkeletonBlock width="35%" height="1.2rem" />
          <SkeletonBlock width="60%" />
          <SkeletonBlock width="80%" />
        </div>
      </>
    )
  }

  if (error || !submission) {
    return (
      <NotFoundState
        title="Submission Not Found"
        message="Failed to load submission."
        linkTo="/assessments/submissions"
        linkLabel="Back to My Submissions"
      />
    )
  }

  const released = gradeState(submission).released

  return (
    <div className={styles.readingColumn}>
      <div className="page-header">
        <h1 className="page-title">{submission.assessment_title}</h1>
        <p className="page-subtitle">{submission.course_name}</p>
      </div>

      {/* Summary */}
      <div className={`card ${styles.summaryCard}`}>
        <div>
          <p className={styles.summaryLabel}>Submitted</p>
          <p className={styles.summaryValue}>{new Date(submission.submitted_at).toLocaleString()}</p>
        </div>

        <div>
          <p className={styles.summaryLabel}>Status</p>
          {/* No score (and no "0%") until the teacher finalizes it. */}
          <GradeStatus submission={submission} showPercent />
          {!released && (
            <p className={styles.statusNote}>
              Your score will appear here once your teacher has reviewed it.
            </p>
          )}
        </div>
      </div>

      {/* Responses */}
      <section className={styles.answersSection}>
        <h2 className={styles.sectionTitle}>Your Answers</h2>

        {submission.responses.map((response, idx) => (
          <div key={response.question_id} className={`card ${styles.responseCard}`}>
            <div className={styles.responseHeader}>
              <span className={styles.questionNumber}>Question {idx + 1}</span>
              <span className={styles.maxPoints}>{plural(response.max_points, 'pt', 'pts')}</span>
            </div>

            <h3 className={styles.questionText}>{response.question_text}</h3>

            <div className={styles.answerDisplay}>
              <strong className={styles.answerLabel}>Your answer:</strong>
              {response.student_choice ? (
                <div className={styles.answerContent}>Choice {response.student_choice}</div>
              ) : response.student_answer ? (
                <div className={styles.answerContent}>{response.student_answer}</div>
              ) : (
                <div className={styles.noAnswer}>Not answered</div>
              )}
            </div>
          </div>
        ))}
      </section>

      {/* Instructor feedback exists only once the grade is finalized. */}
      {released && submission.feedback && (
        <section className={`card ${styles.feedbackCard}`}>
          <h2 className={styles.sectionTitle}>Instructor Feedback</h2>
          <Markdown className={styles.feedbackContent}>{submission.feedback}</Markdown>
          {submission.graded_at && (
            <p className={styles.gradedOn}>Graded on {new Date(submission.graded_at).toLocaleString()}</p>
          )}
        </section>
      )}

      <div className={styles.actionButtons}>
        <button type="button" onClick={() => navigate('/assessments/submissions')} className="btn btn-secondary">
          Back to My Submissions
        </button>
        <button type="button" onClick={() => navigate('/assessments')} className="btn btn-primary">
          Browse Assessments
        </button>
      </div>
    </div>
  )
}
