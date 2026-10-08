import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { GradingQueue } from './grading/GradingQueue'
import { CourseProgressList } from './progress/CourseProgressList'
import { CoursePickerModal } from './tutor/CoursePickerModal'

vi.mock('../lib/api/courses', () => ({
  getAvailableCourses: vi.fn().mockResolvedValue([
    { id: 'c1', name: 'DAA Lab Guide', status: 'ready', created_at: '2026-10-07T00:00:00Z' },
  ]),
}))

const QUEUE = [
  {
    recommendation_id: 'r1',
    submission_id: 'sub1',
    student_email: 'student@demo.com',
    assessment_title: 'Sorting Quiz',
    recommended_score: 3.5,
    max_score: 6,
    status: 'pending_review',
    submitted_at: '2026-10-07T00:00:00Z',
  },
]

const PROGRESS = [
  {
    course_id: 'c1',
    course_name: 'DAA Lab Guide',
    assessments_graded: 1,
    assessments_awaiting_grade: 0,
    earned_points: 4.5,
    possible_points: 6,
    last_graded_at: '2026-10-07T00:00:00Z',
  },
]

describe('clickable rows are keyboard reachable', () => {
  it('grading queue: each row has a real button that selects it', async () => {
    const user = userEvent.setup({ delay: null })
    const onSelect = vi.fn()
    render(<GradingQueue items={QUEUE} selectedId={null} onSelect={onSelect} />)
    await user.tab()
    const button = screen.getByRole('button', { name: 'student@demo.com' })
    expect(button).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('sub1')
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('progress: each course row has a real button, marked current when selected', async () => {
    const user = userEvent.setup({ delay: null })
    const onSelect = vi.fn()
    const { rerender } = render(<CourseProgressList courses={PROGRESS} selectedId={null} onSelect={onSelect} />)
    await user.tab()
    await user.keyboard(' ')
    expect(onSelect).toHaveBeenCalledWith('c1')
    rerender(<CourseProgressList courses={PROGRESS} selectedId="c1" onSelect={onSelect} />)
    expect(screen.getByRole('button', { name: 'DAA Lab Guide' })).toHaveAttribute('aria-current', 'true')
  })
})

describe('stacked tables keep explicit table semantics', () => {
  it('exposes table, rowgroup, row, columnheader and cell roles', () => {
    render(<GradingQueue items={QUEUE} selectedId={null} onSelect={vi.fn()} />)
    const table = screen.getByRole('table')
    expect(table).toHaveAttribute('role', 'table')
    expect(within(table).getAllByRole('rowgroup')).toHaveLength(2)
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Student', 'Assessment', 'Recommended score', 'Status', 'Submitted',
    ])
    const rows = within(table).getAllByRole('row')
    rows.forEach((r) => expect(r).toHaveAttribute('role', 'row'))
    within(rows[1]).getAllByRole('cell').forEach((c) => expect(c).toHaveAttribute('role', 'cell'))
  })

  // Every table that stacks on phones (Phase 5), checked at the source.
  const STACKED = [
    'assessments/AssessmentList.tsx',
    'courses/CourseEnrollmentPanel.tsx',
    'grading/GradingQueue.tsx',
    'grading/FinalizedGrades.tsx',
    'progress/CourseProgressList.tsx',
    'progress/CourseProgressPanel.tsx',
  ]
  it.each(STACKED)('%s: every table element carries its explicit role', (file) => {
    const src = readFileSync(resolve(__dirname, file), 'utf8')
    const table = src.slice(src.indexOf('<table'), src.indexOf('</table>'))
    expect(table).toMatch(/<table[^>]*data-table-stack[^>]*role="table"/)
    const count = (re: RegExp) => (table.match(re) ?? []).length
    expect(count(/<thead\b/g)).toBe(count(/<thead role="rowgroup"/g))
    expect(count(/<tbody\b/g)).toBe(count(/<tbody role="rowgroup"/g))
    expect(count(/<tr\b/g)).toBe(count(/<tr\s+role="row"/g))
    expect(count(/<th\b/g)).toBe(count(/<th role="columnheader"/g))
    expect(count(/<td\b/g)).toBe(count(/<td role="cell"/g))
    expect(count(/<td\b/g)).toBeGreaterThan(0)
  })
})

describe('course picker', () => {
  function Harness({ onCancel }: { onCancel: () => void }) {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          New tutoring session
        </button>
        {open && (
          <CoursePickerModal
            onSelectCourse={vi.fn()}
            onCancel={() => {
              onCancel()
              setOpen(false)
            }}
          />
        )}
      </>
    )
  }

  it('closes on Escape and gives focus back to the trigger', async () => {
    const user = userEvent.setup({ delay: null })
    const onCancel = vi.fn()
    render(<Harness onCancel={onCancel} />)
    const trigger = screen.getByRole('button', { name: 'New tutoring session' })
    await user.click(trigger)
    await screen.findByRole('button', { name: /DAA Lab Guide/ })
    expect(screen.getByRole('dialog', { name: 'Start a tutoring session' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})
