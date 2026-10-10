import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CourseProgressDetail } from '../../lib/api/types'
import { CourseProgressPanel } from './CourseProgressPanel'

const DETAIL: CourseProgressDetail = {
  course_id: 'c1',
  course_name: 'DAA Lab Guide',
  earned_points: 4.5,
  possible_points: 6,
  concepts: [],
  results: [],
  tutoring_sessions: 0,
  tutoring_messages: 0,
  untagged_note: null,
}

vi.mock('./hooks', () => ({
  useMyCourseProgress: () => ({ data: DETAIL, isLoading: false, isError: false, error: null }),
}))

describe('Progress panel copy (fix round 1)', () => {
  it('says "teacher", not "instructor"', () => {
    render(<CourseProgressPanel courseId="c1" />)
    expect(
      screen.getByText('Only grades your teacher has reviewed and released are counted here.')
    ).toBeInTheDocument()
    expect(screen.queryByText(/instructor/i)).not.toBeInTheDocument()
  })
})
