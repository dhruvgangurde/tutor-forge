import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CourseUploadForm } from './CourseUploadForm'

const { mutateAsync } = vi.hoisted(() => ({ mutateAsync: vi.fn() }))

vi.mock('./hooks', () => ({
  useUploadCourse: () => ({ mutateAsync, isPending: false, isError: false, error: null }),
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const MB = 1024 * 1024

/** A file that reports `megabytes` without holding them in memory. */
function fileOf(name: string, megabytes: number) {
  const file = new File(['x'], name, { type: 'application/pdf' })
  Object.defineProperty(file, 'size', { value: megabytes * MB })
  return file
}

/**
 * Submits the form. jsdom reports a required file input as empty even after
 * user.upload sets its files, so a click or Enter is stopped by its form
 * validation before the handler runs; a real browser submits.
 */
function submit() {
  fireEvent.submit(screen.getByRole('button', { name: 'Upload course' }).closest('form')!)
}

beforeEach(() => mutateAsync.mockReset())

describe('Course upload file picker', () => {
  it('offers only the types the backend can parse (no legacy .ppt)', () => {
    render(<CourseUploadForm onDone={vi.fn()} />)
    const picker = screen.getByLabelText(/Course materials/)
    expect(picker.getAttribute('accept')?.split(',')).toEqual(['.pdf', '.pptx', '.txt'])
    expect(screen.getByText('Course materials (PDF, PPTX, TXT)')).toBeInTheDocument()
  })
})

describe('Course upload total size', () => {
  const tooMuch = 'These files add up to more than 100 MB. Upload fewer files at a time.'

  it('refuses files over 100 MB together before uploading, and says why', async () => {
    const user = userEvent.setup()
    render(<CourseUploadForm onDone={vi.fn()} />)
    await user.type(screen.getByLabelText('Course name'), 'Big course')
    await user.upload(screen.getByLabelText(/Course materials/), [fileOf('a.pdf', 60), fileOf('b.pdf', 60)])
    submit()

    expect(screen.getByRole('alert')).toHaveTextContent(tooMuch)
    expect(mutateAsync).not.toHaveBeenCalled()
  })

  it('uploads files that fit within the total', async () => {
    const user = userEvent.setup()
    render(<CourseUploadForm onDone={vi.fn()} />)
    await user.type(screen.getByLabelText('Course name'), 'Small course')
    const files = [fileOf('a.pdf', 40), fileOf('b.pdf', 40)]
    await user.upload(screen.getByLabelText(/Course materials/), files)
    submit()

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mutateAsync).toHaveBeenCalledWith({ name: 'Small course', files })
  })

  it('reaches the name, the files and the upload button by keyboard, in that order', async () => {
    const user = userEvent.setup()
    render(<CourseUploadForm onDone={vi.fn()} />)
    await user.tab()
    expect(screen.getByLabelText('Course name')).toHaveFocus()
    await user.tab()
    expect(screen.getByLabelText(/Course materials/)).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Upload course' })).toHaveFocus()
  })

  it('refuses a single file over the total too', async () => {
    const user = userEvent.setup()
    render(<CourseUploadForm onDone={vi.fn()} />)
    await user.type(screen.getByLabelText('Course name'), 'Big course')
    await user.upload(screen.getByLabelText(/Course materials/), [fileOf('a.pdf', 101)])
    submit()

    expect(screen.getByRole('alert')).toHaveTextContent(tooMuch)
    expect(mutateAsync).not.toHaveBeenCalled()
  })
})
