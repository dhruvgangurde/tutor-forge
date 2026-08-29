import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'

// Control what useAuth returns per test.
let mockAuth: { isAuthenticated: boolean; user: { role: string } | null }
vi.mock('../../store/AuthContext', () => ({
  useAuth: () => mockAuth,
}))

function renderAt(path: string, allowedRole?: 'teacher' | 'student') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ProtectedRoute allowedRole={allowedRole} />}>
          <Route path="/secret" element={<div>Secret Content</div>} />
        </Route>
        <Route path="/login" element={<div>Login Page</div>} />
        <Route path="/not-authorized" element={<div>Not Authorized</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    mockAuth = { isAuthenticated: false, user: null }
  })

  it('redirects unauthenticated users to /login', () => {
    mockAuth = { isAuthenticated: false, user: null }
    renderAt('/secret', 'teacher')
    expect(screen.getByText('Login Page')).toBeInTheDocument()
  })

  it('redirects a wrong-role user to /not-authorized, not /login (F32)', () => {
    mockAuth = { isAuthenticated: true, user: { role: 'student' } }
    renderAt('/secret', 'teacher')
    expect(screen.getByText('Not Authorized')).toBeInTheDocument()
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument()
  })

  it('renders the protected content for the matching role', () => {
    mockAuth = { isAuthenticated: true, user: { role: 'teacher' } }
    renderAt('/secret', 'teacher')
    expect(screen.getByText('Secret Content')).toBeInTheDocument()
  })

  it('renders content for any authenticated user when no role is required', () => {
    mockAuth = { isAuthenticated: true, user: { role: 'student' } }
    renderAt('/secret')
    expect(screen.getByText('Secret Content')).toBeInTheDocument()
  })
})
