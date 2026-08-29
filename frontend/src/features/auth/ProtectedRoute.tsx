import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'

interface ProtectedRouteProps {
  allowedRole?: 'teacher' | 'student'
}

/**
 * Wraps child routes with authentication + optional role guard.
 * - Unauthenticated users → redirect to /login
 * - Wrong role → redirect to /not-authorized (F32: a logged-in user with the
 *   wrong role is not sent back to /login, which read as a broken loop)
 */
export function ProtectedRoute({ allowedRole }: ProtectedRouteProps) {
  const { isAuthenticated, user } = useAuth()

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  if (allowedRole && user?.role !== allowedRole) {
    return <Navigate to="/not-authorized" replace />
  }

  return <Outlet />
}
