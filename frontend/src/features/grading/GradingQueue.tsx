import { Badge } from '../../components/ui/Badge'
import { formatNumber } from '../../lib/formatNumber'
import { statusLabel, statusToVariant } from '../../lib/statusVariant'
import type { GradingQueueItem } from '../../lib/api/types'

interface GradingQueueProps {
  items: GradingQueueItem[]
  selectedId: string | null
  onSelect: (submissionId: string) => void
}

/** Teacher-facing table of submissions awaiting review; click a row to open it. */
export function GradingQueue({ items, selectedId, onSelect }: GradingQueueProps) {
  return (
    <div className="table-scroll">
    <table className="data-table data-table-stack" role="table">
      <thead role="rowgroup">
        <tr role="row">
          <th role="columnheader">Student</th>
          <th role="columnheader">Assessment</th>
          <th role="columnheader">Recommended score</th>
          <th role="columnheader">Status</th>
          <th role="columnheader">Submitted</th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {items.map((item) => (
          <tr
            role="row"
            key={item.submission_id}
            onClick={() => onSelect(item.submission_id)}
            className={`clickable-row${item.submission_id === selectedId ? ' row-selected' : ''}`}
          >
            <td role="cell" data-label="Student">
              {/* The keyboard way in: the row's click stays for the mouse. */}
              <button
                type="button"
                className="row-action"
                aria-current={item.submission_id === selectedId ? 'true' : undefined}
                onClick={(e) => {
                  e.stopPropagation()
                  onSelect(item.submission_id)
                }}
              >
                {item.student_email ?? '—'}
              </button>
            </td>
            <td role="cell" data-label="Assessment">{item.assessment_title ?? '—'}</td>
            <td role="cell" data-label="Recommended score">
              {formatNumber(item.recommended_score)} / {formatNumber(item.max_score)}
            </td>
            <td role="cell" data-label="Status">
              <Badge variant={statusToVariant(item.status)}>{statusLabel(item.status)}</Badge>
            </td>
            <td role="cell" data-label="Submitted">
              {item.submitted_at ? new Date(item.submitted_at).toLocaleDateString() : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}
