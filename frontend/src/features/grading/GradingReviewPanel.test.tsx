import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { GradingDetail } from '../../lib/api/types'
import { GradingReviewPanel } from './GradingReviewPanel'

const DETAIL: GradingDetail = {
  recommendation_id: 'r1',
  submission_id: 'sub1',
  student_email: 'kim@demo.com',
  assessment_title: 'Sorting Quiz',
  recommended_score: 4.5,
  max_score: 6,
  status: 'pending_review',
  questions: [],
  evidence_citations: [],
  created_at: '2026-10-07T16:23:20Z',
}

const approve = { mutateAsync: vi.fn(), isPending: false }
const override = { mutateAsync: vi.fn(), isPending: false }

vi.mock('./hooks', () => ({
  useGradingReview: () => ({ data: DETAIL, isLoading: false, isError: false, error: null }),
  useApproveGrade: () => approve,
  useOverrideGrade: () => override,
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => async () => true }))

function renderPanel() {
  return render(<GradingReviewPanel submissionId="sub1" onFinalized={vi.fn()} />)
}

describe('Grading approve/override labels', () => {
  beforeEach(() => {
    approve.mutateAsync.mockReset().mockResolvedValue({})
    override.mutateAsync.mockReset().mockResolvedValue({})
  })

  it('names the recommended score on the approve button', () => {
    renderPanel()
    expect(screen.getByRole('button', { name: 'Approve recommended score (4.5 / 6)' })).toBeInTheDocument()
  })

  it('approving sends no score: the label is the only thing that changed', async () => {
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /Approve recommended score/ }))
    expect(approve.mutateAsync).toHaveBeenCalledWith({ submissionId: 'sub1', note: undefined })
  })

  it('names the typed override on the button that submits it', async () => {
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Override score' }))
    expect(screen.getByRole('button', { name: 'Override and finalize' })).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/Final score/), '3')
    expect(screen.getByRole('button', { name: 'Override and finalize (3 / 6)' })).toBeInTheDocument()
    // The approve button still names what approve sends.
    expect(screen.getByRole('button', { name: 'Approve recommended score (4.5 / 6)' })).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/Reason for overriding/), 'Partial credit for Q2.')
    await userEvent.click(screen.getByRole('button', { name: 'Override and finalize (3 / 6)' }))
    expect(override.mutateAsync).toHaveBeenCalledWith({
      submissionId: 'sub1',
      finalScore: 3,
      reason: 'Partial credit for Q2.',
    })
  })

  it('does not name an out-of-range override', async () => {
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Override score' }))
    await userEvent.type(screen.getByLabelText(/Final score/), '9')
    expect(screen.getByRole('button', { name: 'Override and finalize' })).toBeInTheDocument()
  })
})
