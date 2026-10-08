import { useNavigate, useParams } from 'react-router-dom'
import { useSubmissionDetail } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
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
    return <div className="text-center py-8">Invalid submission ID</div>
  }

  if (isLoading) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Loading Submission</h1>
        </div>
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      </>
    )
  }

  if (error || !submission) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">Submission Not Found</h1>
        </div>
        <div className="text-center py-8">
          <p className="text-red-600 mb-4">Failed to load submission.</p>
          <button
            onClick={() => navigate('/assessments/submissions')}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Back to My Submissions
          </button>
        </div>
      </>
    )
  }

  const released = gradeState(submission).released

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">{submission.assessment_title}</h1>
        <p className="page-subtitle">{submission.course_name}</p>
      </div>

      <div className={styles.detailContainer}>
        {/* Header Card */}
        <div className={styles.headerCard}>
          <div className={styles.headerContent}>
            <div>
              <p className="text-sm text-gray-600">Submitted</p>
              <p className="font-medium">{new Date(submission.submitted_at).toLocaleString()}</p>
            </div>

            <div>
              <p className="text-sm text-gray-600">Status</p>
              {/* No score (and no "0%") until the teacher finalizes it. */}
              <GradeStatus submission={submission} showPercent />
              {!released && (
                <p className={styles.statusNote}>
                  Your score will appear here once your teacher has reviewed it.
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Responses */}
        <div className={styles.responsesContainer}>
          <h2 className={styles.sectionTitle}>Your Answers</h2>

          {submission.responses.map((response, idx) => (
            <div key={response.question_id} className={styles.responseCard}>
              <div className={styles.responseHeader}>
                <span className={styles.questionNumber}>Question {idx + 1}</span>
                <span className={styles.maxPoints}>{plural(response.max_points, 'pt', 'pts')}</span>
              </div>

              <h3 className={styles.questionText}>{response.question_text}</h3>

              <div className={styles.answerDisplay}>
                <strong>Your answer:</strong>
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
        </div>

        {/* Instructor feedback exists only once the grade is finalized. */}
        {released && submission.feedback && (
          <div className={styles.feedbackCard}>
            <h2 className={styles.sectionTitle}>Instructor Feedback</h2>
            <Markdown className={styles.feedbackContent}>{submission.feedback}</Markdown>
            {submission.graded_at && (
              <p className="text-sm text-gray-500 mt-4">
                Graded on {new Date(submission.graded_at).toLocaleString()}
              </p>
            )}
          </div>
        )}

        {/* Action Buttons */}
        <div className={styles.actionButtons}>
          <button
            onClick={() => navigate('/assessments/submissions')}
            className="px-6 py-2 border border-gray-300 rounded hover:bg-gray-50"
          >
            Back to My Submissions
          </button>
          <button
            onClick={() => navigate('/assessments')}
            className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Browse Assessments
          </button>
        </div>
      </div>
    </>
  )
}
