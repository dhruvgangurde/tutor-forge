import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  PublishedAssessmentSummary,
  StudentAssessmentDetail,
  StudentSubmissionDetail,
  StudentSubmissionSummary,
} from '../../lib/api/types'
import { MySubmissionsPage } from './MySubmissionsPage'
import { SubmissionDetailPage } from './SubmissionDetailPage'
import { AssessmentsPage } from './AssessmentsPage'
import { AssessmentTakePage } from './AssessmentTakePage'
import { MAX_ANSWER_TEXT_CHARS } from '../../lib/limits'

// Student pages read through react-query hooks; each test sets what they return.
const state: {
  published: PublishedAssessmentSummary[]
  mine: StudentSubmissionSummary[]
  detail: StudentSubmissionDetail | null
  take: StudentAssessmentDetail | null
  submitError: unknown
} = { published: [], mine: [], detail: null, take: null, submitError: null }

vi.mock('./hooks', () => ({
  usePublishedAssessments: () => ({ data: state.published, isLoading: false, error: null }),
  useMySubmissions: () => ({ data: state.mine, isLoading: false, error: null }),
  useSubmissionDetail: () => ({ data: state.detail, isLoading: false, error: null }),
  useAssessmentForStudent: () => ({ data: state.take, isLoading: false, error: null }),
  useSubmitAssessment: () => ({ mutate: vi.fn(), isPending: false, error: state.submitError }),
}))

/** Shows where navigation went, so link targets can be asserted. */
function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

function renderAt(path: string, routePath: string, element: JSX.Element) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={routePath} element={element} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>
  )
}

const PENDING: StudentSubmissionSummary = {
  submission_id: 's-pending',
  assessment_id: 'a-sorting',
  assessment_title: 'Sorting Quiz',
  course_id: 'c1',
  course_name: 'DAA Lab Guide',
  submitted_at: '2026-10-07T16:23:20Z',
  // AI grading finished, teacher has not reviewed: the misleading case.
  status: 'graded',
  final_score: null,
  max_score: 6,
}

const RELEASED: StudentSubmissionSummary = {
  ...PENDING,
  submission_id: 's-released',
  assessment_id: 'a-ac',
  assessment_title: 'Single-Phase AC Circuits',
  course_name: 'Basic Electrical Engineering',
  final_score: 1,
  max_score: 16,
}

beforeEach(() => {
  state.published = []
  state.mine = []
  state.detail = null
  state.take = null
  state.submitError = null
})

// ── 1. Misleading grade status ────────────────────────────────────────────────

describe('My Submissions', () => {
  it('shows "Awaiting teacher review" and no score for an unreviewed submission', () => {
    state.mine = [PENDING]
    renderAt('/assessments/submissions', '/assessments/submissions', <MySubmissionsPage />)
    expect(screen.getByText('Awaiting teacher review')).toBeInTheDocument()
    expect(screen.queryByText(/Graded/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\/ 6/)).not.toBeInTheDocument()
  })

  it('shows the released score as its own element, separated from the status', () => {
    state.mine = [RELEASED]
    renderAt('/assessments/submissions', '/assessments/submissions', <MySubmissionsPage />)
    expect(screen.getByText('Grade released')).toBeInTheDocument()
    expect(screen.getByText('1 / 16')).toBeInTheDocument()
    // The old markup ran the two together as "Graded1 / 16".
    expect(document.body.textContent).not.toMatch(/Graded1/)
  })
})

describe('Submission detail', () => {
  function detail(overrides: Partial<StudentSubmissionDetail>): StudentSubmissionDetail {
    return {
      submission_id: 's1',
      assessment_id: 'a1',
      assessment_title: 'Sorting Quiz',
      course_id: 'c1',
      course_name: 'DAA Lab Guide',
      submitted_at: '2026-10-07T16:23:20Z',
      status: 'graded',
      responses: [],
      final_score: null,
      max_score: 6,
      ...overrides,
    }
  }

  it('says the score is pending instead of showing "/ 6 · 0%"', () => {
    state.detail = detail({})
    renderAt('/assessments/submissions/s1', '/assessments/submissions/:submissionId', <SubmissionDetailPage />)
    expect(screen.getByText('Awaiting teacher review')).toBeInTheDocument()
    expect(screen.getByText(/score will appear here once your teacher has reviewed it/)).toBeInTheDocument()
    expect(screen.queryByText(/0%/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\/ 6/)).not.toBeInTheDocument()
  })

  it('shows score and percentage once the teacher has finalized', () => {
    state.detail = detail({ final_score: 4.5 })
    renderAt('/assessments/submissions/s1', '/assessments/submissions/:submissionId', <SubmissionDetailPage />)
    expect(screen.getByText('Grade released')).toBeInTheDocument()
    expect(screen.getByText('4.5 / 6 (75%)')).toBeInTheDocument()
  })
})

