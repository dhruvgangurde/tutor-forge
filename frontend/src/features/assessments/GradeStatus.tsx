import type { ReactNode } from 'react'
import { gradeState } from './gradeState'
import { Badge } from '../../components/ui/Badge'
import styles from './assessments.module.css'

interface GradeStatusProps {
  submission: { final_score?: number | null; max_score?: number | null }
  /** Append the percentage to a released score. */
  showPercent?: boolean
  /** Rendered first on the same line, e.g. a "Submitted" pill. */
  leading?: ReactNode
}

/**
 * A submission's grade as a student may see it: "Awaiting teacher review" with
 * no number until the teacher finalizes, then "Grade released" and the score.
 * Status is a chip with an icon and the word (amber clock while awaiting,
 * accent check once released); the released score is set in the serif.
 */
export function GradeStatus({ submission, showPercent = false, leading }: GradeStatusProps) {
  const grade = gradeState(submission)
  return (
    <div className={styles.statusRow}>
      {leading}
      <Badge variant={grade.released ? 'success' : 'warning'}>{grade.label}</Badge>
      {grade.released && grade.score && (
        <span className={styles.scoreValue}>
          {grade.score}
          {showPercent && grade.percent !== null ? ` (${grade.percent}%)` : ''}
        </span>
      )}
    </div>
  )
}
