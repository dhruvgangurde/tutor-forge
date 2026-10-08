import { Link } from 'react-router-dom'

interface NotFoundStateProps {
  title: string
  message: string
  linkTo: string
  linkLabel: string
}

/**
 * A "this doesn't exist" page body with a way back (frontend audit #7).
 *
 * "Course not found" used to be a bare error banner with no heading and no
 * link: a dead end. Reuses the existing placeholder-page styles, so it looks
 * like the app's other empty states.
 */
export function NotFoundState({ title, message, linkTo, linkLabel }: NotFoundStateProps) {
  return (
    <div className="placeholder-page">
      <div className="placeholder-icon" aria-hidden="true">🧭</div>
      <h1 className="page-title">{title}</h1>
      <p className="placeholder-label">{message}</p>
      <Link to={linkTo} className="btn btn-secondary">
        {linkLabel}
      </Link>
    </div>
  )
}
