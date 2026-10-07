import { describe, expect, it } from 'vitest'
import { gradeState } from './gradeState'
import { statusLabel } from '../../lib/statusVariant'

describe('gradeState', () => {
  it('shows no score until the teacher finalizes it (final_score arrives as null)', () => {
    // Exactly what the API sends for an AI-graded, not-yet-reviewed submission.
    const g = gradeState({ final_score: null, max_score: 6 })
    expect(g).toEqual({ released: false, label: 'Awaiting teacher review', score: null, percent: null })
  })

  it('treats a missing final_score the same way', () => {
    expect(gradeState({ max_score: 6 }).released).toBe(false)
  })

  it('shows the score and percentage once released', () => {
    expect(gradeState({ final_score: 4.5, max_score: 6 })).toEqual({
      released: true,
      label: 'Grade released',
      score: '4.5 / 6',
      percent: 75,
    })
  })

  it('treats a released score of zero as released, not as pending', () => {
    const g = gradeState({ final_score: 0, max_score: 4 })
    expect(g.released).toBe(true)
    expect(g.score).toBe('0 / 4')
    expect(g.percent).toBe(0)
  })
})

describe('statusLabel', () => {
  it('turns raw backend statuses into readable text', () => {
    expect(statusLabel('pending_review')).toBe('Pending review')
    expect(statusLabel('pending_grading')).toBe('Awaiting grading')
    expect(statusLabel('some_new_status')).toBe('some new status')
  })
})
