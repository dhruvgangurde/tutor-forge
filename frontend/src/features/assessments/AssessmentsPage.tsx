import { useNavigate } from 'react-router-dom'
import { usePublishedAssessments } from './hooks'
import { Spinner } from '../../components/ui/Spinner'
import { EmptyState } from '../../components/ui/EmptyState'
import styles from './assessments.module.css'

export function AssessmentsPage() {
  const navigate = useNavigate()
  const { data: assessments, isLoading, error } = usePublishedAssessments()

  if (isLoading) {
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
          {assessments.map((assessment) => (
            <div key={assessment.id} className={styles.assessmentCard}>
              <div className={styles.cardHeader}>
                <h3 className={styles.cardTitle}>{assessment.title}</h3>
                <span className={styles.courseBadge}>{assessment.course_name}</span>
              </div>

              <div className={styles.cardContent}>
                <p className="text-sm text-gray-600">
                  {assessment.question_count} question{assessment.question_count !== 1 ? 's' : ''}
                </p>
              </div>

              <div className={styles.cardFooter}>
                <button
                  onClick={() => navigate(`/assessments/${assessment.id}/take`)}
                  className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
                >
                  Start Assessment
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
