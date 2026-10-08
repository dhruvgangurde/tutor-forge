import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AssessmentDraft, QuestionDetail } from '../../lib/api/types'
import { AssessmentPreview } from './AssessmentPreview'

// The preview reads the draft through react-query hooks; the answer-key display
// is what is under test here, so the hooks return a fixed draft.
let draft: AssessmentDraft
vi.mock('./hooks', () => ({
  useAssessmentDraft: () => ({ data: draft, isLoading: false, isError: false, error: null }),
  usePublishAssessment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateDraftQuestion: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => vi.fn() }))

function mcq(overrides: Partial<QuestionDetail> = {}): QuestionDetail {
  return {
    id: 'q-mcq',
    question_type: 'mcq',
    stem: 'Which statement about Quick Sort is true?',
    options: [
      'It requires extra space for sorting',
      'It is not an in-place sorting algorithm',
      'It is slower than Merge Sort in practice',
      'It has excellent cache performance',
    ],
    bloom_level: 'remember',
    difficulty: 'easy',
    max_points: 1,
    rubric_criteria: [],
    correct_answer: 'D',
    worked_solution: null,
    ...overrides,
  }
}

function makeDraft(questions: QuestionDetail[]): AssessmentDraft {
  return {
    id: 'a1',
    title: 'Sorting Quiz',
    status: 'draft',
    course_id: 'c1',
    question_count: questions.length,
    questions,
    generation_error: null,
    published_at: null,
    created_at: '2026-10-07T00:00:00Z',
  }
}

function optionRow(text: string): HTMLElement {
  // Option rows only -- the enclosing question <li> also contains the text.
  const rows = [...document.querySelectorAll<HTMLElement>('.question-options > li')].filter(
    (li) => (li.textContent ?? '').includes(text)
  )
  expect(rows).toHaveLength(1)
  return rows[0]
}

describe('AssessmentPreview answer key', () => {
  beforeEach(() => {
    draft = makeDraft([mcq()])
  })

  it('marks the option the grader treats as correct, and only that one', () => {
    render(<AssessmentPreview assessmentId="a1" courseId="c1" />)
    const keyed = optionRow('D. It has excellent cache performance')
    expect(within(keyed).getByText('✓ Marked correct')).toBeInTheDocument()
    expect(keyed).toHaveClass('question-option-correct')
    expect(screen.getAllByText('✓ Marked correct')).toHaveLength(1)
    expect(optionRow('C. It is slower than Merge Sort')).not.toHaveClass('question-option-correct')
  })

  it('resolves the key the way the grader does (trim + uppercase)', () => {
    draft = makeDraft([mcq({ correct_answer: ' c ' })])
    render(<AssessmentPreview assessmentId="a1" courseId="c1" />)
    expect(within(optionRow('C. It is slower')).getByText('✓ Marked correct')).toBeInTheDocument()
  })

  it('flags an MCQ whose stored key matches no option instead of marking nothing silently', () => {
    draft = makeDraft([mcq({ correct_answer: 'E' })])
    render(<AssessmentPreview assessmentId="a1" courseId="c1" />)
    expect(screen.queryByText('✓ Marked correct')).not.toBeInTheDocument()
    expect(screen.getByText(/No valid answer key \(stored as “E”\)/)).toBeInTheDocument()
  })

  it('flags an MCQ with no stored key at all', () => {
    draft = makeDraft([mcq({ correct_answer: null })])
    render(<AssessmentPreview assessmentId="a1" courseId="c1" />)
    expect(screen.getByText(/No valid answer key — students cannot score/)).toBeInTheDocument()
  })

  it('shows the expected answer for a numeric question', () => {
    draft = makeDraft([
      mcq(),
      {
        ...mcq({ id: 'q-num', question_type: 'numeric', options: null, correct_answer: '7' }),
        stem: 'How many times is merge called for 8 elements?',
      },
    ])
    render(<AssessmentPreview assessmentId="a1" courseId="c1" />)
    const line = screen.getByText('Answer key:').closest('p')
    expect(line).toHaveTextContent('Answer key: 7')
  })
})
