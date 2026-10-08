import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

const post = vi.fn()
vi.mock('../lib/api/client', () => ({ default: { post: (...args: unknown[]) => post(...args) } }))

function Probe() {
  const { isAuthenticated, logout } = useAuth()
  return (
    <>
      <span data-testid="state">{isAuthenticated ? 'in' : 'out'}</span>
      <button onClick={logout}>log out</button>
    </>
  )
}

describe('AuthContext logout', () => {
  beforeEach(() => {
    post.mockReset()
    localStorage.setItem('tf_access_token', 'tok-123')
    localStorage.setItem('tf_user', JSON.stringify({ id: 'u1', email: 'a@b.c', role: 'student' }))
  })

  it('revokes the token on the server with that token, then clears it locally', () => {
    post.mockResolvedValue({ data: { status: 'logged_out' } })
    render(<AuthProvider><Probe /></AuthProvider>)
    expect(screen.getByTestId('state')).toHaveTextContent('in')

    act(() => screen.getByText('log out').click())

    expect(post).toHaveBeenCalledWith(
      '/auth/logout',
      {},
      { headers: { Authorization: 'Bearer tok-123' } }
    )
    expect(localStorage.getItem('tf_access_token')).toBeNull()
    expect(screen.getByTestId('state')).toHaveTextContent('out')
  })

  it('still logs out locally when the server call fails', async () => {
    post.mockRejectedValue(new Error('network down'))
    render(<AuthProvider><Probe /></AuthProvider>)
    await act(async () => screen.getByText('log out').click())
    expect(localStorage.getItem('tf_access_token')).toBeNull()
    expect(screen.getByTestId('state')).toHaveTextContent('out')
  })
})

function LoginProbe({ token }: { token: string }) {
  const { login } = useAuth()
  return <button onClick={() => login(token, { id: 'u2', email: 't@demo.com', role: 'teacher' })}>sign in</button>
}

describe('AuthContext login over an existing session', () => {
  beforeEach(() => {
    post.mockReset()
    post.mockResolvedValue({ data: { status: 'logged_out' } })
    localStorage.clear()
  })

  it('revokes the token it replaces, using that old token', () => {
    localStorage.setItem('tf_access_token', 'old-tok')
    render(<AuthProvider><LoginProbe token="new-tok" /></AuthProvider>)
    act(() => screen.getByText('sign in').click())

    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/auth/logout', {}, { headers: { Authorization: 'Bearer old-tok' } })
    expect(localStorage.getItem('tf_access_token')).toBe('new-tok')
  })

  it('keeps the new session even if revoking the old one fails', async () => {
    post.mockRejectedValue(new Error('network down'))
    localStorage.setItem('tf_access_token', 'old-tok')
    render(<AuthProvider><LoginProbe token="new-tok" /></AuthProvider>)
    await act(async () => screen.getByText('sign in').click())
    expect(localStorage.getItem('tf_access_token')).toBe('new-tok')
  })

  it('revokes nothing on a first login or when the token is unchanged', () => {
    const { unmount } = render(<AuthProvider><LoginProbe token="new-tok" /></AuthProvider>)
    act(() => screen.getByText('sign in').click())
    unmount()
    render(<AuthProvider><LoginProbe token="new-tok" /></AuthProvider>)
    act(() => screen.getByText('sign in').click())
    expect(post).not.toHaveBeenCalled()
  })
})
