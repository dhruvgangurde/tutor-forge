import { Badge } from '../../components/ui/Badge'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import type { AssessmentSummary } from '../../lib/api/types'

interface AssessmentListProps {
  assessments: AssessmentSummary[]
  selectedId: string | null
  onSelect: (assessmentId: string) => void
}

/**
 * Teacher-facing table of assessments for a course; click a row (or its
 * button) to preview it. A draft still needs the teacher's check, so its
 * button is the outlined "Review"; everything else gets the quieter "View".
 * Both do exactly what clicking the row does.
 */
export function AssessmentList({ assessments, selectedId, onSelect }: AssessmentListProps) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Questions</th>
            <th>Created</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {assessments.map((a) => {
            const isDraft = a.status === 'draft'
            return (
              <tr
                key={a.id}
                onClick={() => onSelect(a.id)}
                className={`clickable-row${a.id === selectedId ? ' row-selected' : ''}`}
              >
                <td className="cell-strong">{a.title}</td>
                <td>
                  <Badge variant={statusToVariant(a.status)}>{statusLabel(a.status)}</Badge>
                </td>
                <td>{a.status === 'generating' ? '—' : a.question_count}</td>
                <td>{new Date(a.created_at).toLocaleDateString()}</td>
                <td className="cell-actions">
                  <button
                    type="button"
                    className={isDraft ? 'btn btn-secondary btn-sm' : 'btn btn-tertiary btn-sm'}
                    aria-label={`${isDraft ? 'Review' : 'View'} ${a.title}`}
                    onClick={(e) => {
                      // The row handles the click; stop it running twice.
                      e.stopPropagation()
                      onSelect(a.id)
                    }}
                  >
                    {isDraft ? 'Review' : 'View'}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
