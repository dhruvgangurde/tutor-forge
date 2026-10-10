import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, renderHook, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SessionSummary, TutoringMessageOut } from '../../lib/api/types'
import { queryKeys } from '../../lib/queryKeys'
import { TutorPage } from './TutorPage'

// The hint label must follow the backend's ladder level (session.current_hint_level).
// It used to show the highest hint_level anywhere in the history, which never
// reset when the student asked a new question.

function msg(over: Partial<TutoringMessageOut>): TutoringMessageOut {
  return {
    id: Math.random().toString(36).slice(2),
    role: 'tutor',
    content: 'text',
    citations: [],
    hint_level: 0,
    is_refusal: false,
    created_at: '2026-10-08T21:33:19Z',
    ...over,
  }
}

const SESSION: SessionSummary = {
  id: 's1',
  course_id: 'c1',
  course_name: 'Physics',
  title: 'what is potential difference?',
  message_count: 6,
  last_activity_at: '2026-10-08T21:40:00Z',
  current_hint_level: 1,
  created_at: '2026-10-08T21:33:00Z',
}

const page = {
  messages: [] as TutoringMessageOut[],
  sessions: [SESSION] as SessionSummary[] | undefined,
  hintError: null as unknown,
}

vi.mock('./hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./hooks')>()
  return {
    ...actual,
    useMessages: () => ({ data: page.messages, isLoading: false, isError: false, error: null }),
    useSendChat: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
    useRequestHint: () => ({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: Boolean(page.hintError),
      error: page.hintError,
      reset: vi.fn(),
    }),
    useSessions: () => ({ data: page.sessions }),
  }
})

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tutor/s1']}>
      <Routes>
        <Route path="/tutor/:sessionId" element={<TutorPage />} />
      </Routes>
    </MemoryRouter>
  )
}

beforeEach(() => {
  page.messages = []
  page.sessions = [SESSION]
  page.hintError = null
})

describe('Hint level label', () => {
  it("follows the session's level, not the highest level in the history", () => {
    // An earlier question went all the way to the full explanation (level 4);
    // a new question reset the ladder, and one hint has been used since.
    page.messages = [
      msg({ role: 'student', content: 'what is potential difference?' }),
      msg({ hint_level: 4 }),
      msg({ role: 'student', content: 'What is electric current?' }),
      msg({ hint_level: 1 }),
    ]
    renderPage()
    expect(screen.getByRole('button', { name: /Request hint level 2/ })).toBeInTheDocument()
    expect(screen.getByText('Hint level:').nextElementSibling).toHaveTextContent('1')
  })

  it('starts at level 0 before the session list has loaded', () => {
    page.messages = [msg({ role: 'student', content: 'Q' }), msg({ hint_level: 3 })]
    page.sessions = undefined
    renderPage()
    expect(screen.getByRole('button', { name: /Request hint level 1/ })).toBeInTheDocument()
  })

  it('shows the backend message when a hint has no question to build on (400)', () => {
    page.messages = [msg({ role: 'student', content: 'tell me about sourdough bread' })]
    page.hintError = Object.assign(new Error('Request failed with status code 400'), {
      isAxiosError: true,
      response: { status: 400, data: { detail: 'Ask a question about the course before requesting a hint.' } },
    })
    renderPage()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Ask a question about the course before requesting a hint.'
    )
  })
})

vi.mock('../../lib/api/tutor', () => ({
  createSession: vi.fn(),
  getMessages: vi.fn(),
  listSessions: vi.fn(),
  sendChat: vi.fn().mockResolvedValue({}),
  requestHint: vi.fn().mockResolvedValue({}),
}))

describe('Tutor mutations refresh the session level', () => {
  function setup() {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const spy = vi.spyOn(client, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    return { spy, wrapper }
  }

  it.each([
    ['useSendChat', (h: typeof import('./hooks')) => h.useSendChat('s1'), 'Q'],
    ['useRequestHint', (h: typeof import('./hooks')) => h.useRequestHint('s1'), undefined],
  ] as const)('%s invalidates the sessions list exactly, and the messages', async (_name, use, arg) => {
    const actual = await vi.importActual<typeof import('./hooks')>('./hooks')
    const { spy, wrapper } = setup()
    const { result } = renderHook(() => use(actual), { wrapper })
    await (result.current.mutateAsync as (a?: string) => Promise<unknown>)(arg)
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.tutor.sessions(), exact: true })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.tutor.messages('s1') })
  })
})
