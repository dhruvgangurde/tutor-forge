import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Badge } from './Badge'
import { ConfirmDialog } from './ConfirmDialog'
import { EmptyState } from './EmptyState'
import { ErrorBanner } from './ErrorBanner'
import { NotFoundState } from './NotFoundState'
import { SkeletonCards, SkeletonRows } from './Skeleton'
import { TabPanel, Tabs } from './Tabs'

describe('Badge (status chip)', () => {
  it('always pairs an icon with the word', () => {
    for (const variant of ['success', 'warning', 'danger', 'info', 'muted'] as const) {
      const { container, unmount } = render(<Badge variant={variant}>Published</Badge>)
      const chip = container.querySelector(`.badge.badge-${variant}`)!
      expect(chip.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
      expect(chip).toHaveTextContent('Published')
      unmount()
    }
  })
})

describe('ErrorBanner (inline error card)', () => {
  it('shows the message as an alert, with no retry unless one is possible', () => {
    render(<ErrorBanner message="Could not load your courses." />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveClass('error-banner')
    expect(alert).toHaveTextContent('Could not load your courses.')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers "Try again" only when the caller can retry', async () => {
    const onRetry = vi.fn()
    render(<ErrorBanner message="Could not load." onRetry={onRetry} />)
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

describe('EmptyState', () => {
  it('renders a quiet title, one line and an optional action', () => {
    render(
      <EmptyState
        icon="📚"
        title="No students enrolled"
        label="Add a student by email to give them access."
        action={<button>Add student</button>}
      />
    )
    expect(screen.getByRole('heading', { name: 'No students enrolled' })).toBeInTheDocument()
    expect(screen.getByText('Add a student by email to give them access.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add student' })).toBeInTheDocument()
    // The legacy icon prop is accepted but no longer drawn.
    expect(screen.queryByText('📚')).not.toBeInTheDocument()
  })
})

describe('Skeletons', () => {
  it('announce the load once and hide the placeholder blocks', () => {
    const { container } = render(<SkeletonCards label="Loading assessments" count={2} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading assessments')
    expect(container.querySelectorAll('.skeleton-card')).toHaveLength(2)
    container.querySelectorAll('.skeleton-card').forEach((card) => expect(card).toHaveAttribute('aria-hidden', 'true'))
  })

  it('shape rows like a table', () => {
    const { container } = render(<SkeletonRows label="Loading students" rows={3} columns={2} />)
    expect(container.querySelectorAll('.skeleton-row')).toHaveLength(3)
    expect(container.querySelectorAll('.skeleton-row')[0].querySelectorAll('.skeleton')).toHaveLength(2)
  })
})

describe('Tabs', () => {
  function Harness() {
    const [active, setActive] = useState('outline')
    const tabs = [
      { id: 'outline', label: 'Outline' },
      { id: 'assessments', label: 'Assessments' },
      { id: 'students', label: 'Students' },
    ]
    return (
      <>
        <Tabs label="Course sections" tabs={tabs} active={active} onChange={setActive} idPrefix="c" />
        {tabs.map((t) => (
          <TabPanel key={t.id} idPrefix="c" id={t.id} active={active}>
            {t.label} panel
          </TabPanel>
        ))}
      </>
    )
  }

  it('exposes a labelled tablist with one selected tab and its panel', () => {
    render(<Harness />)
    const list = screen.getByRole('tablist', { name: 'Course sections' })
    const tabs = within(list).getAllByRole('tab')
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false'])
    expect(screen.getByRole('tabpanel', { name: 'Outline' })).toHaveTextContent('Outline panel')
  })

  it('switches on click and with the arrow keys', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('tab', { name: 'Students' }))
    expect(screen.getByRole('tabpanel', { name: 'Students' })).toBeInTheDocument()

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Students' }), { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Outline' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Outline' })).toHaveFocus()

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Outline' }), { key: 'End' })
    expect(screen.getByRole('tab', { name: 'Students' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('NotFoundState (404 layout)', () => {
  it('has a heading, one explanation and one primary way back', () => {
    render(
      <MemoryRouter>
        <NotFoundState title="Course not found" message="This course doesn't exist." linkTo="/courses" linkLabel="Back to courses" />
      </MemoryRouter>
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Course not found' })).toBeInTheDocument()
    const back = screen.getByRole('link', { name: 'Back to courses' })
    expect(back).toHaveAttribute('href', '/courses')
    expect(back).toHaveClass('btn', 'btn-primary')
  })
})

describe('ConfirmDialog focus', () => {
  function Harness() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          Publish assessment
        </button>
        <ConfirmDialog
          open={open}
          message="Publish this assessment?"
          confirmLabel="Publish"
          onConfirm={() => setOpen(false)}
          onCancel={() => setOpen(false)}
        />
      </>
    )
  }

  it('starts on Cancel, keeps Tab inside the dialog, and returns focus on close', async () => {
    const user = userEvent.setup({ delay: null })
    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Publish assessment' })
    await user.click(trigger)

    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const confirm = screen.getByRole('button', { name: 'Publish' })
    expect(cancel).toHaveFocus()

    await user.tab()
    expect(confirm).toHaveFocus()
    await user.tab()
    expect(cancel).toHaveFocus()
    await user.tab({ shift: true })
    expect(confirm).toHaveFocus()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