// ── 2. Submitted assessments look re-takeable ─────────────────────────────────

describe('Assessments list', () => {
  const SORTING: PublishedAssessmentSummary = {
    id: 'a-sorting',
    title: 'Sorting Quiz',
    course_id: 'c1',
    course_name: 'DAA Lab Guide',
    question_count: 3,
    total_points: 6,
    published_at: '2026-10-07T00:00:00Z',
    created_at: '2026-10-07T00:00:00Z',
  }
  const NEW: PublishedAssessmentSummary = { ...SORTING, id: 'a-new', title: 'Graphs Quiz' }

  it('marks a submitted assessment and links to the submission, not a new attempt', async () => {
    state.published = [SORTING, NEW]
    state.mine = [PENDING]
    renderAt('/assessments', '/assessments', <AssessmentsPage />)

    const card = screen.getByText('Sorting Quiz').closest('div[class*="assessmentCard"]') as HTMLElement
    expect(within(card).getByText('Submitted')).toBeInTheDocument()
    expect(within(card).getByText('Awaiting teacher review')).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /start/i })).not.toBeInTheDocument()

    await userEvent.click(within(card).getByRole('button', { name: 'View submission' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/assessments/submissions/s-pending')
  })

  it('still offers a fresh attempt for an assessment not yet submitted', async () => {
    state.published = [NEW]
    state.mine = [PENDING]
    renderAt('/assessments', '/assessments', <AssessmentsPage />)
    expect(screen.queryByText('Submitted')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Start assessment' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/assessments/a-new/take')
  })

  it('shows the released score on a submitted card', () => {
    state.published = [{ ...SORTING, id: 'a-ac', title: 'Single-Phase AC Circuits' }]
    state.mine = [RELEASED]
    renderAt('/assessments', '/assessments', <AssessmentsPage />)
    expect(screen.getByText('Grade released')).toBeInTheDocument()
    expect(screen.getByText('1 / 16')).toBeInTheDocument()
  })
})

describe('Take page', () => {
  const TAKE: StudentAssessmentDetail = {
    id: 'a-sorting',
    title: 'Sorting Quiz',
    status: 'published',
    course_id: 'c1',
    question_count: 1,
    published_at: '2026-10-07T00:00:00Z',
    questions: [
      {
        id: 'q-num',
        question_type: 'numeric',
        stem: 'Final sorted array of [3,27,38,43]?',
        options: null,
        max_points: 1,
        rubric_criteria: [],
      },
    ],
  }

  it('shows a clear notice instead of a fresh attempt when already submitted', async () => {
    state.take = TAKE
    state.mine = [PENDING]
    renderAt('/assessments/a-sorting/take', '/assessments/:assessmentId/take', <AssessmentTakePage />)

    const notice = screen.getByRole('status')
    expect(within(notice).getByText('You have already submitted this assessment')).toBeInTheDocument()
    expect(within(notice).getByText('Awaiting teacher review')).toBeInTheDocument()
    expect(screen.queryByText('Final sorted array of [3,27,38,43]?')).not.toBeInTheDocument()

    await userEvent.click(within(notice).getByRole('button', { name: 'View your submission' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/assessments/submissions/s-pending')
  })

  it('lets a list be typed into a numeric answer (a number input refused commas)', async () => {
    state.take = TAKE
    renderAt('/assessments/a-sorting/take', '/assessments/:assessmentId/take', <AssessmentTakePage />)
    const input = screen.getByLabelText('Numeric answer')
    expect(input).toHaveAttribute('type', 'text')
    await userEvent.type(input, '3, 27, 38, 43')
    expect(input).toHaveValue('3, 27, 38, 43')
  })

  it('caps a typed answer at the server limit', () => {
    state.take = TAKE
    renderAt('/assessments/a-sorting/take', '/assessments/:assessmentId/take', <AssessmentTakePage />)
    expect(screen.getByLabelText('Numeric answer')).toHaveAttribute('maxLength', String(MAX_ANSWER_TEXT_CHARS))
  })

  it('shows a submit failure as a styled alert above the buttons', async () => {
    state.take = TAKE
    state.submitError = new Error('boom')
    renderAt('/assessments/a-sorting/take', '/assessments/:assessmentId/take', <AssessmentTakePage />)
    await userEvent.click(screen.getByRole('button', { name: 'Review & Submit' }))

    const alert = screen.getByRole('alert')
    expect(alert).toHaveClass('error-banner')
    const submit = screen.getByRole('button', { name: 'Submit Assessment' })
    // DOCUMENT_POSITION_FOLLOWING: the button comes after the alert.
    expect(alert.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
