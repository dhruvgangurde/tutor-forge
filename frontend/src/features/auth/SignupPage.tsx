import { useState, FormEvent } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'
import api from '../../lib/api/client'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { MAX_EMAIL_CHARS } from '../../lib/limits'
import { getErrorMessage } from '../../lib/api/errors'

export function SignupPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters.')
      return
    }

    setLoading(true)

    try {
      const resp = await api.post('/auth/register', { email, password })
      const { access_token, role } = resp.data

      const meResp = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${access_token}` },
      })
      const { id } = meResp.data

      login(access_token, { id, email, role })
      navigate('/assessments', { replace: true })
    } catch (err) {
      setError(getErrorMessage(err, 'Registration failed. Please try again.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <h1 className="login-title">Create Account</h1>
          <p className="login-subtitle">Sign up to get started</p>
        </div>

        <form id="signup-form" onSubmit={handleSubmit} className="login-form">
          {error && <ErrorBanner message={error} />}

          <div className="field-group">
            <label htmlFor="email-input" className="field-label">Email</label>
            <input
              id="email-input"
              type="email"
              className="field-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              maxLength={MAX_EMAIL_CHARS}
              autoComplete="email"
              placeholder="you@example.com"
            />
          </div>

          <div className="field-group">
            <label htmlFor="password-input" className="field-label">Password</label>
            <input
              id="password-input"
              type="password"
              className="field-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              placeholder="••••••••"
            />
            <p className="text-xs text-gray-500 mt-1">At least 6 characters</p>
          </div>

          <div className="field-group">
            <label htmlFor="confirm-password-input" className="field-label">Confirm Password</label>
            <input
              id="confirm-password-input"
              type="password"
              className="field-input"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
              placeholder="••••••••"
            />
          </div>

          <button
            id="signup-button"
            type="submit"
            className="btn btn-primary btn-full"
            disabled={loading}
          >
            {loading ? 'Creating account…' : 'Sign up'}
          </button>
        </form>

        <p className="text-center text-sm text-gray-600 mt-4">
          Already have an account? <Link to="/login" className="text-blue-600 hover:text-blue-700">Sign in</Link>
        </p>
      </div>
    </div>
  )
}
