import { useState, FormEvent } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'
import api from '../../lib/api/client'
import { ErrorBanner } from '../../components/ui/ErrorBanner'
import { MAX_EMAIL_CHARS } from '../../lib/limits'
import { getErrorMessage } from '../../lib/api/errors'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const resp = await api.post('/auth/login', { email, password })
      const { access_token, role } = resp.data

      // Fetch user identity after receiving token
      const meResp = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${access_token}` },
      })
      const { id } = meResp.data

      login(access_token, { id, email, role })
      navigate(role === 'teacher' ? '/courses' : '/assessments', { replace: true })
    } catch (err) {
      setError(getErrorMessage(err, 'Invalid email or password.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <h1 className="login-title">TutorForge AI</h1>
          <p className="login-subtitle">Sign in to continue</p>
        </div>

        <form id="login-form" onSubmit={handleSubmit} className="login-form">
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
              autoComplete="current-password"
              placeholder="••••••••"
            />
          </div>

          <button
            id="login-button"
            type="submit"
            className="btn btn-primary btn-full"
            disabled={loading}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="login-switch">
          Don't have an account? <Link to="/signup" className="login-switch-link">Sign up</Link>
        </p>

      </div>
    </div>
  )
}
