import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { TutorPage } from './TutorPage'

let messagesState: { data?: unknown; isLoading: boolean; isError: boolean; error: unknown } = {
  isLoading: true,
  isError: false,
  error: null,
}

vi.mock('./hooks', () => ({
  useMessages: () => messagesState,
  useSendChat: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useRequestHint: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null, reset: vi.fn() }),
  useSessions: () => ({ data: [] }),
}))

function apiError(status: number, data: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data },
  })
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tutor/00000000-0000-0000-0000-000000000000']}>
      <Routes>
        <Route path="/tutor/:sessionId" element={<TutorPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Tutor page: missing session', () => {
  it('shows a neutral loading state, not an empty chat, while loading', () => {
    messagesState = { isLoading: true, isError: false, error: null }
    renderPage()
    expect(screen.getByText('Loading session…')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/Ask a question/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Tutoring Session' })).not.toBeInTheDocument()
  })

  it('shows a not-found page with a way back once the session is known to be missing', () => {
    messagesState = {
      isLoading: false,
      isError: true,
      error: apiError(404, { detail: 'Tutoring session not found.' }),
    }
    renderPage()
    expect(screen.getByRole('heading', { name: 'Tutoring session not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to sessions' })).toHaveAttribute('href', '/tutor')
    expect(screen.queryByPlaceholderText(/Ask a question/)).not.toBeInTheDocument()
  })

  it('treats a session owned by another student the same way', () => {
    messagesState = {
      isLoading: false,
      isError: true,
      error: apiError(403, { detail: 'You do not own this tutoring session.' }),
    }
    renderPage()
    expect(screen.getByRole('heading', { name: 'Tutoring session not found' })).toBeInTheDocument()
  })
})
