import { useFinalizedGrades } from './hooks'
import { Badge } from '../../components/ui/Badge'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import { getErrorMessage } from '../../lib/api/errors'

function fmt(n: number): string {
  return String(Math.round(n * 100) / 100)
}

/**
 * Read-only history of released grades, shown under the pending queue.
 *
 * Once approved or overridden, a grade used to disappear from the Grading page
 * with no way to look back at it. Nothing here is clickable or editable: a
 * released grade is final (see grading/service.finalize_grade).
 */
export function FinalizedGrades() {
  const { data, isLoading, isError, error } = useFinalizedGrades()

  return (
    <section className="detail-section">
      <h2 className="section-title">Finalized{data ? ` (${data.length})` : ''}</h2>
      {isLoading && <SkeletonRows label="Loading finalized grades…" rows={2} columns={5} />}
      {isError && <ErrorBanner message={getErrorMessage(error)} />}
      {data && data.length === 0 && <p className="card-meta">No grades have been finalized yet.</p>}
      {data && data.length > 0 && (
        <div className="card table-card">
        <div className="table-scroll">
        <table className="data-table data-table-stack">
          <thead>
            <tr>
              <th>Student</th>
              <th>Assessment</th>
              <th>Final score</th>
              <th>Decision</th>
              <th>Released</th>
            </tr>
          </thead>
          <tbody>
            {data.map((g) => (
              <tr key={g.submission_id}>
                <td data-label="Student">{g.student_email ?? '—'}</td>
                <td data-label="Assessment">{g.assessment_title}</td>
                <td data-label="Final score">
                  {fmt(g.final_score)} / {fmt(g.max_score)}
                </td>
                <td data-label="Decision">
                  <Badge variant={statusToVariant(g.action)}>{statusLabel(g.action)}</Badge>
                </td>
                <td data-label="Released">{g.released ? new Date(g.finalized_at).toLocaleDateString() : 'Not released'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        </div>
      )}
    </section>
  )
}
