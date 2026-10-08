import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CourseUploadForm } from './CourseUploadForm'

vi.mock('./hooks', () => ({
  useUploadCourse: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

describe('Course upload file picker', () => {
  it('offers only the types the backend can parse (no legacy .ppt)', () => {
    render(<CourseUploadForm onDone={vi.fn()} />)
    const picker = screen.getByLabelText(/Course materials/)
    expect(picker.getAttribute('accept')?.split(',')).toEqual(['.pdf', '.pptx', '.txt'])
    expect(screen.getByText('Course materials (PDF, PPTX, TXT)')).toBeInTheDocument()
  })
})
