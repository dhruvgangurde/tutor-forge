import { Badge } from '../../components/ui/Badge'
import { formatNumber } from '../../lib/formatNumber'
import type { CourseProgressSummary } from '../../lib/api/types'

interface CourseProgressListProps {
  courses: CourseProgressSummary[]
  selectedId: string | null
  onSelect: (courseId: string) => void
}

/** Percentage of released points earned, or null when nothing is graded yet. */
export function scorePercent(earned: number, possible: number): number | null {
  if (possible <= 0) return null
  return Math.round((earned / possible) * 100)
}

/** Student-facing table of courses they have work in; click a row for detail. */
export function CourseProgressList({
  courses,
  selectedId,
  onSelect,
}: CourseProgressListProps) {
  return (
    <div className="table-scroll">
    <table className="data-table data-table-stack" role="table">
      <thead role="rowgroup">
        <tr role="row">
          <th role="columnheader">Course</th>
          <th role="columnheader">Graded</th>
          <th role="columnheader">Awaiting grade</th>
          <th role="columnheader">Released score</th>
          <th role="columnheader">Last graded</th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {courses.map((c) => {
          const pct = scorePercent(c.earned_points, c.possible_points)
          return (
            <tr
              role="row"
              key={c.course_id}
              onClick={() => onSelect(c.course_id)}
              className={`clickable-row${c.course_id === selectedId ? ' row-selected' : ''}`}
            >
              <td role="cell" data-label="Course">
                {/* The keyboard way in: the row's click stays for the mouse. */}
                <button
                  type="button"
                  className="row-action"
                  aria-current={c.course_id === selectedId ? 'true' : undefined}
                  onClick={(e) => {
                    e.stopPropagation()
                    onSelect(c.course_id)
                  }}
                >
                  {c.course_name}
                </button>
              </td>
              <td role="cell" data-label="Graded">{c.assessments_graded}</td>
              <td role="cell" data-label="Awaiting grade">
                {c.assessments_awaiting_grade > 0 ? (
                  <Badge variant="muted">{c.assessments_awaiting_grade}</Badge>
                ) : (
                  '—'
                )}
              </td>
              <td role="cell" data-label="Released score" className="cell-score">
                {/* An em dash, not 0%: nothing released yet is not a zero score. */}
                {pct === null
                  ? '—'
                  : `${formatNumber(c.earned_points)} / ${formatNumber(c.possible_points)} (${pct}%)`}
              </td>
              <td role="cell" data-label="Last graded">
                {c.last_graded_at
                  ? new Date(c.last_graded_at).toLocaleDateString()
                  : '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
    </div>
  )
}
