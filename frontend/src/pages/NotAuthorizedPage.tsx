import { Link } from 'react-router-dom'
import { useAuth } from '../store/AuthContext'

/**
 * Shown when an authenticated user hits a route their role can't access (F32).
 * Previously such users were redirected to /login — confusing, since they are
 * already logged in.
 */
export function NotAuthorizedPage() {
  const { user } = useAuth()
  const homePath = user?.role === 'teacher' ? '/courses' : '/assessments'

  return (
    <div className="placeholder-page">
      <div className="placeholder-icon">🔒</div>
      <p className="placeholder-label">You don't have access to this page.</p>
      <Link to={homePath}>Back to home</Link>
    </div>
  )
}
