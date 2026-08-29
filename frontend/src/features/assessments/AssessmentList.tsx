import { Badge } from '../../components/ui/Badge'
import { statusToVariant } from '../../lib/statusVariant'
import type { AssessmentSummary } from '../../lib/api/types'

interface AssessmentListProps {
  assessments: AssessmentSummary[]
  selectedId: string | null
  onSelect: (assessmentId: string) => void
}

/** Teacher-facing table of assessments for a course; click a row to preview it. */
export function AssessmentList({ assessments, selectedId, onSelect }: AssessmentListProps) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Title</th>
          <th>Status</th>
          <th>Questions</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {assessments.map((a) => (
          <tr
            key={a.id}
            onClick={() => onSelect(a.id)}
            className={a.id === selectedId ? 'row-selected' : undefined}
            style={{ cursor: 'pointer' }}
          >
            <td>{a.title}</td>
            <td>
              <Badge variant={statusToVariant(a.status)}>{a.status}</Badge>
            </td>
            <td>{a.status === 'generating' ? '—' : a.question_count}</td>
            <td>{new Date(a.created_at).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
