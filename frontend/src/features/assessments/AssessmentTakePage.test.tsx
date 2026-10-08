import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { StudentAssessmentDetail } from '../../lib/api/types'
import { AssessmentTakePage } from './AssessmentTakePage'

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

vi.mock('./hooks', () => ({
  useAssessmentForStudent: () => ({ data: QUIZ, isLoading: false, error: null }),
  useMySubmissions: () => ({ data: [], isLoading: false, error: null }),
  useSubmitAssessment: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}))

function renderTake() {
  return render(
    <MemoryRouter initialEntries={['/assessments/a1/take']}>
      <Routes>
        <Route path="/assessments/:assessmentId/take" element={<AssessmentTakePage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Take page (restyle)', () => {
  it('shows the question position once, beside a progress bar', () => {
    renderTake()
    expect(screen.getAllByText(/Question 1 of 2/)).toHaveLength(1)
    const bar = screen.getByRole('progressbar', { name: 'Quiz progress' })
    expect(bar).toHaveAttribute('aria-valuenow', '1')
    expect(bar).toHaveAttribute('aria-valuemax', '2')
  })

  it('marks the selected option with the accent style and the word "Selected"', async () => {
    renderTake()
    expect(screen.queryByText('Selected')).not.toBeInTheDocument()

    const radio = screen.getByRole('radio', { name: /A\. O\(n log n\)/ })
    await userEvent.click(radio)

    expect(radio).toBeChecked()
    const option = radio.closest('label')!
    expect(option.className).toMatch(/optionSelected/)
    expect(within(option).getByText('Selected')).toBeInTheDocument()
    // Only the chosen option carries it.
    expect(screen.getAllByText('Selected')).toHaveLength(1)
  })

  it('keeps the review-and-submit step and shows the quiz footer', async () => {
    renderTake()
    expect(screen.getByText('Your teacher reviews every grade before you see it.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getAllByText(/Question 2 of 2/)).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Review & Submit' }))
    expect(screen.getByRole('heading', { name: 'Review Your Answers' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit Assessment' })).toBeInTheDocument()
  })
})
