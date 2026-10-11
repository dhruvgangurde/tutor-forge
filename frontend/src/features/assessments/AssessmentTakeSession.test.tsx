import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import api from '../../lib/api/client'
import type { StudentAssessmentDetail } from '../../lib/api/types'
import { AuthProvider } from '../../store/AuthContext'
import { AssessmentTakePage } from './AssessmentTakePage'

// The quiz page end to end through the real API client and its 401 handling,
// with a fake HTTP adapter in place of the backend: nothing is sent anywhere.

const QUIZ: StudentAssessmentDetail = {
  id: 'a1',
  title: 'Sorting Quiz',
  status: 'published',
  course_id: 'c1',
  question_count: 2,
  published_at: '2026-10-07T00:00:00Z',
  questions: [
    {
      id: 'q1',
      question_type: 'mcq',
      stem: 'What is the worst-case time of Merge Sort?',
      options: ['O(n log n)', 'O(n^2)', 'O(n)', 'O(log n)'],
      max_points: 1,
      rubric_criteria: [],
    },
    {
      id: 'q2',
      question_type: 'short_answer',
      stem: 'Why is Merge Sort stable?',
      options: null,
      max_points: 2,
      rubric_criteria: [],
    },
  ],
}

const STUDENT = { id: 'u1', email: 'student@demo.com', role: 'student' as const }

type Reply = { status: number; data: unknown }
type Route_ = (config: InternalAxiosRequestConfig) => Reply

const original = api.defaults.adapter
let calls: { key: string; auth: string | undefined }[] = []

function install(routes: Record<string, Route_>) {
  calls = []
  const adapter: AxiosAdapter = async (config) => {
    const key = `${(config.method ?? 'get').toUpperCase()} ${config.url}`
    calls.push({ key, auth: config.headers?.Authorization as string | undefined })
    const handler = routes[key]
    const { status, data } = handler ? handler(config) : { status: 404, data: { detail: 'Not found.' } }
    const response = { data, status, statusText: '', headers: {}, config }
    if (status >= 400) {
      throw new AxiosError(`Request failed with status code ${status}`, String(status), config, null, response)
    }
    return response
  }
  api.defaults.adapter = adapter
}

function count(key: string) {
  return calls.filter((c) => c.key === key).length
}

const quizRoutes: Record<string, Route_> = {
  'GET /assessments/a1/take': () => ({ status: 200, data: QUIZ }),
  'GET /assessments/my-submissions': () => ({ status: 200, data: [] }),
}

const expired: Route_ = () => ({ status: 401, data: { detail: 'Could not validate credentials.' } })

function renderQuiz() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={['/assessments/a1/take']}>
          <Routes>
            <Route path="/assessments/:assessmentId/take" element={<AssessmentTakePage />} />
            <Route path="/assessments/submissions/:submissionId" element={<p>Submission page</p>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}

