import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StudentAssessmentDetail } from '../../lib/api/types'
import { AssessmentTakePage } from './AssessmentTakePage'

// The banner reads the stored token's exp claim for display only. Fake timers
// and a fake token stand in for real time and a real sign-in.

const QUIZ: StudentAssessmentDetail = {
  id: 'a1',
  title: 'Sorting Quiz',
  status: 'published',
  course_id: 'c1',
  question_count: 1,
  published_at: '2026-10-07T00:00:00Z',
  questions: [
    {
      id: 'q1',
      question_type: 'short_answer',
      stem: 'Why is Merge Sort stable?',
      options: null,
      max_points: 2,
      rubric_criteria: [],
    },
  ],
}

vi.mock('./hooks', () => ({
  useAssessmentForStudent: () => ({ data: QUIZ, isLoading: false, error: null }),
  useMySubmissions: () => ({ data: [], isLoading: false, error: null }),
  useSubmitAssessment: () => ({ mutate: vi.fn(), isPending: false, error: null, reset: vi.fn() }),
}))

const NOW = new Date('2026-10-11T10:00:00Z').getTime()

/** A token whose payload says it expires at `expMs` (header and signature are not read). */
function tokenExpiringAt(expMs: number): string {
  const payload = btoa(JSON.stringify({ sub: 'u1', exp: Math.floor(expMs / 1000) }))
    .replace(/=+$/, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
  return `eyJhbGciOiJIUzI1NiJ9.${payload}.signature`
}

function renderTake() {
  return render(
    <MemoryRouter initialEntries={['/assessments/a1/take']}>
      <Routes>
        <Route path="/assessments/:assessmentId/take" element={<AssessmentTakePage />} />
      </Routes>
    </MemoryRouter>
  )
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms)
  })
}

const banner = () => screen.queryByText(/Your sign-in ends in about/)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
})

describe('Session-ending banner on the quiz', () => {
  it('appears about 10 minutes before the sign-in ends, not before', () => {
    localStorage.setItem('tf_access_token', tokenExpiringAt(NOW + 15 * 60_000))
    renderTake()
    expect(banner()).not.toBeInTheDocument()

    advance(4 * 60_000) // 11 minutes left
    expect(banner()).not.toBeInTheDocument()

    advance(60_000) // 10 minutes left
    expect(
      screen.getByText(
        'Your sign-in ends in about 10 minutes. If it ends, you can sign in again here and keep your answers.'
      )
    ).toBeInTheDocument()
  })

  it('counts down and says "minute" for the last one', () => {
    localStorage.setItem('tf_access_token', tokenExpiringAt(NOW + 10 * 60_000))
    renderTake()
    advance(9 * 60_000 + 30_000) // 30 seconds left
    expect(
      screen.getByText(
        'Your sign-in ends in about 1 minute. If it ends, you can sign in again here and keep your answers.'
      )
    ).toBeInTheDocument()
  })

  it('goes away once the sign-in has ended (the submit then asks to sign in again)', () => {
    localStorage.setItem('tf_access_token', tokenExpiringAt(NOW + 2 * 60_000))
    renderTake()
    expect(banner()).toBeInTheDocument()
    advance(3 * 60_000)
    expect(banner()).not.toBeInTheDocument()
  })

  it('shows nothing without a readable token', () => {
    localStorage.setItem('tf_access_token', 'not-a-jwt')
    renderTake()
    advance(60_000)
    expect(banner()).not.toBeInTheDocument()
  })

  it('carries an icon and words, not colour alone, and is announced politely', () => {
    localStorage.setItem('tf_access_token', tokenExpiringAt(NOW + 5 * 60_000))
    renderTake()
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent(/Your sign-in ends in about 5 minutes/)
    expect(status.querySelector('svg')).not.toBeNull()
  })
})
