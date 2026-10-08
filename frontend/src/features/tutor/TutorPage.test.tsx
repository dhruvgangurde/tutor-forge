import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TutoringMessageOut } from '../../lib/api/types'
import { TutorPage } from './TutorPage'
import { HINT_NEEDS_QUESTION } from './HintButton'

// A small stand-in for react-query's mutation state, so the page can be
// exercised exactly as it uses useRequestHint (mutateAsync / isError / error / reset).
const hint = {
  mutateAsync: vi.fn(),
  isPending: false,
  isError: false,
  error: null as unknown,
  reset: vi.fn(),
}
let messages: TutoringMessageOut[] = []

vi.mock('./hooks', () => ({
  useMessages: () => ({ data: messages, isLoading: false, isError: false, error: null }),
  useSendChat: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useRequestHint: () => hint,
  useSessions: () => ({ data: [] }),
}))

function studentQuestion(): TutoringMessageOut {
  return {
    id: 'm1',
    role: 'student',
    content: 'How does partition work?',
    citations: [],
    hint_level: 0,
    is_refusal: false,
    created_at: '2026-10-07T16:21:24Z',
  }
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tutor/s1']}>
      <Routes>
        <Route path="/tutor/:sessionId" element={<TutorPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Tutor hint button', () => {
  beforeEach(() => {
    messages = []
    Object.assign(hint, { isPending: false, isError: false, error: null })
    hint.mutateAsync.mockReset()
    hint.reset.mockReset()
  })

  it('is disabled before the first question, with a tooltip saying why', () => {
    renderPage()
    const button = screen.getByRole('button', { name: /Request hint level 1/ })
    expect(button).toBeDisabled()
    // The tooltip is on the wrapper: hover events do not fire on a disabled button.
    expect(button.parentElement).toHaveAttribute('title', HINT_NEEDS_QUESTION)
    // ...and the reason is exposed to screen readers too.
    expect(button).toHaveAccessibleDescription(HINT_NEEDS_QUESTION)
  })

  it('is enabled once the student has asked something', () => {
    messages = [studentQuestion()]
    renderPage()
    const button = screen.getByRole('button', { name: /Request hint level 1/ })
    expect(button).toBeEnabled()
    expect(button.parentElement).not.toHaveAttribute('title')
  })

  it('catches a failed hint request instead of leaving an unhandled rejection', async () => {
    messages = [studentQuestion()]
    hint.mutateAsync.mockRejectedValue(new Error('Request failed with status code 400'))
    const { rerender } = renderPage()

    // If the page did not catch this, Vitest would fail the run on the
    // unhandled rejection -- which is what the audit saw in the console.
    await userEvent.click(screen.getByRole('button', { name: /Request hint level 1/ }))
    expect(hint.mutateAsync).toHaveBeenCalledTimes(1)

    // react-query then reports the failure through isError / error.
    Object.assign(hint, { isError: true, error: new Error('Request failed with status code 400') })
    rerender(
      <MemoryRouter initialEntries={['/tutor/s1']}>
        <Routes>
          <Route path="/tutor/:sessionId" element={<TutorPage />} />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('shows the server explanation when a hint fails', () => {
    messages = [studentQuestion()]
    Object.assign(hint, {
      isError: true,
      error: Object.assign(new Error('Request failed with status code 400'), {
        isAxiosError: true,
        response: { status: 400, data: { detail: 'Please ask a question before requesting a hint.' } },
      }),
    })
    renderPage()
    expect(screen.getByRole('alert')).toHaveTextContent('Please ask a question before requesting a hint.')
  })
})
