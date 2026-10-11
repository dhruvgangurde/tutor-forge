import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../../hooks/useToast'
import { listSessions } from '../../lib/api/tutor'
import type { SessionSummary } from '../../lib/api/types'
import { queryClient as appQueryClient } from '../../lib/queryClient'
import { SessionsPage } from './SessionsPage'

vi.mock('../../lib/api/tutor', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api/tutor')>()
  return { ...actual, listSessions: vi.fn() }
})

const mockList = vi.mocked(listSessions)

const SESSION: SessionSummary = {
  id: 's1',
  course_id: 'c1',
  course_name: 'Physics',
  title: 'what is potential difference?',
  message_count: 4,
  last_activity_at: '2026-10-08T21:40:00Z',
  current_hint_level: 0,
  created_at: '2026-10-08T21:33:00Z',
}

function serverError() {
  return Object.assign(new Error('Request failed with status code 500'), {
    isAxiosError: true,
    response: { status: 500, data: { detail: 'Internal Server Error' } },
  })
}

/** The app's own query defaults (retry policy and retry delay), not test-only ones. */
function renderPage() {
  const client = new QueryClient({ defaultOptions: appQueryClient.getDefaultOptions() })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <SessionsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Tutor sessions list', () => {
  beforeEach(() => {
    mockList.mockReset()
  })

  it('shows a loading state while the list is on its way', () => {
    mockList.mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByRole('status')).toHaveTextContent('Loading sessions…')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the empty state when there are no sessions', async () => {
    mockList.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No tutoring sessions yet. Start your first session!')).toBeInTheDocument()
  })

  it('lists sessions by title and course', async () => {
    mockList.mockResolvedValue([SESSION])
    renderPage()
    expect(await screen.findByRole('button', { name: /what is potential difference\?/ })).toBeInTheDocument()
    expect(screen.getByText('Physics')).toBeInTheDocument()
  })

  it('keeps showing the loading state, not a blank page, while a failed request is retried', async () => {
    mockList.mockRejectedValue(serverError())
    renderPage()
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(1))
    // First attempt has failed; the retry is pending.
    expect(screen.getByRole('status')).toHaveTextContent('Loading sessions…')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows a friendly error with Try again after a 500, and Try again reloads the list', async () => {
    mockList.mockRejectedValue(serverError())
    renderPage()

    const alert = await screen.findByRole('alert', {}, { timeout: 4000 })
    expect(alert).toHaveTextContent('Something went wrong on our side. Please try again in a moment.')
    expect(alert).not.toHaveTextContent('500')
    expect(alert).not.toHaveTextContent('Internal Server Error')
    expect(mockList).toHaveBeenCalledTimes(2) // one retry, per the app's retry policy

    mockList.mockResolvedValue([SESSION])
    const retry = screen.getByRole('button', { name: 'Try again' })
    retry.focus()
    await userEvent.keyboard('{Enter}')

    expect(await screen.findByRole('button', { name: /what is potential difference\?/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(3)
  })
})
