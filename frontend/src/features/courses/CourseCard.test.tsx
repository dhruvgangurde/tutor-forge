import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { CourseSummary } from '../../lib/api/types'
import { CourseCard } from './CourseCard'

vi.mock('./hooks', () => ({
  useCourseDeletionImpact: () => ({ data: undefined, isLoading: false }),
  useDeleteCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRestoreCourse: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => async () => true }))

const BASE: CourseSummary = {
  id: 'c1',
  name: 'Sorting',
  status: 'ready',
  created_at: '2026-10-07T10:00:00Z',
  archived_at: null,
  is_archived: false,
  failure_reason: null,
}

function renderCard(course: Partial<CourseSummary>) {
  return render(
    <MemoryRouter>
      <CourseCard course={{ ...BASE, ...course }} />
    </MemoryRouter>
  )
}

const REASON = 'The course outline could not be worked out from these materials. Try uploading the course again.'

describe('CourseCard: failed courses (audit #11)', () => {
  it('shows why the course failed', () => {
    renderCard({ status: 'failed', failure_reason: REASON })
    expect(screen.getByText(REASON)).toBeInTheDocument()
  })

  it('offers Delete only: no Archive, Restore or Retry', () => {
    renderCard({ status: 'failed', failure_reason: REASON })
    expect(screen.getByRole('button', { name: 'Delete…' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Restore' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
  })

  it('does not offer Restore on an archived failed course', () => {
    renderCard({ status: 'failed', failure_reason: REASON, is_archived: true, archived_at: '2026-10-07T11:00:00Z' })
    expect(screen.queryByRole('button', { name: 'Restore' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete…' })).toBeInTheDocument()
  })

  it('falls back to a general reason when none was recorded', () => {
    renderCard({ status: 'failed', failure_reason: null })
    expect(screen.getByText(/Processing the course materials failed/)).toBeInTheDocument()
  })

  it('leaves healthy courses as they were', () => {
    renderCard({})
    expect(screen.getByRole('button', { name: 'Archive' })).toBeInTheDocument()
    expect(screen.queryByText(/failed/i)).not.toBeInTheDocument()
    renderCard({ is_archived: true, archived_at: '2026-10-07T11:00:00Z' })
    expect(screen.getByRole('button', { name: 'Restore' })).toBeInTheDocument()
  })
})
