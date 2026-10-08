import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { SessionSummary, TutoringMessageOut } from '../../lib/api/types'
import { ChatInput } from './ChatInput'
import { ChatThread } from './ChatThread'
import { HintButton } from './HintButton'
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

describe('Conversation log', () => {
  it('is a labelled, keyboard-focusable log, empty or not', () => {
    const { rerender } = render(<ChatThread messages={[]} />)
    let log = screen.getByRole('log', { name: 'Conversation' })
    expect(log).toHaveAttribute('tabindex', '0')

    rerender(<ChatThread messages={[msg({ role: 'student', content: 'Why?' }), msg({ content: 'What do you think?' })]} />)
    log = screen.getByRole('log', { name: 'Conversation' })
    expect(log).toHaveAttribute('tabindex', '0')
    expect(log).toHaveTextContent('Why?')
  })

  it('names the sender of every message for screen readers', () => {
    render(<ChatThread messages={[msg({ role: 'student', content: 'Why?' }), msg({ content: 'What do you think?' })]} />)
    const rows = document.querySelectorAll('.chat-bubble-row')
    expect(rows[0]).toHaveTextContent(/^You:\s*Why\?/)
    expect(within(rows[0] as HTMLElement).getByText('You:')).toHaveClass('sr-only')
    expect(rows[1]).toHaveTextContent(/^Tutor/)
  })

  it('keeps the Thinking placeholder out of the log (the status region announces it)', () => {
    render(<ChatThread messages={[msg({ role: 'student', content: 'Why?' })]} isReplying />)
    expect(screen.getByText('Thinking').closest('.chat-bubble-row')).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('Chat field', () => {
  it('has a real label, not just a placeholder', () => {
    render(<ChatInput onSendMessage={vi.fn()} />)
    const field = screen.getByRole('textbox', { name: 'Ask a question about the course material' })
    expect(field).toHaveAttribute('placeholder', 'Ask a question about the course material...')
  })

  it('stays focused while the reply is pending: read-only, not disabled', () => {
    const { rerender } = render(<ChatInput onSendMessage={vi.fn()} />)
    const field = screen.getByRole('textbox', { name: 'Ask a question about the course material' })
    field.focus()
    rerender(<ChatInput onSendMessage={vi.fn()} isLoading />)
    expect(field).not.toBeDisabled()
    expect(field).toHaveAttribute('readonly')
    expect(field).toHaveFocus()
    rerender(<ChatInput onSendMessage={vi.fn()} />)
    expect(field).not.toHaveAttribute('readonly')
    expect(field).toHaveFocus()
  })

  it('keeps the hint button focusable while a hint is pending, without re-requesting', () => {
    const onRequestHint = vi.fn().mockResolvedValue(undefined)
    render(<HintButton currentHintLevel={1} onRequestHint={onRequestHint} isLoading />)
    const button = screen.getByRole('button', { name: /Requesting hint/ })
    expect(button).not.toBeDisabled()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    button.click()
    expect(onRequestHint).not.toHaveBeenCalled()
  })
})

const SESSION: SessionSummary = {
  id: 's1',
  course_id: 'c1',
  course_name: 'DAA Lab Guide',
  title: 'How does the partition step work?',
  message_count: 1,
  last_activity_at: '2026-10-07T10:00:00Z',
  current_hint_level: 0,
  created_at: '2026-10-07T09:00:00Z',
}

const state = { pending: false, error: null as unknown }

vi.mock('./hooks', () => ({
  useMessages: () => ({ data: [msg({ role: 'student', content: 'Q' })], isLoading: false, isError: false, error: null }),
  useSendChat: () => ({ mutateAsync: vi.fn(), isPending: state.pending, isError: Boolean(state.error), error: state.error }),
  useRequestHint: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null, reset: vi.fn() }),
  useSessions: () => ({ data: [SESSION] }),
}))

function page() {
  return (
    <MemoryRouter initialEntries={['/tutor/s1']}>
      <Routes>
        <Route path="/tutor/:sessionId" element={<TutorPage />} />
      </Routes>
    </MemoryRouter>
  )
}

function lifecycleStatus(): HTMLElement {
  // The page's own region; the composer has a separate one for the length limit.
  const region = screen.getAllByRole('status').find((el) => el.getAttribute('aria-live') === 'polite')
  expect(region).toBeDefined()
  return region!
}

describe('Reply lifecycle announcements', () => {
  it('is always present and silent until something happens', () => {
    state.pending = false
    state.error = null
    render(page())
    expect(lifecycleStatus()).toBeEmptyDOMElement()
  })

  it('announces replying, then ready, and never the reply text', () => {
    state.pending = false
    state.error = null
    const { rerender } = render(page())
    state.pending = true
    rerender(page())
    expect(lifecycleStatus()).toHaveTextContent(/^Tutor is replying$/)
    state.pending = false
    rerender(page())
    expect(lifecycleStatus()).toHaveTextContent(/^Reply ready$/)
  })

  it('announces a failure', () => {
    state.pending = true
    state.error = null
    const { rerender } = render(page())
    state.pending = false
    state.error = Object.assign(new Error('Request failed with status code 503'), {
      isAxiosError: true,
      response: { status: 503, data: { detail: 'Service Unavailable' } },
    })
    rerender(page())
    expect(lifecycleStatus()).toHaveTextContent(/^Something went wrong$/)
  })
})
