import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../store/AuthContext'
import { LoginPage } from './LoginPage'

const post = vi.fn()
vi.mock('../../lib/api/client', () => ({
  default: { post: (...args: unknown[]) => post(...args), get: vi.fn() },
}))

describe('Login with a wrong password', () => {
  it('shows the server message in place', async () => {
    post.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 401'), {
        isAxiosError: true,
        response: { status: 401, data: { detail: 'Invalid email or password.' } },
      })
    )
    render(
      <MemoryRouter initialEntries={['/login']}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>
    )
    await userEvent.type(screen.getByLabelText('Email'), 'teacher@demo.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    expect(screen.getByLabelText('Email')).toHaveValue('teacher@demo.com')
  })
})
