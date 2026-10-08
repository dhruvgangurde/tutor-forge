export const MCQ_LETTERS = ['A', 'B', 'C', 'D'] as const

/**
 * Index of the MCQ option the grader will treat as correct, or null when the
 * stored key does not point at any option.
 *
 * Mirrors the backend grader exactly (agents/grading/nodes.py): it compares
 * `correct_answer.strip().upper()` to the student's letter. A key that is not
 * a single letter within range therefore marks every student wrong -- the
 * preview flags that instead of silently showing nothing as correct.
 */
export function mcqKeyIndex(
  correctAnswer: string | null | undefined,
  optionCount: number
): number | null {
  const letter = (correctAnswer ?? '').trim().toUpperCase()
  const idx = (MCQ_LETTERS as readonly string[]).indexOf(letter)
  return idx >= 0 && idx < optionCount ? idx : null
}
