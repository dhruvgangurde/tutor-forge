import { formatNumber } from '../../lib/formatNumber'

export interface GradeState {
  /** True only once the teacher has finalized (approved or overridden) the grade. */
  released: boolean
  /** Readable status for a student -- never the raw backend status value. */
  label: string
  /** "4.5 / 6" once released, otherwise null. */
  score: string | null
  /** Rounded percentage once released (and the maximum is known), otherwise null. */
  percent: number | null
}

const fmt = formatNumber

/**
 * What a student may see about a submission's grade.
 *
 * The backend sets status "graded" as soon as the AI recommendation exists, but
 * that is not a grade: `final_score` is only populated once the teacher has
 * approved or overridden it, and is sent as `null` until then. The old check
 * (`final_score !== undefined`) was true for `null`, which is how an
 * unreviewed submission rendered as "Score / 6 · 0%" and "Graded / 6".
 */
export function gradeState(submission: {
  final_score?: number | null
  max_score?: number | null
}): GradeState {
  const { final_score: score, max_score: max } = submission
  if (typeof score !== 'number') {
    return { released: false, label: 'Awaiting teacher review', score: null, percent: null }
  }
  const hasMax = typeof max === 'number' && max > 0
  return {
    released: true,
    label: 'Grade released',
    score: hasMax ? `${fmt(score)} / ${fmt(max)}` : fmt(score),
    percent: hasMax ? Math.round((score / max) * 100) : null,
  }
}
