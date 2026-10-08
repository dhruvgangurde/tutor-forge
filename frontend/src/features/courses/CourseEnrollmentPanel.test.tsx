import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CourseEnrollmentPanel } from './CourseEnrollmentPanel'

// Stand-ins for the react-query mutations, exercised exactly as the panel uses them.
const enroll = {
  mutateAsync: vi.fn(),
  isPending: false,
  isError: false,
  error: null as unknown,
  reset: vi.fn(),
}
const remove = { mutateAsync: vi.fn(), isPending: false }

vi.mock('./hooks', () => ({
  useEnrollments: () => ({
    data: [{ student_id: 'st1', email: 'kim@demo.com', enrolled_at: '2026-10-07T10:00:00Z' }],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useEnrollStudent: () => enroll,
  useRemoveEnrollment: () => remove,
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => async () => true }))

function alreadyEnrolled() {
  Object.assign(enroll, {
    isError: true,
    error: Object.assign(new Error('Request failed with status code 409'), {
      isAxiosError: true,
      response: { status: 409, data: { detail: 'That student is already enrolled.' } },
    }),
  })
  // reset() clears the error, as react-query's does.
  enroll.reset.mockImplementation(() => Object.assign(enroll, { isError: false, error: null }))
}

describe('Students panel: stale add error', () => {
  beforeEach(() => {
    enroll.reset.mockReset()
    remove.mutateAsync.mockReset()
    alreadyEnrolled()
  })

  it('clears the error once a removal succeeds', async () => {
    remove.mutateAsync.mockResolvedValue({ message: 'kim@demo.com removed.' })
    const { rerender } = render(<CourseEnrollmentPanel courseId="c1" />)
    expect(screen.getByRole('alert')).toHaveTextContent('That student is already enrolled.')

    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(enroll.reset).toHaveBeenCalled()
    rerender(<CourseEnrollmentPanel courseId="c1" />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the error when the removal fails', async () => {
    remove.mutateAsync.mockRejectedValue(new Error('boom'))
    render(<CourseEnrollmentPanel courseId="c1" />)
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(enroll.reset).not.toHaveBeenCalled()
  })

  it('clears the error as soon as the email is edited', async () => {
    const { rerender } = render(<CourseEnrollmentPanel courseId="c1" />)
    await userEvent.type(screen.getByLabelText('Add a student by email'), 'x')
    expect(enroll.reset).toHaveBeenCalledTimes(1)
    rerender(<CourseEnrollmentPanel courseId="c1" />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
