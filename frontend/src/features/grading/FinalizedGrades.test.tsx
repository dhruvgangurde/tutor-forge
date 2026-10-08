import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FinalizedGradeItem } from '../../lib/api/types'
import { FinalizedGrades } from './FinalizedGrades'

let state: { data?: FinalizedGradeItem[]; isLoading: boolean; isError: boolean; error: unknown } = {
  isLoading: false,
  isError: false,
  error: null,
}

vi.mock('./hooks', () => ({ useFinalizedGrades: () => state }))

const ROW: FinalizedGradeItem = {
  submission_id: 's1',
  student_email: 'kim@demo.com',
  assessment_title: 'Sorting Quiz',
  course_name: 'DAA Lab Guide',
  final_score: 4.5,
  max_score: 6,
  action: 'overridden',
  released: true,
  finalized_at: '2026-10-08T09:00:00Z',
}

describe('Finalized grades list', () => {
  it('lists released grades read-only', () => {
    state = { data: [ROW], isLoading: false, isError: false, error: null }
    render(<FinalizedGrades />)
    expect(screen.getByRole('heading', { name: 'Finalized (1)' })).toBeInTheDocument()
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('kim@demo.com')).toBeInTheDocument()
    expect(within(row).getByText('Sorting Quiz')).toBeInTheDocument()
    expect(within(row).getByText('4.5 / 6')).toBeInTheDocument()
    expect(within(row).getByText('Overridden')).toBeInTheDocument()
    // Nothing to act on: no buttons or links in the history.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('says so when nothing has been finalized', () => {
    state = { data: [], isLoading: false, isError: false, error: null }
    render(<FinalizedGrades />)
    expect(screen.getByText('No grades have been finalized yet.')).toBeInTheDocument()
  })
})
