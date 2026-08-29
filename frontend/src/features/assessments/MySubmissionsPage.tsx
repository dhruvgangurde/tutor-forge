import { useNavigate } from 'react-router-dom'
import { useMySubmissions } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { EmptyState } from '../../components/ui/EmptyState'
import styles from './assessments.module.css'

export function MySubmissionsPage() {
  const navigate = useNavigate()
  const { data: submissions, isLoading, error } = useMySubmissions()

  if (isLoading) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">My Submissions</h1>
          <p className="page-subtitle">View your assessment results.</p>
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
          <h1 className="page-title">My Submissions</h1>
          <p className="page-subtitle">View your assessment results.</p>
        </div>
        <div className="text-center py-8 text-red-600">
          <p>Failed to load submissions. Please try again.</p>
        </div>
      </>
    )
  }

  if (!submissions || submissions.length === 0) {
    return (
      <>
        <div className="page-header">
          <h1 className="page-title">My Submissions</h1>
          <p className="page-subtitle">View your assessment results.</p>
        </div>
        <EmptyState icon="📋" label="No submissions yet. Start taking assessments to see your results here." />
        <div className="text-center mt-6">
          <button
            onClick={() => navigate('/assessments')}
            className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Browse Assessments
          </button>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="page-header">
        <h1 className="page-title">My Submissions</h1>
        <p className="page-subtitle">View your assessment results and feedback.</p>
      </div>

      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <p className="text-sm text-gray-600">
            {submissions.length} submission{submissions.length !== 1 ? 's' : ''}
          </p>
          <button
            onClick={() => navigate('/assessments')}
            className="px-4 py-2 text-sm bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
          >
            Take Assessment
          </button>
        </div>

        <div className={styles.assessmentGrid}>
          {submissions.map((submission) => (
            <div
              key={submission.submission_id}
              className={styles.assessmentCard}
              onClick={() => navigate(`/assessments/submissions/${submission.submission_id}`)}
            >
              <div className={styles.cardHeader}>
                <h3 className={styles.cardTitle}>{submission.assessment_title}</h3>
                <span className={styles.courseBadge}>{submission.course_name}</span>
              </div>

              <div className={styles.cardContent}>
                <div className="text-sm text-gray-600 mb-2">
                  Submitted: {new Date(submission.submitted_at).toLocaleDateString()}
                </div>

                <div className="flex items-center justify-between">
                  <span className={`text-sm px-2 py-1 rounded ${getStatusColor(submission.status)}`}>
                    {submission.status === 'pending_grading' ? 'Pending Review' : 'Graded'}
                  </span>

                  {submission.final_score !== undefined && submission.max_score !== undefined && (
                    <span className="text-lg font-bold text-blue-600">
                      {submission.final_score} / {submission.max_score}
                    </span>
                  )}
                </div>
              </div>

              <div className={styles.cardFooter}>
                <button className="w-full px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200 transition-colors">
                  View Details
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

function getStatusColor(status: string): string {
  if (status === 'pending_grading') {
    return 'bg-yellow-100 text-yellow-800'
  }
  if (status === 'graded') {
    return 'bg-green-100 text-green-800'
  }
  return 'bg-gray-100 text-gray-800'
}
