import { Badge } from '../../components/ui/Badge'
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
    <table className="data-table data-table-stack">
      <thead>
        <tr>
          <th>Course</th>
          <th>Graded</th>
          <th>Awaiting grade</th>
          <th>Released score</th>
          <th>Last graded</th>
        </tr>
      </thead>
      <tbody>
        {courses.map((c) => {
          const pct = scorePercent(c.earned_points, c.possible_points)
          return (
            <tr
              key={c.course_id}
              onClick={() => onSelect(c.course_id)}
              className={`clickable-row${c.course_id === selectedId ? ' row-selected' : ''}`}
            >
              <td data-label="Course">{c.course_name}</td>
              <td data-label="Graded">{c.assessments_graded}</td>
              <td data-label="Awaiting grade">
                {c.assessments_awaiting_grade > 0 ? (
                  <Badge variant="muted">{c.assessments_awaiting_grade}</Badge>
                ) : (
                  '—'
                )}
              </td>
              <td data-label="Released score" className="cell-score">
                {/* An em dash, not 0%: nothing released yet is not a zero score. */}
                {pct === null ? '—' : `${c.earned_points} / ${c.possible_points} (${pct}%)`}
              </td>
              <td data-label="Last graded">
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
