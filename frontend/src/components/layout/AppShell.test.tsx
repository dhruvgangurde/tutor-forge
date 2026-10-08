import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

const logout = vi.fn()
let role: 'teacher' | 'student' = 'teacher'
vi.mock('../../store/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', email: `${role}@demo.com`, role }, logout }),
}))

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>
}

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="*" element={<Where />} />
        </Route>
        <Route path="/login" element={<p>login page</p>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('AppShell variants', () => {
  it('gives teachers a sidebar with their sections, account email and Log out', async () => {
    role = 'teacher'
    logout.mockReset()
    renderShell('/grading')
    const sidebar = screen.getByRole('complementary')
    const nav = within(sidebar).getByRole('navigation', { name: 'Main' })
    expect(within(nav).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Courses', '/courses'],
      ['Grading', '/grading'],
    ])
    expect(within(nav).getByRole('link', { name: 'Grading' })).toHaveClass('active')
    expect(within(sidebar).getByText('teacher@demo.com')).toBeInTheDocument()

    await userEvent.click(within(sidebar).getByRole('button', { name: 'Log out' }))
    expect(logout).toHaveBeenCalledTimes(1)
    expect(screen.getByText('login page')).toBeInTheDocument()
  })

  it('gives students a top bar with the same routes and Log out', async () => {
    role = 'student'
    logout.mockReset()
    renderShell('/tutor/abc')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    const header = screen.getByRole('banner')
    const nav = within(header).getByRole('navigation', { name: 'Main' })
    expect(within(nav).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Assessments', '/assessments'],
      ['Tutor', '/tutor'],
      ['Progress', '/progress'],
    ])
    expect(within(nav).getByRole('link', { name: 'Tutor' })).toHaveClass('active')
    expect(screen.getByTestId('where')).toHaveTextContent('/tutor/abc')

    await userEvent.click(within(header).getByRole('button', { name: 'Log out' }))
    expect(logout).toHaveBeenCalledTimes(1)
    expect(screen.getByText('login page')).toBeInTheDocument()
  })
})
