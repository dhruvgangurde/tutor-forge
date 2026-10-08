import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { SessionSummary, TutoringMessageOut } from '../../lib/api/types'
import { ChatThread } from './ChatThread'
import { HintButton, HINT_NEEDS_QUESTION } from './HintButton'
import { TutorPage } from './TutorPage'

function msg(over: Partial<TutoringMessageOut>): TutoringMessageOut {
  return {
    id: Math.random().toString(36).slice(2),
    role: 'tutor',
    content: 'text',
    citations: [],
    hint_level: 0,
    is_refusal: false,
    created_at: '2026-10-07T10:00:00Z',
    ...over,
  }
}

describe('ChatThread (restyle)', () => {
  it('labels tutor turns with TUTOR and leaves student turns unlabelled', () => {
    const { container } = render(
      <ChatThread messages={[msg({ role: 'student', content: 'Why?' }), msg({ content: 'What do you think?' })]} />
    )
    const labels = container.querySelectorAll('.tutor-label')
    expect(labels).toHaveLength(1)
    expect(labels[0]).toHaveTextContent('Tutor')
    expect(labels[0].querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('shows a quiet Thinking line in the tutor slot while a reply is pending', () => {
    const { rerender } = render(<ChatThread messages={[msg({ role: 'student', content: 'Why?' })]} />)
    expect(screen.queryByText('Thinking')).not.toBeInTheDocument()
    rerender(<ChatThread messages={[msg({ role: 'student', content: 'Why?' })]} isReplying />)
    const thinking = screen.getByText('Thinking')
    expect(thinking.closest('.chat-bubble-row')).toHaveClass('tutor')
  })

  it('gives citation chips a document icon without changing their text', () => {
    const { container } = render(
      <ChatThread
        messages={[
          msg({
            citations: [{ chunk_text: 'x', source_file: 'daa_lab_guide.pdf', page_or_slide: 12, confidence: 0.8 }],
          }),
        ]}
      />
    )
    const chip = container.querySelector('.citation-badge')!
    expect(chip.querySelector('svg')).not.toBeNull()
    expect(chip.textContent).toBe('Daa Lab Guide, page 12')
  })
})

describe('HintButton (restyle)', () => {
  it('shows the existing reason beside the disabled button before the first question', () => {
    render(<HintButton currentHintLevel={0} hasQuestion={false} onRequestHint={vi.fn()} />)
    const button = screen.getByRole('button', { name: /Request hint level 1/ })
    expect(button).toBeDisabled()
    expect(screen.getByText(HINT_NEEDS_QUESTION)).toBeVisible()
    expect(button).toHaveAccessibleDescription(HINT_NEEDS_QUESTION)
  })

  it('shows the hint level once a question exists (no dots)', () => {
    render(<HintButton currentHintLevel={2} hasQuestion onRequestHint={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Request hint level 3/ })).toBeEnabled()
    expect(screen.getByText('Hint level:')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
  })
})

const SESSION: SessionSummary = {
  id: 's1',
  course_id: 'c1',
  course_name: 'DAA Lab Guide',
  title: 'How does the partition step work?',
  message_count: 2,
  last_activity_at: '2026-10-07T10:00:00Z',
  current_hint_level: 0,
  created_at: '2026-10-07T09:00:00Z',
}

let page = {
  messages: [] as TutoringMessageOut[],
  sessions: [SESSION] as SessionSummary[],
  sendError: null as unknown,
}

vi.mock('./hooks', () => ({
  useMessages: () => ({ data: page.messages, isLoading: false, isError: false, error: null }),
  useSendChat: () => ({ mutateAsync: vi.fn(), isPending: false, isError: Boolean(page.sendError), error: page.sendError }),
  useRequestHint: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null, reset: vi.fn() }),
  useSessions: () => ({ data: page.sessions }),
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tutor/s1']}>
      <Routes>
        <Route path="/tutor/:sessionId" element={<TutorPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('TutorPage header and composer (restyle)', () => {
  it('shows the course name, the session title and the tutor note', () => {
    page = { messages: [msg({ role: 'student', content: 'Q' })], sessions: [SESSION], sendError: null }
    renderPage()
    expect(screen.getByText('DAA Lab Guide')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'How does the partition step work?' })).toBeInTheDocument()
    expect(
      screen.getByText('The tutor asks questions rather than giving answers, and only uses your course material.')
    ).toBeInTheDocument()
  })

  it('falls back to the generic title when the session is not in the list', () => {
    page = { messages: [], sessions: [], sendError: null }
    renderPage()
    expect(screen.getByRole('heading', { level: 1, name: 'Tutoring Session' })).toBeInTheDocument()
    // Empty chat: the note sits under the prompt, not twice.
    expect(screen.getAllByText(/The tutor asks questions rather than giving answers/)).toHaveLength(1)
  })

  it('puts Hint and Send on one row in the composer', () => {
    page = { messages: [msg({ role: 'student', content: 'Q' })], sessions: [SESSION], sendError: null }
    const { container } = renderPage()
    const row = container.querySelector('.composer-actions') as HTMLElement
    expect(within(row).getByRole('button', { name: /Request hint level 1/ })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Send/ })).toBeInTheDocument()
  })

  it('shows a failed send as an inline error card with no separate Try again', () => {
    page = {
      messages: [msg({ role: 'student', content: 'Q' })],
      sessions: [SESSION],
      sendError: Object.assign(new Error('Request failed with status code 503'), {
        isAxiosError: true,
        response: { status: 503, data: { detail: 'Service Unavailable' } },
      }),
    }
    renderPage()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveClass('error-banner')
    expect(alert).toHaveTextContent('Something went wrong on our side. Please try again in a moment.')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })
})
