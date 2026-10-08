import { useAuth } from '../store/AuthContext'
import { NotFoundState } from '../components/ui/NotFoundState'

/** Rendered for unknown routes hit by an authenticated user (see App.tsx). */
export function NotFoundPage() {
  const { user } = useAuth()
  const homePath = user?.role === 'teacher' ? '/courses' : '/assessments'

  return (
    <NotFoundState
      title="Page not found"
      message="This page doesn't exist."
      linkTo={homePath}
      linkLabel="Back to home"
    />
  )
}
