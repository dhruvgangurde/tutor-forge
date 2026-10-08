import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CourseProgressDetail } from './api/types'
import { formatNumber } from './formatNumber'
import { plural } from './plural'
import { CourseProgressPanel } from '../features/progress/CourseProgressPanel'

describe('formatNumber (display only)', () => {
  it('shows at most one decimal and no trailing zero', () => {
    expect(formatNumber(3.2142857142857144)).toBe('3.2')
    expect(formatNumber(4.5)).toBe('4.5')
    expect(formatNumber(6)).toBe('6')
    expect(formatNumber(6.0)).toBe('6')
    expect(formatNumber(2.3333)).toBe('2.3')
    expect(formatNumber(0.25)).toBe('0.3')
    expect(formatNumber(0.04)).toBe('0')
    expect(formatNumber(-0.04)).toBe('0')
    expect(formatNumber(0.1 + 0.2)).toBe('0.3')
  })

  it('never prints NaN or Infinity', () => {
    expect(formatNumber(Number.NaN)).toBe('—')
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe('—')
  })

  it('is used for fractional counts in plural()', () => {
    expect(plural(6.25, 'point')).toBe('6.3 points')
    expect(plural(1, 'pt', 'pts')).toBe('1 pt')
  })
})

const DETAIL: CourseProgressDetail = {
  course_id: 'c1',
  course_name: 'DAA Lab Guide',
  earned_points: 4.5,
  possible_points: 6,
  concepts: [
    {
      concept_id: 'k1',
      concept_name: 'Merge Operation',
      chapter_title: 'Merge Sort',
      attempts: 2,
      earned_points: 3.2142857142857144,
      possible_points: 5,
      mastery: 0.6428571428571429,
    },
  ],
  results: [
    {
      submission_id: 's1',
      assessment_title: 'Sorting Quiz',
      final_score: 2.3333,
      max_score: 6,
      action: 'approved',
      finalized_at: '2026-10-07T00:00:00Z',
      submitted_at: '2026-10-07T00:00:00Z',
    },
  ],
  tutoring_sessions: 1,
  tutoring_messages: 3,
  untagged_note: null,
}

vi.mock('../features/progress/hooks', () => ({
  useMyCourseProgress: () => ({ data: DETAIL, isLoading: false, isError: false, error: null }),
}))

describe('Progress mastery line', () => {
  it('shows rounded points, not the raw float sum', () => {
    render(<CourseProgressPanel courseId="c1" />)
    expect(screen.getByText(/3\.2 \/ 5 pts across 2 questions/)).toBeInTheDocument()
    expect(screen.queryByText(/3\.2142857/)).not.toBeInTheDocument()
    expect(screen.getByText('2.3 / 6')).toBeInTheDocument()
  })
})
