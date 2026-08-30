import { Badge } from '../../components/ui/Badge'
import { statusToVariant } from '../../lib/statusVariant'
import type { GradingQueueItem } from '../../lib/api/types'

interface GradingQueueProps {
  items: GradingQueueItem[]
  selectedId: string | null
  onSelect: (submissionId: string) => void
}

/** Teacher-facing table of submissions awaiting review; click a row to open it. */
export function GradingQueue({ items, selectedId, onSelect }: GradingQueueProps) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Student</th>
          <th>Assessment</th>
          <th>Recommended score</th>
          <th>Status</th>
          <th>Submitted</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr
            key={item.submission_id}
            onClick={() => onSelect(item.submission_id)}
            className={item.submission_id === selectedId ? 'row-selected' : undefined}
            style={{ cursor: 'pointer' }}
          >
            <td>{item.student_email ?? '—'}</td>
            <td>{item.assessment_title ?? '—'}</td>
            <td>
              {item.recommended_score} / {item.max_score}
            </td>
            <td>
              <Badge variant={statusToVariant(item.status)}>{item.status}</Badge>
            </td>
            <td>
              {item.submitted_at ? new Date(item.submitted_at).toLocaleDateString() : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
