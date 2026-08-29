import { Link } from 'react-router-dom'
import { useAuth } from '../store/AuthContext'

/** Rendered for unknown routes hit by an authenticated user (see App.tsx). */
export function NotFoundPage() {
  const { user } = useAuth()
  const homePath = user?.role === 'teacher' ? '/courses' : '/assessments'

  return (
    <div className="placeholder-page">
      <div className="placeholder-icon">🧭</div>
      <p className="placeholder-label">This page doesn't exist.</p>
      <Link to={homePath}>Back to home</Link>
    </div>
  )
}
