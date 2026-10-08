import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../store/AuthContext'
import { LoginPage } from './LoginPage'

const post = vi.fn()
const get = vi.fn()
vi.mock('../../lib/api/client', () => ({
  default: { post: (...args: unknown[]) => post(...args), get: (...args: unknown[]) => get(...args) },
}))

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

async function signIn(password: string) {
  await userEvent.type(screen.getByLabelText('Email'), 'teacher@demo.com')
  await userEvent.type(screen.getByLabelText('Password'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

const wrongPassword = Object.assign(new Error('Request failed with status code 401'), {
  isAxiosError: true,
  response: { status: 401, data: { detail: 'Invalid email or password.' } },
})

beforeEach(() => {
  post.mockReset()
  get.mockReset()
  localStorage.clear()
})

describe('Login with a wrong password', () => {
  it('shows the server message in place', async () => {
    post.mockRejectedValue(wrongPassword)
    renderLogin()
    await signIn('wrong-password')

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(screen.getByLabelText('Email')).toHaveValue('teacher@demo.com')
  })

  it('does not end the session that is already signed in', async () => {
    localStorage.setItem('tf_access_token', 'existing-tok')
    post.mockRejectedValue(wrongPassword)
    renderLogin()
    await signIn('wrong-password')

    await screen.findByRole('alert')
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('/auth/login', { email: 'teacher@demo.com', password: 'wrong-password' })
    expect(localStorage.getItem('tf_access_token')).toBe('existing-tok')
  })
})

describe('Login over an existing session', () => {
  it('revokes the replaced token once the new login succeeds', async () => {
    localStorage.setItem('tf_access_token', 'existing-tok')
    post.mockImplementation(async (url: string) =>
      url === '/auth/login'
        ? { data: { access_token: 'new-tok', role: 'teacher' } }
        : { data: { status: 'logged_out' } }
    )
    get.mockResolvedValue({ data: { id: 't1' } })
    renderLogin()
    await signIn('password123')

    await vi.waitFor(() => expect(localStorage.getItem('tf_access_token')).toBe('new-tok'))
    // /auth/me is asked about the NEW session, and the old one is revoked.
    expect(get).toHaveBeenCalledWith('/auth/me', { headers: { Authorization: 'Bearer new-tok' } })
    expect(post).toHaveBeenCalledWith('/auth/logout', {}, { headers: { Authorization: 'Bearer existing-tok' } })
  })
})
