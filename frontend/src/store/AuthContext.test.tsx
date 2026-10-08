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