/** Answer both questions, go to review, confirm the submit. */
async function answerAndSubmit() {
  await userEvent.click(await screen.findByRole('radio', { name: /A\. O\(n log n\)/ }))
  await userEvent.click(screen.getByRole('button', { name: 'Next' }))
  await userEvent.type(screen.getByRole('textbox'), 'It keeps equal keys in order')
  await userEvent.click(screen.getByRole('button', { name: 'Review & Submit' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit Assessment' }))
  await userEvent.click(screen.getByRole('button', { name: 'Submit' }))
}

beforeEach(() => {
  localStorage.setItem('tf_access_token', 'expired-tok')
  localStorage.setItem('tf_user', JSON.stringify(STUDENT))
})

afterEach(() => {
  api.defaults.adapter = original
  localStorage.clear()
})

describe('Quiz submit after the sign-in has expired', () => {
  it('keeps the answers and the page instead of sending the student to /login', async () => {
    install({ ...quizRoutes, 'POST /assessments/a1/submit': expired })
    renderQuiz()
    await answerAndSubmit()

    expect(await screen.findByRole('dialog', { name: 'Sign in again to submit' })).toBeInTheDocument()
    // The session was not cleared (the redirect-to-login path clears it first) ...
    expect(localStorage.getItem('tf_access_token')).toBe('expired-tok')
    // ... and the answers are still on the review page behind the dialog.
    expect(screen.getByText(/Choice: A/)).toBeInTheDocument()
    expect(screen.getByText(/It keeps equal keys in order/)).toBeInTheDocument()
  })
})

/** Submit is refused once for the expired sign-in, then answered by `then`. */
function submitExpiredThen(then: Reply): Route_ {
  let n = 0
  return () => (n++ === 0 ? { status: 401, data: { detail: 'Could not validate credentials.' } } : then)
}

const signInRoutes = (meId: string): Record<string, Route_> => ({
  'POST /auth/login': (config) => {
    const body = JSON.parse(config.data as string)
    expect(body.email).toBe(STUDENT.email)
    return { status: 200, data: { access_token: 'new-tok', token_type: 'bearer', role: 'student' } }
  },
  'GET /auth/me': (config) => {
    expect(config.headers.Authorization).toBe('Bearer new-tok')
    return {
      status: 200,
      data: { id: meId, email: meId === STUDENT.id ? STUDENT.email : 'other@demo.com', role: 'student' },
    }
  },
  'POST /auth/logout': () => ({ status: 200, data: { status: 'logged_out' } }),
})

async function signIn(password = 'correct-horse') {
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Sign in and submit' }))
}

describe('Signing in again from the quiz', () => {
  it('shows the approved wording with the email of the student who started the quiz', async () => {
    install({ ...quizRoutes, 'POST /assessments/a1/submit': expired })
    renderQuiz()
    await answerAndSubmit()

    const dialog = await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    expect(dialog).toHaveAccessibleDescription(
      'Your sign-in has expired. Your answers are still here. Sign in as student@demo.com to submit.'
    )
    expect(screen.getByLabelText('Email')).toHaveValue('student@demo.com')
    expect(screen.getByLabelText('Email')).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('retries the submit once after the same student signs in, then shows the submission', async () => {
    install({
      ...quizRoutes,
      ...signInRoutes('u1'),
      'POST /assessments/a1/submit': submitExpiredThen({
        status: 200,
        data: { submission_id: 's1', status: 'pending_grading', message: 'Submitted.' },
      }),
    })
    renderQuiz()
    await answerAndSubmit()
    await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    await signIn()

    expect(await screen.findByText('Submission page')).toBeInTheDocument()
    expect(count('POST /assessments/a1/submit')).toBe(2)
    const retry = calls.filter((c) => c.key === 'POST /assessments/a1/submit')[1]
    expect(retry.auth).toBe('Bearer new-tok')
    expect(localStorage.getItem('tf_access_token')).toBe('new-tok')
    // The existing re-login behaviour revokes the old, expired token.
    expect(calls.some((c) => c.key === 'POST /auth/logout' && c.auth === 'Bearer expired-tok')).toBe(true)
  })

  it('refuses a different account: nothing is submitted and the answers are kept', async () => {
    install({ ...quizRoutes, ...signInRoutes('u2'), 'POST /assessments/a1/submit': expired })
    renderQuiz()
    await answerAndSubmit()
    await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    await signIn()

    expect(
      await screen.findByText(
        "That's a different account. Sign in as student@demo.com to submit this quiz. Your answers are kept."
      )
    ).toBeInTheDocument()
    expect(count('POST /assessments/a1/submit')).toBe(1)
    // The other account's new session is ended again and never stored.
    expect(calls.some((c) => c.key === 'POST /auth/logout' && c.auth === 'Bearer new-tok')).toBe(true)
    expect(localStorage.getItem('tf_access_token')).toBe('expired-tok')
    expect(JSON.parse(localStorage.getItem('tf_user')!).id).toBe('u1')
    expect(screen.getByText(/It keeps equal keys in order/)).toBeInTheDocument()
  })

  it('shows a wrong password plainly and submits nothing', async () => {
    install({
      ...quizRoutes,
      'POST /assessments/a1/submit': expired,
      'POST /auth/login': () => ({ status: 401, data: { detail: 'Invalid email or password.' } }),
    })
    renderQuiz()
    await answerAndSubmit()
    await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    await signIn('wrong-password')

    expect(await screen.findByText('Invalid email or password.')).toBeInTheDocument()
    expect(count('POST /assessments/a1/submit')).toBe(1)
    expect(screen.getByLabelText('Password')).toHaveValue('') // cleared, never shown
    expect(localStorage.getItem('tf_access_token')).toBe('expired-tok')
  })

  it('does not loop when the retried submit is refused again', async () => {
    install({ ...quizRoutes, ...signInRoutes('u1'), 'POST /assessments/a1/submit': expired })
    renderQuiz()
    await answerAndSubmit()
    await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    await signIn()

    expect(await screen.findByRole('alert')).toBeInTheDocument() // the submit error, on the page
    expect(screen.queryByRole('dialog', { name: 'Sign in again to submit' })).not.toBeInTheDocument()
    expect(count('POST /assessments/a1/submit')).toBe(2)
    expect(screen.getByText(/It keeps equal keys in order/)).toBeInTheDocument()
  })

  it.each([
    ['Cancel', async () => userEvent.click(screen.getByRole('button', { name: 'Cancel' }))],
    ['Escape', async () => userEvent.keyboard('{Escape}')],
  ])('%s closes the dialog, keeps the answers and returns focus to Submit', async (_name, close) => {
    install({ ...quizRoutes, 'POST /assessments/a1/submit': expired })
    renderQuiz()
    await answerAndSubmit()
    await screen.findByRole('dialog', { name: 'Sign in again to submit' })
    await close()

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(/It keeps equal keys in order/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit Assessment' })).toHaveFocus()
    expect(localStorage.getItem('tf_access_token')).toBe('expired-tok')
    expect(count('POST /assessments/a1/submit')).toBe(1)
  })

  it('focuses the labelled password field, keeps Tab inside, and never logs the password', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m))
    install({
      ...quizRoutes,
      'POST /assessments/a1/submit': expired,
      'POST /auth/login': () => ({ status: 401, data: { detail: 'Invalid email or password.' } }),
    })
    renderQuiz()
    await answerAndSubmit()
    const dialog = await screen.findByRole('dialog', { name: 'Sign in again to submit' })

    const password = screen.getByLabelText('Password')
    expect(password).toHaveFocus()
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveAttribute('autocomplete', 'current-password')

    await userEvent.type(password, 'secret-pw-123')
    for (let i = 0; i < 6; i++) {
      await userEvent.tab()
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    }
    await userEvent.tab({ shift: true })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)

    await userEvent.click(screen.getByRole('button', { name: 'Sign in and submit' }))
    await screen.findByText('Invalid email or password.')
    expect(document.body.textContent).not.toContain('secret-pw-123')
    for (const spy of spies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain('secret-pw-123')
      spy.mockRestore()
    }
  })
})

describe('Every other expired request still goes to /login', () => {
  it('clears the session for a request that did not opt out', async () => {
    install({ 'GET /courses': expired })
    await expect(api.get('/courses')).rejects.toBeTruthy()
    expect(localStorage.getItem('tf_access_token')).toBeNull()
    expect(localStorage.getItem('tf_user')).toBeNull()
  })

  it('keeps the session for the one request that opts out', async () => {
    install({ 'POST /assessments/a1/submit': expired })
    await expect(
      api.post('/assessments/a1/submit', { responses: [] }, { skipSessionRedirect: true })
    ).rejects.toBeTruthy()
    expect(localStorage.getItem('tf_access_token')).toBe('expired-tok')
  })
})
