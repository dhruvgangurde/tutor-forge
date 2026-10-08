import { useFinalizedGrades } from './hooks'
import { formatNumber } from '../../lib/formatNumber'
import { Badge } from '../../components/ui/Badge'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import { getErrorMessage } from '../../lib/api/errors'

const fmt = formatNumber

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
        <table className="data-table data-table-stack" role="table">
          <thead role="rowgroup">
            <tr role="row">
              <th role="columnheader">Student</th>
              <th role="columnheader">Assessment</th>
              <th role="columnheader">Final score</th>
              <th role="columnheader">Decision</th>
              <th role="columnheader">Released</th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {data.map((g) => (
              <tr role="row" key={g.submission_id}>
                <td role="cell" data-label="Student">{g.student_email ?? '—'}</td>
                <td role="cell" data-label="Assessment">{g.assessment_title}</td>
                <td role="cell" data-label="Final score">
                  {fmt(g.final_score)} / {fmt(g.max_score)}
                </td>
                <td role="cell" data-label="Decision">
                  <Badge variant={statusToVariant(g.action)}>{statusLabel(g.action)}</Badge>
                </td>
                <td role="cell" data-label="Released">{g.released ? new Date(g.finalized_at).toLocaleDateString() : 'Not released'}</td>
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
