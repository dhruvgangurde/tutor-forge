import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { QuestionDetail } from '../../lib/api/types'
import { QuestionEditForm } from './QuestionEditForm'

// The form saves through useUpdateDraftQuestion (PATCH /assessments/{id}/questions/{qid}).
// Capturing the mutation call shows exactly what would be persisted.
const mutateAsync = vi.fn()
vi.mock('./hooks', () => ({
  useUpdateDraftQuestion: () => ({ mutateAsync, isPending: false, isError: false, error: null }),
}))
vi.mock('../../hooks/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const OPTIONS = [
  'It requires extra space for sorting',
  'It is not an in-place sorting algorithm',
  'It is slower than Merge Sort in practice',
  'It has excellent cache performance',
]

function question(overrides: Partial<QuestionDetail> = {}): QuestionDetail {
  return {
    id: 'q1',
    question_type: 'mcq',
    stem: 'Which statement about Quick Sort is true?',
    options: OPTIONS,
    bloom_level: 'remember',
    difficulty: 'easy',
    max_points: 1,
    rubric_criteria: [],
    // The live bug: the generated key marked the wrong option (C).
    correct_answer: 'C',
    worked_solution: null,
    ...overrides,
  }
}

function renderForm(q: QuestionDetail, onDone = vi.fn()) {
  render(<QuestionEditForm assessmentId="a1" question={q} onDone={onDone} />)
  return onDone
}

describe('QuestionEditForm answer key', () => {
  beforeEach(() => {
    mutateAsync.mockReset()
    mutateAsync.mockResolvedValue({
      question_id: 'q1',
      assessment_id: 'a1',
      updated_fields: ['correct_answer'],
      concept_tag: 'unchanged',
    })
  })

  it('shows the current key selected among the real option texts', () => {
    renderForm(question())
    const current = screen.getByRole('radio', { name: /C\. It is slower than Merge Sort/ })
    expect(current).toBeChecked()
    // Every option is offered by its text, not as a bare letter.
    for (const [i, text] of OPTIONS.entries()) {
      expect(screen.getByRole('radio', { name: new RegExp(`${'ABCD'[i]}\\. ${text}`) })).toBeInTheDocument()
    }
    const currentLabel = current.closest('label') as HTMLElement
    expect(within(currentLabel).getByText('Currently marked correct')).toBeInTheDocument()
    expect(screen.queryByText(/Leave unchanged/)).not.toBeInTheDocument()
  })

  it('changes the key with one click and persists only the new letter', async () => {
    const user = userEvent.setup()
    const onDone = renderForm(question())

    await user.click(screen.getByRole('radio', { name: /D\. It has excellent cache performance/ }))
    expect(screen.getByRole('radio', { name: /D\. It has excellent cache/ })).toBeChecked()
    expect(screen.getByText('Saving changes the correct answer from C to D.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(mutateAsync).toHaveBeenCalledTimes(1)
    expect(mutateAsync).toHaveBeenCalledWith({ questionId: 'q1', body: { correct_answer: 'D' } })
    expect(onDone).toHaveBeenCalled()
  })

  it('does not resend an unchanged key', async () => {
    const user = userEvent.setup()
    renderForm(question())
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(mutateAsync).not.toHaveBeenCalled()
    expect(screen.getByText('Nothing has changed yet.')).toBeInTheDocument()
  })

  it('sends other edits without touching the key', async () => {
    const user = userEvent.setup()
    renderForm(question())
    const stem = screen.getByLabelText('Question')
    await user.clear(stem)
    await user.type(stem, 'Which is true of Quick Sort?')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(mutateAsync).toHaveBeenCalledWith({
      questionId: 'q1',
      body: { stem: 'Which is true of Quick Sort?' },
    })
  })

  it('treats a key that matches no option as unset and asks the teacher to pick one', async () => {
    const user = userEvent.setup()
    renderForm(question({ correct_answer: 'E' }))
    expect(screen.getByText(/The stored key “E” does not match any option/)).toBeInTheDocument()
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked()

    await user.click(screen.getByRole('radio', { name: /B\. It is not an in-place/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(mutateAsync).toHaveBeenCalledWith({ questionId: 'q1', body: { correct_answer: 'B' } })
  })

  it('shows and corrects the expected answer of a numeric question', async () => {
    const user = userEvent.setup()
    renderForm(
      question({
        question_type: 'numeric',
        options: null,
        stem: 'Final sorted array of [3,27,38,43]?',
        // Also seen live: a generated key listing values not in the input.
        correct_answer: '3,9,10,27,38,43,82',
      })
    )
    const key = screen.getByLabelText('Answer key')
    expect(key).toHaveValue('3,9,10,27,38,43,82')
    await user.clear(key)
    await user.type(key, '3,27,38,43')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(mutateAsync).toHaveBeenCalledWith({
      questionId: 'q1',
      body: { correct_answer: '3,27,38,43' },
    })
  })
})
