import { useMyCourseProgress } from './hooks'
import { scorePercent } from './CourseProgressList'
import { Badge } from '../../components/ui/Badge'
import { SkeletonRows } from '../../components/ui/Skeleton'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { getErrorMessage } from '../../lib/api/errors'
import { statusLabel } from '../../lib/statusVariant'
import type { BadgeVariant } from '../../components/ui/Badge'

interface CourseProgressPanelProps {
  courseId: string
}

/**
 * Mastery bands. Kept coarse on purpose: the underlying number is a ratio of
 * points on however many questions happened to be tagged to a concept, which
 * does not support finer distinctions than "solid / getting there / needs work".
 */
function masteryVariant(mastery: number): BadgeVariant {
  if (mastery >= 0.8) return 'success'
  if (mastery >= 0.5) return 'warning'
  return 'danger'
}

function masteryLabel(mastery: number): string {
  return `${Math.round(mastery * 100)}%`
}

/** One course's concept mastery, released grades, and tutoring engagement. */
export function CourseProgressPanel({ courseId }: CourseProgressPanelProps) {
  const { data, isLoading, isError, error } = useMyCourseProgress(courseId)

  if (isLoading) return <SkeletonRows label="Loading progress…" rows={3} columns={3} />
  if (isError) return <ErrorBanner message={getErrorMessage(error)} />
  if (!data) return null

  const pct = scorePercent(data.earned_points, data.possible_points)

  return (
    <div className="course-progress">
      <div className="card-header-row">
        <h3 className="card-title">{data.course_name}</h3>
        {pct !== null && <Badge variant="info">{pct}%</Badge>}
      </div>

      <div className="score-summary">
        <div className="score-summary-block">
          <span className="score-summary-label">Released score</span>
          <span className="score-summary-value">
            {data.earned_points}{' '}
            <span className="score-summary-max">/ {data.possible_points}</span>
          </span>
        </div>
        <p className="score-summary-note">
          Only grades your instructor has reviewed and released are counted here.
        </p>
      </div>

      <h4 className="section-title">Concept mastery</h4>
      {data.concepts.length === 0 ? (
        <p className="card-meta">
          {data.untagged_note ??
            'No concept mastery yet — it appears once a graded assessment covers a concept.'}
        </p>
      ) : (
        <ul className="mastery-list">
          {data.concepts.map((c) => (
            <li key={c.concept_id} className="mastery-item">
              <div className="mastery-header">
                <span className="mastery-name">{c.concept_name}</span>
                <Badge variant={masteryVariant(c.mastery)}>{masteryLabel(c.mastery)}</Badge>
              </div>
              <div className="mastery-meta">
                <span className="mastery-chapter">{c.chapter_title}</span>
                <span className="mastery-points">
                  {c.earned_points} / {c.possible_points} pts across {c.attempts}{' '}
                  {c.attempts === 1 ? 'question' : 'questions'}
                </span>
              </div>
              <div
                className="mastery-bar"
                role="img"
                aria-label={`${c.concept_name}: ${masteryLabel(c.mastery)} mastery`}
              >
                <div
                  className="mastery-bar-fill"
                  data-band={masteryVariant(c.mastery)}
                  style={{ width: `${Math.round(c.mastery * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}

      <h4 className="section-title">Released grades</h4>
      {data.results.length === 0 ? (
        <p className="card-meta">Nothing released yet for this course.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Assessment</th>
              <th>Score</th>
              <th>Decision</th>
              <th>Released</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((r) => (
              <tr key={r.submission_id}>
                <td>{r.assessment_title}</td>
                <td>
                  {r.final_score} / {r.max_score}
                </td>
                <td>
                  <Badge variant={r.action === 'approved' ? 'success' : 'info'}>
                    {statusLabel(r.action)}
                  </Badge>
                </td>
                <td>{new Date(r.finalized_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4 className="section-title">Tutoring</h4>
      <p className="card-meta">
        {data.tutoring_sessions === 0
          ? 'No tutoring sessions on this course yet.'
          : `${data.tutoring_sessions} ${
              data.tutoring_sessions === 1 ? 'session' : 'sessions'
            }, ${data.tutoring_messages} ${
              data.tutoring_messages === 1 ? 'question' : 'questions'
            } asked.`}
      </p>
    </div>
  )
}
